import asyncio
import logging
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Union, cast

from git.repo import Repo

from airflow_ci.airflow import HttpHook, task
from airflow_ci.const import (
    PIPELINE_DOCKER_CONTAINER_NAME,
    PIPELINE_DOCKER_TASK_ID,
    PIPELINE_DOCKER_XCOM_KEY,
    PIPELINE_TASK_ID,
    PIPELINE_TEMP_DIR,
)
from airflow_ci.pipeline import (
    Pipeline,
    PipelineDagRunConf,
    PipeLineFile,
    Step,
    StepDagRunConf,
    StepResult,
    temp_dir_context,
)

if TYPE_CHECKING:
    from airflow_ci.airflow import Context, DagRun, TaskInstance, XComArg


__all__ = ["go_step", "create_docker_step_task"]

logger = logging.getLogger("airflow.task")


@task.docker
def go_step(**context: Any) -> None:
    """go step from dag_run.conf.step

    Raises:
        AirflowFailException: there is no task instance
    """
    cont = cast("Context", context)
    task_instance = cast(Union["TaskInstance", None], cont.get("ti", None))

    if task_instance is None:
        raise RuntimeError("there is no task_instance")
    dagrun = task_instance.dag_run
    conf = StepDagRunConf.parse_obj(dagrun.conf)

    dummy_pipeline = Pipeline(
        steps={},
        conditions=[],
        env={},
        timeout=0,
        temp_dir=conf.temp_dir,
        git_dir=conf.git_dir,
    )

    step = Step.parse_obj({**conf.step.dict(), "pipeline": dummy_pipeline})

    process_result = step.run_process()
    result = StepResult.from_process_result(step, process_result).json()
    task_instance.xcom_push(PIPELINE_DOCKER_XCOM_KEY, result)


@task.python
def go_pipeline(**context: Any) -> None:
    """go pipeline from dag_run.conf.webhook

    Raises:
        AirflowException: there is no task_instance
        AirflowFailException: there is no http_conn_id in dag_run.conf
    """
    cont = cast("Context", context)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            lambda: asyncio.run(
                go_pipeline_async(context=cont),
            ),
        )
        wait([future], return_when=ALL_COMPLETED)
    future.result()


async def go_pipeline_async(context: "Context") -> None:
    """go pipeline as async

    Args:
        context: airflow context
    """
    task_instance = context.get("ti", None)

    if task_instance is None:
        raise RuntimeError("there is no task_instance")
    dagrun: "DagRun" = task_instance.dag_run  # type: ignore

    conf = PipelineDagRunConf.parse_obj(dagrun.conf)
    hook = conf.get_hook()
    airflow_http_hook = HttpHook(conf.airflow_http_conn_id)
    git_http_hook = HttpHook(conf.git_http_conn_id)

    with temp_dir_context(conf.temp_dir) as path:
        git_dir = path / hook.repo.name.replace("/", "_")
        repo = Repo.clone_from(
            hook.repo.clone_url,
            git_dir,
            allow_unsafe_protocols=True,
            allow_unsafe_options=True,
        )
        commit = repo.commit(hook.commit.key)
        repo.git.checkout(commit.name_rev.split()[0])

        pipeline = PipeLineFile.load_file(git_dir).to_pipeline(git_dir)

        results = await pipeline.go(
            hook,
            airflow_http_hook=airflow_http_hook,
            git_http_hook=git_http_hook,
            temp_context=path,
        )

        if results is None:
            logger.info("pipeline result is None")
            return

        for result in results:
            logger.info(
                dedent(
                    """===
                step_name=%s
                ===
                output:
                %s
                ===
                """,
                ),
                result.name,
                result.output,
            )
        return


def create_docker_step_task(**kwargs: Any) -> "XComArg":
    """create run step in docker task

    Returns:
        step task
    """
    return go_step.override(
        task_id=PIPELINE_DOCKER_TASK_ID,
        image="{{ dag_run.conf.step.image }}",
        container_name=PIPELINE_DOCKER_CONTAINER_NAME + "_{{ dag_run.conf.step.name }}",
        do_xcom_push=False,
        mount_tmp_dir=True,
        tmp_dir=PIPELINE_TEMP_DIR,
        **kwargs,
    )()


def create_docker_pipeline_task(**kwargs: Any) -> "XComArg":
    """create run pipeline in docker task

    Returns:
        pipeline task
    """
    return go_pipeline.override(
        task_id=PIPELINE_TASK_ID,
        do_xcom_push=False,
        **kwargs,
    )()
