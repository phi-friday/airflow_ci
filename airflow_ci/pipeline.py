import asyncio
import importlib
import logging
import re
import subprocess
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from itertools import chain, repeat
from pathlib import Path
from tempfile import gettempdir
from traceback import format_exception
from typing import TYPE_CHECKING, Any, ContextManager, TypeAlias, Union

import httpx
import orjson
import toml
import yaml
from pydantic import BaseModel as _BaseModel
from pydantic import DirectoryPath, Field, root_validator

from airflow_ci.airflow import DagRunState
from airflow_ci.const import (
    PIPELINE_DOCKER_DAG_ID,
    PIPELINE_DOCKER_TASK_ID,
    PIPELINE_DOCKER_XCOM_KEY,
    PIPELINE_FILENAME,
    PIPELINE_SUFFIXES,
    PIPELINE_TEMP_DIR,
)
from airflow_ci.webhook.attrs import BaseWebHook, HookKey, HookType

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from subprocess import CompletedProcess

    from typing_extensions import Self

    from airflow_ci.airflow import HttpHook

__all__ = [
    "Status",
    "Condition",
    "Flag",
    "PipeLineFile",
    "Pipeline",
    "StepResult",
    "Step",
    "StepDagRunConf",
    "PipelineDagRunConf",
    "temp_dir_context",
]

logger = logging.getLogger("airflow.task")
Env: TypeAlias = dict[str, str]
HTTP_TIMEOUT = 10
HTTP_SLEEP = 10


def _orjson_dumps(value: Any, *, default: Any) -> str:
    """orjson.dumps returns bytes, to match standard json.dumps we need to decode

    https://pydantic-docs.helpmanual.io/usage/exporting_models/
    """
    return orjson.dumps(value, default=default).decode()


class BaseModel(_BaseModel):
    """orjson model"""

    class Config:  # noqa: D106
        json_loads = orjson.loads
        json_dumps = _orjson_dumps


class Status(str, Enum):
    """step status"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class Condition(BaseModel):
    """webhook의 종류에 따라 대응하기 위한 조건"""

    event: HookType | list[HookType] | None = Field(default=None)
    branch: str | list[str] | None = Field(default=None)
    user: str | list[str] | None = Field(default=None)

    def check_event(self, hook_key: "HookKey") -> bool:
        """check if hook should be run as event

        Args:
            hook_key: webhook key

        Returns:
            true or false
        """
        if self.event:
            if not isinstance(self.event, list):
                events = {self.event}
            else:
                events = set(self.event)
            return hook_key.event in events
        return False

    def check_branch(self, hook_key: "HookKey") -> bool:
        """check if hook should be run as branch

        Args:
            hook_key: webhook key

        Returns:
            true or false
        """
        if self.branch:
            if hook_key.branch:
                if isinstance(self.branch, str):
                    branches = [self.branch]
                else:
                    branches = self.branch

                return any(
                    bool(re.match(rf"{branch}", hook_key.branch)) for branch in branches
                )
            return False
        return True

    def check_user(self, hook_key: "HookKey") -> bool:
        """check if hook should be run as user

        Args:
            hook_key: webhook key

        Returns:
            true or false
        """
        if self.user:
            if hook_key.user:
                users = [self.user] if isinstance(self.user, str) else self.user
                return any(bool(re.match(rf"{user}", hook_key.user)) for user in users)
            return False
        return True

    def check(self, hook_key: "HookKey") -> bool:
        """check if hook should be run

        Args:
            hook_key: webhook key

        Returns:
            true or false
        """
        return all(
            func(hook_key)
            for func in (self.check_branch, self.check_branch, self.check_user)
        )


class PipeLineFile(BaseModel):
    """pipeline file"""

    steps: list["Step"] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    env: Env = Field(default_factory=dict)
    timeout: int = Field(default=60 * 20)
    temp_dir: DirectoryPath = Field(
        default_factory=lambda: PIPELINE_TEMP_DIR / str(uuid.uuid4()),
    )

    def update_step(self, func: "Callable[[Step], Step]") -> "Self":
        """update step as func

        Args:
            func: step func

        Returns:
            self
        """
        self.steps = [func(step) for step in self.steps]
        return self

    def to_pipeline(self, git_dir: Path) -> "Pipeline":
        """pipeline file to runtime pipeline

        Returns:
            pipeline
        """

        steps = {step.name: step for step in self.steps}
        for step in self.steps:
            for upper in step.upper:
                step.set_upper(upper, callback=True)
            for lower in step.lower:
                step.set_lower(lower, callback=True)
        return Pipeline(
            steps=steps,
            conditions=self.conditions,
            env=self.env,
            timeout=self.timeout,
            temp_dir=self.temp_dir,
            git_dir=git_dir,
        )

    @classmethod
    def load_file(cls, path: Path) -> "Self":
        """load pipeline config file

        Args:
            path: config file dir path

        Raises:
            FileNotFoundError: there is no config file in path

        Returns:
            pipeline file model
        """
        filename = path / PIPELINE_FILENAME
        for suffix, func in zip(
            PIPELINE_SUFFIXES,
            (cls.load_yaml, cls.parse_file, cls.load_toml),
        ):
            file = filename.with_suffix(suffix)
            if not file.is_file():
                continue
            return func(file)

        raise FileNotFoundError("there is no pipeline file, %s", filename)

    @classmethod
    def load_yaml(cls, path: Path) -> "Self":
        """load from yaml

        Args:
            path: yaml file path

        Returns:
            pipline file model
        """
        with path.open("r", encoding="utf-8") as file:
            obj = yaml.safe_load(file)
        return cls.parse_obj(obj)

    @classmethod
    def load_toml(cls, path: Path) -> "Self":
        """load from toml

        Args:
            path: toml file path

        Returns:
            pipeline file model
        """
        obj = toml.load(path)
        return cls.parse_obj(obj)

    def get_temp_dir_context(self) -> ContextManager[Path]:
        """enter temp dir context

        Returns:
            context manager with temp dir path
        """
        return temp_dir_context(self.temp_dir)


class Pipeline(BaseModel):
    """pipeline"""

    steps: dict[str, "Step"]
    conditions: list[Condition]
    env: Env
    timeout: int
    temp_dir: DirectoryPath
    git_dir: DirectoryPath

    def check(self, hook: "BaseWebHook") -> bool:
        """check if pipeline should be run

        Args:
            hook: webhook

        Returns:
            true or false
        """
        if not self.conditions:
            return True
        key = hook.create_key()

        return any(condition.check(key) for condition in self.conditions)

    def update_step(self, func: "Callable[[Step], Step]") -> "Self":
        """update step as func

        Args:
            func: step func

        Returns:
            self
        """
        self.steps = {name: func(step) for name, step in self.steps.items()}
        return self

    async def go(
        self,
        hook: "BaseWebHook",
        airflow_http_hook: "HttpHook",
        git_http_hook: "HttpHook",
        temp_context: Path | None = None,
    ) -> Union[list["StepResult"], None]:
        """go pipeline from hook

        Args:
            hook: webhook
            airflow_http_hook: airflow http hook
            git_http_hook: airflow http hook

        Returns:
            step results or none
        """
        if self.check(hook):
            with ExitStack() as stack:
                if not temp_context:
                    stack.enter_context(self.get_temp_dir_context())
                results = await self.run(airflow_http_hook)
                await self.set_status(hook, git_http_hook, results)
                return results
        return None

    async def set_status(
        self,
        hook: "BaseWebHook",
        git_http_hook: "HttpHook",
        results: list["StepResult"],
    ) -> None:
        """post status

        Args:
            hook: webhook
            git_http_hook: airflow http hook for git server
            results: pipeline step results
        """
        session = git_http_hook.get_conn()

        async with httpx.AsyncClient(
            auth=session.auth,  # type: ignore
            base_url=git_http_hook.base_url,
            headers={key: str(value) for key, value in session.headers.items()},
            timeout=HTTP_TIMEOUT,
            verify=False,  # noqa: S501
        ) as client:
            return await hook.post_status(client=client, results=results)

    def get_temp_dir_context(self) -> ContextManager[Path]:
        """enter temp dir context

        Returns:
            context manager with temp dir path
        """
        return temp_dir_context(self.temp_dir)

    async def run(self, http_hook: "HttpHook") -> list["StepResult"]:
        """run steps in pipeline

        Args:
            http_hook: airflow http hook

        Raises:
            RuntimeError: some highest step has error

        Returns:
            step results
        """
        soon_values = (
            step.go(None, http_hook) for step in self.steps.values() if not step.upper
        )
        future = asyncio.gather(*soon_values)
        maybe_results: list[Union["StepResult", None]] = await future
        results = [x for x in maybe_results if x is not None]
        if len(maybe_results) != len(results):
            raise RuntimeError("some highest step has error")

        return results


class Flag(BaseModel):
    """step status check flag"""

    include: set[Status] = Field(default_factory=set)
    exclude: set[Status] = Field(default_factory=set)

    @root_validator
    def check_is_correct_flag(
        cls,  # noqa: N805
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """check is correct flag"""
        include: set[Status] = values.get("include", set())
        exclude: set[Status] = values.get("exclude", set())

        if intersect := include.intersection(exclude):
            raise ValueError("there is intersection, %s", intersect)

        if Status.PENDING in (union := include.union(exclude)):
            raise ValueError("pending in flags, %s", union)

        return values

    def check(self, step: "Step") -> bool:
        """check step status"""
        status = step.status
        if status == Status.PENDING:
            return False

        if bool(self.include):
            return status in self.include

        if bool(self.exclude):
            return status not in self.exclude

        return True


class StepResult(BaseModel):
    """step result"""

    name: str
    return_code: int
    output: str
    status: Status
    lower_result: dict[str, "StepResult"] = Field(default_factory=dict)

    @classmethod
    def from_process_result(
        cls,
        step: "Step",
        process_result: "CompletedProcess[str]",
    ) -> "Self":
        """create step result from process result

        Args:
            step: step
            process_result: subprocess result

        Returns:
            step result
        """
        return StepResult(
            name=step.name,
            return_code=process_result.returncode,
            output=process_result.stdout,
            status=Status.SUCCESS if process_result.returncode == 0 else Status.FAILURE,
        )


class BaseStep(BaseModel):
    """base step"""

    # attr
    name: str
    image: str
    script: list[str]
    cwd: str | None = Field(default=None)
    env: Env = Field(default_factory=dict)
    timeout: int | None = Field(default=None)

    # connect
    upper: set[str] = Field(default_factory=set)
    lower: set[str] = Field(default_factory=set)

    # status
    status: Status = Field(default=Status.PENDING)
    status_flags: dict[str, Flag] = Field(default_factory=dict)


class Step(BaseStep):
    """
    upper와 lower로 정의된 연결 관계가 있다

    A의 upper에 B가 존재하면, B의 lower에도 A가 존재한다.
    B의 실행이 끝나면 A를 호출하고,
    B의 lower에서 A를 제거하고, A의 upper에서 B를 제거한다.

    B가 A를 호출했을 때, A의 status_flag를 확인한다.
    A의 status_flags에 B의 이름이 있는지 확인한다

    B의 이름이 A의 status_flags에 있다면,
    A의 stutus_flags에서 B에 해당하는 Flag를 사용하여(check 메소드)
    지정된 status로 호출되었는지 확인하고,
    지정된 status가 맞다면 A의 status를 유지하고,
    지정된 status가 아니라면 A의 status를 실패로 변경한다.

    status_flags 확인이 끝나고,
    A의 upper가 비어있고,
    A의 status가 pending이라면,
    A를 실행한다.
    """

    pipeline: Pipeline
    result: StepResult | None = Field(default=None)

    def set_upper(self, step: Union["Step", str], *, callback: bool) -> "Self":
        """set upper

        Args:
            step: upper step
            callback: call "set_lower" from upper step

        Returns:
            self
        """
        name = step if isinstance(step, str) else step.name
        self.upper.add(name)
        if callback:
            target = self.pipeline.steps[name]
            target.set_lower(self, callback=False)
        return self

    def del_upper(self, step: Union["Step", str], *, callback: bool) -> "Self":
        """del upper

        Args:
            step: upper step
            callback: call "del_lower" from upper step

        Returns:
            self
        """
        name = step if isinstance(step, str) else step.name
        self.upper.discard(name)
        if callback:
            target = self.pipeline.steps[name]
            target.del_lower(self, callback=False)
        return self

    def set_lower(self, step: Union["Step", str], *, callback: bool) -> "Self":
        """set lower

        Args:
            step: lower step
            callback: call "set_upper" from lower step

        Returns:
            self
        """
        name = step if isinstance(step, str) else step.name
        self.lower.add(name)
        if callback:
            target = self.pipeline.steps[name]
            target.set_upper(self, callback=False)
        return self

    def del_lower(self, step: Union["Step", str], *, callback: bool) -> "Self":
        """del lower

        Args:
            step: lower step
            callback: call "del_upper" from lower step

        Returns:
            self
        """
        name = step if isinstance(step, str) else step.name
        self.lower.discard(name)
        if callback:
            target = self.pipeline.steps[name]
            target.del_upper(self, callback=False)
        return self

    def update_cwd(self, path: Path | str) -> "Self":
        """update cwd path

        Args:
            path: base path

        Returns:
            self
        """
        if isinstance(path, str):
            path = Path(path)

        if self.cwd is None:
            self.cwd = path.as_posix()
        else:
            self.cwd = (path / self.cwd).resolve().as_posix()
        return self

    async def go(
        self,
        upper: Union["Step", None],
        http_hook: "HttpHook",
    ) -> StepResult | None:
        """check -> run -> call_next

        Args:
            upper: upper step or None(first)
            http_hook: airflow http hook

        Raises:
            RuntimeError: before running, status is not pending
            RuntimeError: after running, status is not failure or success

        Returns:
            step result or None
        """
        if upper is not None:
            self.del_upper(upper, callback=True)
            if upper.name in self.status_flags:
                flag = self.status_flags[upper.name]
                if not flag.check(upper):
                    self.status = Status.FAILURE
                    if self.upper:
                        return None

                    self.result = StepResult(
                        name=self.name,
                        return_code=-1,
                        output="",
                        status=Status.FAILURE,
                    )
                    logger.error("step %s's upper failed the test", self.name)
                    return await self.call_next(http_hook)

            if self.upper:
                return None

        if self.status != Status.PENDING:
            raise RuntimeError("step status is not pending, %s", self.status)

        self.result = await self.run(http_hook)
        if self.status == Status.FAILURE:
            logger.error(self.result.output)
        elif self.status == Status.SUCCESS:
            logger.info(self.result.output)
        else:
            raise RuntimeError("step status is not failure or success, %s", self.status)

        return await self.call_next(http_hook)

    async def run(self, http_hook: "HttpHook") -> StepResult:
        """run in docker

        Args:
            http_hook: airflow http hook

        Returns:
            step result
        """
        self.status = Status.RUNNING
        try:
            result = await self.run_http(http_hook)
        except Exception as exc:  # noqa: BLE001
            self.status = Status.FAILURE
            return StepResult(
                name=self.name,
                return_code=-1,
                output="".join(format_exception(type(exc), exc)),
                status=self.status,
            )
        else:
            self.status = result.status
            return result

    def run_process(self) -> "CompletedProcess[str]":
        """run script

        Returns:
            subprocess result
        """
        temp_script = (Path(gettempdir()) / str(uuid.uuid4())).with_suffix(".sh")
        temp_script.touch(mode=0o777, exist_ok=False)
        with temp_script.open("w+") as file:
            file.writelines(
                chain.from_iterable(
                    zip(
                        chain(["#!/usr/bin/env bash", "", "set -ex", ""], self.script),
                        repeat("\n"),
                    ),
                ),
            )

        return subprocess.run(
            [temp_script.as_posix()],
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=self.pipeline.env | self.env,
            timeout=self.timeout or self.pipeline.timeout,
            text=True,
        )

    async def call_next(self, http_hook: "HttpHook") -> StepResult:
        """call lower step

        Args:
            http_hook: airflow http hook

        Raises:
            RuntimeError: self result is None

        Returns:
            step result
        """
        if self.result is None:
            raise RuntimeError("step %s result is None", self.name)
        if not self.lower:
            return self.result

        lower = list(self.lower)
        soon_values = (
            self.pipeline.steps[name].go(
                self,
                http_hook=http_hook,
            )
            for name in lower
        )
        future = asyncio.gather(*soon_values)
        maybe_results: list[Union["StepResult", None]] = await future

        self.result.lower_result = {
            name: value
            for name, value in zip(lower, maybe_results)
            if value is not None
        }

        return self.result

    async def run_http(self, http_hook: "HttpHook") -> StepResult:
        """run step in airflow docker dag as http api

        Args:
            http_hook: airflow http hook

        Raises:
            AirflowFailException: docker dag failed
            AirflowException: docker dag timeout

        Returns:
            step result
        """
        dag_run_id = (
            PIPELINE_DOCKER_DAG_ID
            + "_"
            + self.name
            + "_"
            + datetime.now(tz=timezone(timedelta())).isoformat()
        )
        session = http_hook.get_conn()
        timeout = self.timeout or self.pipeline.timeout

        conf_data = StepDagRunConf(
            step=BaseStep.parse_obj(
                self.copy(
                    update={
                        "env": self.pipeline.env | self.env,
                        "timeout": timeout,
                    },
                ),
            ),
            temp_dir=self.pipeline.temp_dir,
            git_dir=self.pipeline.git_dir,
        )

        async with httpx.AsyncClient(
            auth=session.auth,  # type: ignore
            base_url=http_hook.base_url,
            headers={key: str(value) for key, value in session.headers.items()},
            timeout=HTTP_TIMEOUT,
            verify=False,  # noqa: S501
        ) as client:
            new_dagrun = await client.post(
                url=f"/dags/{PIPELINE_DOCKER_DAG_ID}/dagRuns",
                json={
                    "conf": conf_data.dict(),
                    "dag_run_id": dag_run_id,
                },
            )
            new_dagrun.raise_for_status()

            time = 0
            timeout += HTTP_SLEEP
            while time < timeout:
                await asyncio.sleep(HTTP_SLEEP)
                dag_run_status = await client.get(
                    url=f"/dags/{PIPELINE_DOCKER_DAG_ID}/dagRuns/{dag_run_id}",
                )
                dag_run_status.raise_for_status()
                status: dict[str, Any] = dag_run_status.json()
                state: str = status["state"]
                if state == DagRunState.SUCCESS:
                    break
                if state in DagRunState.FAILED:
                    raise RuntimeError("pipe step dag failed, %s", dag_run_id)
                logger.info("wait for %s success", dag_run_id)
                time += HTTP_SLEEP
            else:
                raise TimeoutError("pipe step dag timeout, %s", dag_run_id)

            xcom = await client.get(
                url="dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
                "/{task_id}/xcomEntries/{xcom_key}".format(
                    dag_id=PIPELINE_DOCKER_DAG_ID,
                    dag_run_id=dag_run_id,
                    task_id=PIPELINE_DOCKER_TASK_ID,
                    xcom_key=PIPELINE_DOCKER_XCOM_KEY,
                ),
                params={"deserialize": False},
            )
            xcom.raise_for_status()
            values: dict[str, Any] = xcom.json()
            result = values["value"]
            return StepResult.parse_raw(result)

    def __hash__(self) -> int:  # noqa: D105
        return hash(self.name)


class StepDagRunConf(BaseModel):
    """airflow step dagrun conf"""

    step: BaseStep
    temp_dir: DirectoryPath
    git_dir: DirectoryPath


class PipelineDagRunConf(BaseModel):
    """airflow pipeline dagrun conf"""

    temp_dir: DirectoryPath

    webhook: dict[str, Any]
    hook_module: str | None = Field(default=None)
    hook_type: str

    airflow_http_conn_id: str
    git_http_conn_id: str

    def get_hook(self) -> "BaseWebHook":
        """parse webhook object

        Returns:
            webhook
        """
        webhook_model: type[BaseWebHook]
        if self.hook_module:
            module = importlib.import_module(self.hook_module)
            webhook_model = getattr(module, self.hook_type)
        else:
            module_elements = [*BaseWebHook.__module__.split(".")[:-1], self.hook_type]
            if len(module_elements) < 2:  # noqa: PLR2004
                modulename = module_elements[0]
            else:
                modulename = ".".join(module_elements)
            module = importlib.import_module(modulename)
            webhook_model = module.WebHook
        return webhook_model.parse_webhook(self.webhook)


@contextmanager
def temp_dir_context(path: Path) -> "Generator[Path, None, None]":
    """get temp dir

    when exit, rmdir

    Args:
        path: dir path

    Yields:
        dir path
    """
    path.mkdir(parents=False, exist_ok=False)
    yield path
    _rmdir(path)


def _rmdir(path: Path) -> None:
    for item in path.iterdir():
        if item.is_dir():
            _rmdir(item)
        else:
            item.unlink(missing_ok=False)
    path.rmdir()
