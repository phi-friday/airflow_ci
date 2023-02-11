from enum import Enum
from typing import TYPE_CHECKING, Any

from requests import Session

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

__all__ = [
    "DagRunState",
    "HttpHook",
    "DagRun",
    "TaskInstance",
    "XComArg",
    "Context",
    "task",
]

class DagRunState(str, Enum):
    """airflow dagrun state"""

    QUEUED: str
    RUNNING: str
    SUCCESS: str
    FAILED: str

class HttpHook:
    """airflow http provider hook"""

    base_url: str

    def __init__(
        self,
        method: str = ...,
        http_conn_id: str = ...,
        auth_type: Any = ...,
        tcp_keep_alive: bool = ...,
        tcp_keep_alive_idle: int = ...,
        tcp_keep_alive_count: int = ...,
        tcp_keep_alive_interval: int = ...,
    ) -> None: ...
    def get_conn(  # noqa: D102
        self,
        headers: dict[Any, Any] | None = None,
    ) -> Session: ...

class DagRun:
    """airflow dagrun"""

    conf: dict[str, Any]

class TaskInstance:
    """airflow task instance"""

    dag_run: DagRun

    def xcom_push(  # noqa: D102
        self,
        key: str,
        value: Any,
        execution_date: datetime | None = ...,
        session: Session = ...,
    ) -> None: ...

class XComArg:
    """airflow xcom arg"""

class Context(dict):
    """airflow context"""

class Task:  # noqa: D101
    __call__: Callable[..., XComArg]
    def override(self, **kwargs: Any) -> Task: ...  # noqa: D102

class TaskDecorator:  # noqa: D101
    def __call__(self, python_callable: Callable) -> Task: ...  # noqa: D102
    def override(self, **kwargs: Any) -> Callable[..., Task]: ...  # noqa: D102

class TaskCollection:
    """airflow task decorator"""

    def __getattr__(self, name: str) -> TaskDecorator: ...  # noqa: D105

task: TaskCollection
