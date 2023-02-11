from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, type_check_only

if TYPE_CHECKING:
    from requests import Session

__all__ = ["DagRunState", "HttpHook"]


class DagRunState(str, Enum):
    """airflow dagrun state"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@type_check_only
class HttpHook(Protocol):
    """airflow http provider hook"""

    base_url: str

    def get_conn(  # noqa: D102
        self,
        headers: dict[Any, Any] | None = None,
    ) -> "Session":
        ...
