from pydantic import AnyHttpUrl, BaseSettings, DirectoryPath, Field, SecretStr

from airflow_ci.const import PIPELINE_TEMP_DIR

__all__ = ["settings"]


class ApiSettings(BaseSettings):
    """api settings"""

    airflow_base_url: AnyHttpUrl = Field(default=..., env="PIPELINE_AIRFLOW_BASE_URL")
    airflow_username: str = Field(default=..., env="PIPELINE_AIRFLOW_USER_USERNAME")
    airflow_password: SecretStr = Field(
        default=...,
        env="PIPELINE_AIRFLOW_USER_PASSWORD",
    )
    airflow_http_conn_id: str = Field(default=..., env="PIPELINE_AIRFLOW_HTTP_CONN_ID")
    git_http_conn_id: str = Field(default=..., env="PIPELINE_AIRFLOW_GIT_HTTP_CONN_ID")
    temp_dir: DirectoryPath = Field(default=PIPELINE_TEMP_DIR)
    hook_module: str | None = Field(default=None, env="PIPELINE_HOOK_MODULE")
    hook_type: str = Field(default="gitea", env="PIPELINE_HOOK_TYPE")


settings = ApiSettings()
