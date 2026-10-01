"""Resumo dos atendimentos encerrados, pra dar contexto ao próximo atendimento.

Só atendimentos em que o beneficiário confirmou a identidade são resumidos, e o
resumo só volta pro prompt quando ESSE MESMO CPF for confirmado de novo: o
celular pode estar com outra pessoa, então visitante nunca vê histórico.

Os resumos são gerados em background (`summary_watcher`), fora do caminho da
resposta; se o LLM falhar, tenta de novo nas próximas rodadas.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core import config
from app.core.resources import get_db
from app.services.rag import chat_completion

logger = logging.getLogger(__name__)

WATCH_INTERVAL_SECONDS = 60
MAX_ATTEMPTS = 3
MIN_MESSAGES = 2  # sessão só com "oi" não vale resumo
MAX_TRANSCRIPT_MESSAGES = 40
# Horário de Brasília fixo (sem horário de verão desde 2019); evita depender do
# pacote tzdata na imagem Docker
_TZ = timezone(timedelta(hours=-3))

_SUMMARY_PROMPT = f"""Você resume atendimentos de WhatsApp da {config.COMPANY_NAME} (administradora de
planos de saúde) para o próximo atendente ler em segundos.

Escreva UMA ou DUAS frases curtas, em português, dizendo o que o cliente queria
e como terminou (resolvido, encaminhado para atendente, ficou pendente).
Não inclua CPF, telefone, e-mail, valores ou qualquer número de documento.
Não invente nada que não esteja na conversa. Sem markdown, sem aspas."""

_SPEAKER = {"user": "Cliente", "llm": config.BOT_NAME, "system": config.BOT_NAME, "human": "Atendente"}


def _as_utc(dt: datetime) -> datetime:
    # Mongo devolve datetime sem timezone (mas em UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _transcript(session_id) -> str:
    cursor = get_db().messages.find({"session_id": session_id}).sort("created_at", 1).limit(MAX_TRANSCRIPT_MESSAGES)
    lines = []
    async for m in cursor:
        speaker = "Cliente" if m["role"] == "user" else _SPEAKER.get(m.get("source"), config.BOT_NAME)
        lines.append(f"{speaker}: {m['content']}")
    return "\n".join(lines)


async def _summarize_next() -> bool:
    """Resume um atendimento pendente. Devolve False quando não há mais nenhum."""
    # Reivindica atomicamente (incrementando as tentativas antes de chamar o LLM):
    # com mais de um worker, cada sessão é resumida por um só
    session = await get_db().sessions.find_one_and_update(
        {
            "status": "closed",
            "beneficiario_cpf": {"$nin": [None, ""]},
            "summary": {"$exists": False},
            "summary_attempts": {"$not": {"$gte": MAX_ATTEMPTS}},
            "message_count": {"$gte": MIN_MESSAGES},
        },
        {"$inc": {"summary_attempts": 1}},
    )
    if session is None:
        return False

    transcript = await _transcript(session["_id"])
    try:
        summary = await chat_completion(
            [
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=0.0,
        )
    except (httpx.HTTPError, RuntimeError):
        logger.exception("Falha ao resumir atendimento %s (tenta de novo depois)", session["_id"])
        return False  # LLM fora: espera a próxima rodada em vez de insistir agora

    await get_db().sessions.update_one(
        {"_id": session["_id"]},
        {"$set": {"summary": summary.strip(), "summary_at": datetime.now(timezone.utc)}},
    )
    logger.info("Atendimento %s resumido", session["_id"])
    return True


async def summary_watcher() -> None:
    while True:
        try:
            while await _summarize_next():
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erro no watcher de resumos")
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)


async def previous_sessions_context(beneficiario_cpf: str, current_session_id, limit: int = 3) -> str | None:
    """Resumo dos últimos atendimentos desse beneficiário, pronto pro prompt.

    Chamar só depois da identidade confirmada (ver identification.py).
    """
    cursor = get_db().sessions.find(
        {
            "beneficiario_cpf": beneficiario_cpf,
            "summary": {"$exists": True},
            "_id": {"$ne": current_session_id},
        },
        {"summary": 1, "ended_at": 1},
    ).sort("ended_at", -1).limit(limit)

    lines = []
    async for s in cursor:
        date = _as_utc(s["ended_at"]).astimezone(_TZ).strftime("%d/%m/%Y")
        lines.append(f"- {date}: {s['summary']}")
    if not lines:
        return None
    return "Atendimentos anteriores (mais recente primeiro):\n" + "\n".join(lines)
