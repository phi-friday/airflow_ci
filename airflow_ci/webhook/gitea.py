from typing import TYPE_CHECKING, Any

import httpx

from airflow_ci.webhook.attrs import (
    BaseCommit,
    BaseCommitKey,
    BasePullRequest,
    BaseRepository,
    BaseUser,
    BaseWebHook,
    HookType,
    WebhookApiData,
)

if TYPE_CHECKING:
    from typing_extensions import Self


class CommitKey(BaseCommitKey):
    """gitea commit key"""

    def to_commit(  # noqa: D102
        self,
        webhook: "WebhookApiData",
    ) -> "Commit":
        repositoy = webhook["webhook"]["repository"]
        base_url = _parse_base_url(webhook["webhook"])

        with httpx.Client(
            base_url=base_url,
            verify=False,  # noqa: S501
        ) as client:
            response = client.get(
                url="api/v1/repos/{owner}/{repo}/commits".format(
                    owner=repositoy["owner"]["username"],
                    repo=repositoy["name"],
                ),
                params={"sha": self.key},
            )
            response.raise_for_status()
            result = response.json()[0]

        return Commit(
            key=self.key,
            message=result["commit"]["message"],
            url=result["url"],
            author=BaseUser(
                email=result["commit"]["author"]["email"],
                username=result["commit"]["author"]["name"],
            ),
            commiter=BaseUser(
                email=result["commit"]["committer"]["email"],
                username=result["commit"]["committer"]["name"],
            ),
            ref=None,
            branch=None,
        )


class Commit(CommitKey, BaseCommit):
    """gitea commit"""


class PullRequest(BasePullRequest):
    """gitea pr"""

    base: CommitKey
    head: CommitKey


class WebHook(BaseWebHook):
    """gitea webhook"""

    @classmethod
    def parse_type(cls, webhook: WebhookApiData) -> HookType:
        """parse hook type from header

        Args:
            webhook: webhook data

        Returns:
            hook type
        """
        header = webhook["header"]
        data = webhook["webhook"]
        if header["x-gitea-event"] == "create":
            if data["ref_type"] == "branch":
                return HookType.BRANCH
            if data["ref_type"] == "tag":
                return HookType.TAG
        if header["x-gitea-event"] == "push":
            ref: str = data["ref"]
            if ref.startswith("refs/heads"):
                return HookType.PUSH
        if header["x-gitea-event"] == "pull_request":
            return HookType.PULL_REQEUST
        raise NotImplementedError()

    @classmethod
    def parse_pull_request(
        cls,
        webhook: WebhookApiData,
        *,
        hook_type: HookType,
    ) -> PullRequest:
        """parse pull request data from webhook data

        Args:
            webhook: webhook data
            hook_type: hook type

        Raises:
            NotImplementedError: non pull request

        Returns:
            pull request model
        """
        if hook_type != HookType.PULL_REQEUST:
            raise NotImplementedError()

        pr_data = webhook["webhook"]["pull_request"]
        return PullRequest(
            id=pr_data["id"],
            user=BaseUser(
                email=pr_data["user"]["email"],
                username=pr_data["user"]["username"],
            ),
            html_url=pr_data["html_url"],
            diff_url=pr_data["diff_url"],
            base=CommitKey(
                key=pr_data["base"]["sha"],
            ),
            head=CommitKey(
                key=pr_data["head"]["sha"],
            ),
        )

    @classmethod
    def parse_commit(
        cls,
        webhook: WebhookApiData,
        *,
        hook_type: HookType,
    ) -> Commit:
        """parse commit data from webhook body

        Args:
            webhook: webhook data
            hook_type: hook type

        Raises:
            NotImplementedError: _description_

        Returns:
            commit model
        """

        if hook_type == HookType.PULL_REQEUST:
            raise NotImplementedError()

        data = webhook["webhook"]
        if hook_type in {HookType.BRANCH, HookType.PUSH}:
            commit_data = data["head_commit"]
            return Commit(
                key=commit_data["id"],
                message=commit_data["message"],
                url=commit_data["url"],
                author=BaseUser(
                    email=commit_data["author"]["email"],
                    username=commit_data["author"]["username"],
                ),
                commiter=BaseUser(
                    email=commit_data["committer"]["email"],
                    username=commit_data["committer"]["username"],
                ),
                ref=data["ref"],
                branch=_parse_branch(data, hook_type=hook_type),
            )
        if hook_type == HookType.TAG:
            commit_key = CommitKey(key=data["sha"])
            return commit_key.to_commit(webhook=webhook)

        raise NotImplementedError()

    @classmethod
    def parse_repository(
        cls,
        webhook: WebhookApiData,
        *,
        hook_type: HookType,
    ) -> BaseRepository:
        """parse repository data from webhook data

        Args:
            webhook: webhook data
            hook_type: hook type

        Raises:
            NotImplementedError: _description_

        Returns:
            repository model
        """

        repo_data = webhook["webhook"]["repository"]
        if hook_type in {
            hook_type.BRANCH,
            HookType.TAG,
            HookType.PUSH,
            HookType.PULL_REQEUST,
        }:
            return BaseRepository(
                name=repo_data["name"],
                full_name=repo_data["full_name"],
                private=repo_data["private"],
                fork=repo_data["fork"],
                owner=BaseUser(
                    email=repo_data["owner"]["email"],
                    username=repo_data["owner"]["username"],
                ),
                html_url=repo_data["html_url"],
                ssh_url=repo_data["ssh_url"],
                clone_url=repo_data["clone_url"],
                default_branch=repo_data["default_branch"],
            )
        raise NotImplementedError()

    @classmethod
    def parse_webhook(  # noqa: D102
        cls,
        webhook: WebhookApiData,
    ) -> "Self":
        hook_type = cls.parse_type(webhook)

        if hook_type == HookType.PULL_REQEUST:
            pull_request = cls.parse_pull_request(webhook, hook_type=hook_type)
            commit = pull_request.head.to_commit(webhook)
        else:
            pull_request = None
            commit = cls.parse_commit(webhook, hook_type=hook_type)
        repository = cls.parse_repository(webhook, hook_type=hook_type)

        return cls(
            hook_type=hook_type,
            repo=repository,
            commit=commit,
            pull_request=pull_request,
        )


def _parse_branch(data: dict[str, Any], *, hook_type: HookType) -> str | None:
    if hook_type in {HookType.BRANCH, HookType.TAG}:
        if "ref_type" in data and data["ref_type"] == "branch":
            return data["ref"]
        return None
    if hook_type == HookType.PUSH:
        return str(data["ref"]).removeprefix("refs/heads/")
    if hook_type == HookType.PULL_REQEUST:
        return None

    raise NotImplementedError()


def _parse_base_url(data: dict[str, Any]) -> str:
    repo = data["repository"]
    url: str = repo["clone_url"]
    return url.removesuffix(".git").removesuffix(f"/{repo['full_name']}")
