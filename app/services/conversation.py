from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from app.core.resources import get_db


async def save_message(client: dict, role: str, content: str, source: str = "llm") -> None:
    """Salva uma mensagem ligada à sessão atual do cliente."""
    now = datetime.now(timezone.utc)
    session_id = client["current_session_id"]
    await get_db().messages.insert_one({
        "session_id": session_id,
        "phone": client["phone"],
        "role": role,
        "content": content,
        "source": source,  # "llm", "system" (respostas hardcoded, ex: fluxo de identificação),
                            # "human" (atendente digitando pelo número da empresa)
                            # ou "user" (esse último só quando role="user")
        "created_at": now,
    })
    await get_db().sessions.update_one(
        {"_id": session_id},
        {"$inc": {"message_count": 1}, "$set": {"last_message_at": now}},
    )


async def get_session_messages(session_id, limit: int = 10) -> list[dict]:
    """Últimas `limit` mensagens da sessão, em ordem cronológica."""
    cursor = get_db().messages.find({"session_id": session_id}).sort("created_at", -1).limit(limit)
    messages = await cursor.to_list(length=limit)
    return list(reversed(messages))


async def is_recent_bot_message(session_id, content: str, window_minutes: int = 5) -> bool:
    """O bot enviou esse texto nessa sessão há pouco? Serve pra separar o eco
    das mensagens do próprio bot (o WhatsApp avisa toda mensagem enviada pelo
    número) das mensagens digitadas pelo atendente humano."""
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    found = await get_db().messages.find_one({
        "session_id": session_id,
        "role": "assistant",
        "source": {"$in": ["llm", "system"]},
        "content": content,
        "created_at": {"$gte": since},
    })
    return found is not None


async def mark_processed(idempotency_key: str) -> bool:
    """Registra o evento; devolve False se ele já tinha sido processado.

    Atômico graças ao índice único em idempotency_key: duas entregas
    simultâneas do mesmo evento não passam as duas.
    """
    try:
        await get_db().processed_events.insert_one({
            "idempotency_key": idempotency_key,
            "processed_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        return False
    return True