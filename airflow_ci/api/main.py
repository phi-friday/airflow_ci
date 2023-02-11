from datetime import datetime, timedelta, timezone
from pprint import pformat
from traceback import format_exception_only
from typing import Any

import httpx
import orjson
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.logger import logger
from fastapi.responses import ORJSONResponse

from airflow_ci.api.config import settings
from airflow_ci.const import PIPELINE_DAG_ID
from airflow_ci.pipeline import PipelineDagRunConf

__all__ = ["app"]

app = FastAPI(default_response_class=ORJSONResponse)


@app.post("/log")
async def log_webhook(request: Request) -> Any:
    """body to log"""

    body = await request.body()
    data = orjson.loads(body)

    logger.info("%s", pformat(data))
    return data


@app.post("/hook")
async def transport_webhook(request: Request) -> Any:
    """transport webhook data to airflow"""

    body = await request.body()
    data = orjson.loads(body)

    conf = PipelineDagRunConf(
        temp_dir=settings.temp_dir,
        webhook=data,
        hook_module=settings.hook_module,
        hook_type=settings.hook_type,
        airflow_http_conn_id=settings.airflow_http_conn_id,
        git_http_conn_id=settings.git_http_conn_id,
    )
    async with httpx.AsyncClient(
        base_url=settings.airflow_base_url,
        auth=(settings.airflow_username, settings.airflow_password.get_secret_value()),
        verify=False,  # noqa: S501
    ) as client:
        response = await client.post(
            url=f"/dags/{PIPELINE_DAG_ID}/dagRuns",
            json={
                "conf": conf.dict(),
                "dag_run_id": PIPELINE_DAG_ID
                + "_"
                + datetime.now(timezone(timedelta())).isoformat(),
            },
        )

        try:
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=format_exception_only(type(exc), exc),
            ) from exc

        return response.json()
