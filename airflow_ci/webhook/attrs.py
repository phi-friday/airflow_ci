from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict, final

from pydantic import AnyHttpUrl, BaseModel, EmailStr, Field

if TYPE_CHECKING:
    from collections.abc import Iterable

    from httpx import AsyncClient
    from typing_extensions import Self

    from airflow_ci.pipeline import StepResult

__all__ = [
    "HookType",
    "BaseUser",
    "BaseRepository",
    "BaseCommitKey",
    "BaseCommit",
    "BasePullRequest",
    "HookKey",
    "WebhookApiData",
]


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
    ssh_url: str
    clone_url: AnyHttpUrl
    default_branch: str


class BaseCommitKey(BaseModel):
    """git commit with only key"""

    key: str

    def to_commit(
        self,
        webhook: "WebhookApiData",
    ) -> "BaseCommit":
        """get commit data from key

        Args:
            webhook: webhook body and header

        Raises:
            NotImplementedError: not impl.

        Returns:
            commit model
        """

        raise NotImplementedError()


class BaseCommit(BaseCommitKey):
    """git commit"""

    message: str
    url: AnyHttpUrl
    author: BaseUser
    commiter: BaseUser
    ref: str | None
    branch: str | None

    def to_commit(  # noqa: D102
        self,
        webhook: "WebhookApiData",  # noqa: ARG002
    ) -> "BaseCommit":
        return self


class BasePullRequest(BaseModel):
    """git pr"""

    id: int  # noqa: A003
    user: BaseUser
    html_url: AnyHttpUrl
    diff_url: AnyHttpUrl
    base: BaseCommitKey
    head: BaseCommitKey


class BaseWebHook(BaseModel):
    """webhook"""

    hook_type: HookType
    repo: BaseRepository
    commit: BaseCommit
    pull_request: BasePullRequest | None = Field(default=None)

    @classmethod
    def parse_webhook(cls, webhook: "WebhookApiData") -> "Self":
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


class WebhookApiData(TypedDict, total=True):
    """api server response data as json"""

    webhook: dict[str, Any]
    header: dict[str, Any]
