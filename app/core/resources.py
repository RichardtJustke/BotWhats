"""Infraestrutura da aplicação: nasce uma vez no startup (lifespan do main) e
é fechada no shutdown. As funções de serviço só pegam o recurso pronto pelos
getters abaixo, nunca criam conexão/cliente próprio."""

import logging
from pathlib import Path

import aiomysql
import chromadb
import httpx
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core import config

logger = logging.getLogger(__name__)

# Mesmo caminho absoluto usado pelo scripts/ingest.py, independente do cwd
CHROMA_DIR = Path(__file__).resolve().parents[2] / "chroma_data"

_mongo_client: AsyncIOMotorClient | None = None
_mysql_pool: aiomysql.Pool | None = None
_http_client: httpx.AsyncClient | None = None
_knowledge_collection = None


async def init_resources() -> None:
    global _mongo_client, _mysql_pool, _http_client, _knowledge_collection

    _mongo_client = AsyncIOMotorClient(config.MONGO_URI)
    await _mongo_client[config.MONGO_DB_NAME].command("ping")
    await _ensure_mongo_indexes()

    _mysql_pool = await aiomysql.create_pool(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        db=config.MYSQL_DB,
        minsize=config.MYSQL_POOL_MIN,
        maxsize=config.MYSQL_POOL_MAX,
        autocommit=True,  # só leitura; sem isso a conexão reaproveitada enxerga snapshot velho
        pool_recycle=3600,  # descarta conexões antes do wait_timeout do MySQL derrubá-las
    )

    # Um único cliente HTTP (e pool de conexões keep-alive) pra OpenWA e OpenRouter.
    # Chamadas mais lentas, como o LLM, passam timeout próprio por requisição.
    _http_client = httpx.AsyncClient(timeout=10.0)

    _knowledge_collection = chromadb.PersistentClient(path=str(CHROMA_DIR)).get_or_create_collection(
        "knowledge_base"
    )


async def close_resources() -> None:
    if _http_client is not None:
        await _http_client.aclose()
    if _mysql_pool is not None:
        _mysql_pool.close()
        await _mysql_pool.wait_closed()
    if _mongo_client is not None:
        _mongo_client.close()


async def _ensure_mongo_indexes() -> None:
    # create_index é idempotente: se o índice já existe, não faz nada
    db = get_db()
    await db.messages.create_index([("phone", 1), ("created_at", -1)])
    await db.messages.create_index([("session_id", 1), ("created_at", -1)])
    await db.sessions.create_index([("phone", 1), ("started_at", -1)])
    await db.sessions.create_index([("started_at", -1)])
    # Resumos de atendimentos anteriores do mesmo beneficiário (summaries.py)
    await db.sessions.create_index([("beneficiario_cpf", 1), ("ended_at", -1)])
    await db.processed_events.create_index("idempotency_key", unique=True)
    # Não é unique porque já existem clientes duplicados por phone na base
    await db.clients.create_index("phone")
    await db.clients.create_index([("session_status", 1), ("last_activity_at", 1)])


def _require(resource, name: str):
    if resource is None:
        raise RuntimeError(f"{name} não inicializado (lifespan não rodou?)")
    return resource


def get_db() -> AsyncIOMotorDatabase:
    return _require(_mongo_client, "Mongo")[config.MONGO_DB_NAME]


def get_mysql_pool() -> aiomysql.Pool:
    return _require(_mysql_pool, "Pool do MySQL")


def get_http_client() -> httpx.AsyncClient:
    return _require(_http_client, "Cliente HTTP")


def get_knowledge_collection():
    return _require(_knowledge_collection, "Coleção do Chroma")
