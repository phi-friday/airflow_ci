from pathlib import Path
from tempfile import gettempdir
from typing import Final

PIPELINE_FILENAME: Final[str] = ".pipeline"
PIPELINE_SUFFIXES: Final[list[str]] = ["yaml", ".json", "toml"]

PIPELINE_DAG_ID: Final[str] = "pipeline_dag"
PIPELINE_TASK_ID: Final[str] = "pipeline_task"
PIPELINE_DOCKER_DAG_ID: Final[str] = "pipeline_in_docker"
PIPELINE_DOCKER_TASK_ID: Final[str] = "pipeline_step_in_dokcer"
PIPELINE_DOCKER_CONTAINER_NAME: Final[str] = "pipeline_step"
PIPELINE_DOCKER_XCOM_KEY: Final[str] = "pipeline_step_result_in_docker"
PIPELINE_TEMP_DIR: Final[Path] = Path(gettempdir()) / "pipeline"
