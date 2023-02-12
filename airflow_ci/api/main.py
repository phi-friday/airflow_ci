from datetime import datetime, timedelta, timezone
from traceback import format_exception_only
from typing import TYPE_CHECKING, Any

import httpx
import orjson
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.logger import logger
from fastapi.responses import ORJSONResponse

from airflow_ci.api.config import settings
from airflow_ci.const import PIPELINE_DAG_ID
from airflow_ci.pipeline import PipelineDagRunConf
from airflow_ci.webhook.gitea import WebHook

if TYPE_CHECKING:
    from airflow_ci.webhook.attrs import WebhookApiData

__all__ = ["app"]

app = FastAPI(default_response_class=ORJSONResponse)


@app.post("/log")
async def log_webhook(request: Request) -> Any:
    """body to log"""

    body = await request.body()
    webhook_data = orjson.loads(body)
    header = dict(request.headers.mutablecopy())
    data: "WebhookApiData" = {"webhook": webhook_data, "header": header}

    logger.info("%s", orjson.dumps(data, option=orjson.OPT_INDENT_2).decode())
    return data


@app.post("/gitea_log")
async def log_gitea_webhook(request: Request) -> Any:
    """gitea body to log"""

    body = await request.body()
    webhook_data = orjson.loads(body)
    header = dict(request.headers.mutablecopy())
    data: "WebhookApiData" = {"webhook": webhook_data, "header": header}

    gitea_data = WebHook.parse_webhook(data)

    logger.info(
        "%s",
        orjson.dumps(gitea_data.dict(), option=orjson.OPT_INDENT_2).decode(),
    )
    return data


@app.post("/hook")
async def transport_webhook(request: Request) -> Any:
    """transport webhook data to airflow"""

    body = await request.body()
    webhook_data = orjson.loads(body)
    header = dict(request.headers.mutablecopy())
    data = {"webhook": webhook_data, "header": header}

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
