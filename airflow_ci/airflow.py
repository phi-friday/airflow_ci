from enum import Enum

try:
    from airflow.decorators import task  # type: ignore
    from airflow.models.dagrun import DagRun  # type: ignore
    from airflow.models.taskinstance import TaskInstance  # type: ignore
    from airflow.models.xcom_arg import XComArg  # type: ignore
    from airflow.providers.http.hooks.http import HttpHook  # type: ignore
    from airflow.utils.context import Context  # type: ignore
except (ModuleNotFoundError, ImportError):
    task = None
    DagRun = None
    TaskInstance = None
    XcomArg = None
    HttpHook = None
    Context = None


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

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
