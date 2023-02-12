from enum import Enum
from typing import TYPE_CHECKING, Any, final

from pydantic import AnyHttpUrl, AnyUrl, BaseModel, EmailStr, Field

if TYPE_CHECKING:
    from collections.abc import Iterable

    from httpx import AsyncClient
    from typing_extensions import Self

    from airflow_ci.pipeline import StepResult

__all__ = ["HookType", "BaseUser", "BaseRepository", "BaseCommit", "HookKey"]


class HookType(str, Enum):
    """git hook type"""

    PUSH = "push"
    PULL_REQEUST = "pull_request"
    BRANCH = "branch"
    TAG = "tag"
    RELEASE = "release"


class BaseUser(BaseModel):
    """git user"""

    email: EmailStr
    username: str


class BaseRepository(BaseModel):
    """git repository"""

    name: str
    full_name: str
    private: bool
    fork: bool
    owner: BaseUser
    html_url: AnyHttpUrl
    ssh_url: AnyUrl
    clone_url: AnyHttpUrl
    default_branch: str


class BaseCommit(BaseModel):
    """git commit"""

    key: str
    message: str
    url: AnyHttpUrl
    author: BaseUser
    commiter: BaseUser


class BaseWebHook(BaseModel):
    """webhook"""

    hook_type: HookType
    repo: BaseRepository
    commit: BaseCommit

    @classmethod
    def parse_webhook(cls, webhook: Any) -> "Self":
        """parse webhook data

        Args:
            webhook: webhook data

        Returns:
            webhook model
        """
        raise NotImplementedError()

    def create_key(self) -> "HookKey":
        """create unique key to classify"""
        raise NotImplementedError()

    async def post_status(
        self,
        *,
        client: "AsyncClient",
        results: "Iterable[StepResult]",
    ) -> None:
        """post commit status

        Args:
            client: async httpx client
            results: pipeline step results
        """
        raise NotImplementedError()


@final
class HookKey(BaseModel):
    """for classify webhook"""

    event: HookType
    branch: str | None = Field(default=None)
    user: str | None = Field(default=None)
