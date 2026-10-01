import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api import webhook
from app.core import config
from app.core.resources import close_resources, get_db, get_mysql_pool, init_resources
from app.services.session import session_watcher
from app.services.summaries import summary_watcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _check_mongo() -> None:
    await get_db().command("ping")


async def _check_mysql() -> None:
    async with get_mysql_pool().acquire() as conn:
        await conn.ping()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_resources()
        await _check_mongo()
        await _check_mysql()
    except Exception as e:
        await close_resources()
        raise RuntimeError(
            f"Falha ao inicializar recursos (Mongo {config.MONGO_URI}, "
            f"MySQL {config.MYSQL_HOST}:{config.MYSQL_PORT}): {e}"
        ) from e

    logger.info("Recursos inicializados (Mongo, MySQL, HTTP, Chroma), subindo API.")
    watcher = asyncio.create_task(session_watcher())
    summarizer = asyncio.create_task(summary_watcher())
    yield
    watcher.cancel()
    summarizer.cancel()
    await close_resources()
    logger.info("Recursos fechados.")


app = FastAPI(title=f"{config.COMPANY_NAME} Bot", lifespan=lifespan)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    checks = {"mongo": "ok", "mysql": "ok"}

    try:
        await _check_mongo()
    except Exception as e:
        checks["mongo"] = f"erro: {e}"

    try:
        await _check_mysql()
    except Exception as e:
        checks["mysql"] = f"erro: {e}"

    healthy = all(v == "ok" for v in checks.values())
    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if healthy else "unhealthy", "checks": checks},
    )
