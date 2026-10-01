"""Ciclo de vida da sessão de atendimento, guardado no documento do cliente:

    active ──(SESSION_TIMEOUT_MINUTES sem mensagem)──→ awaiting_more_help
       ↑          bot pergunta "posso ajudar em algo mais?"        │
       │                                                           │
       ├──── cliente responde qualquer coisa ←─────────────────────┤
       │                                                           │
       │     cliente responde "não/obrigado"  ou  SESSION_CLOSE_MINUTES
       │     sem resposta → bot encerra o atendimento              │
       │                                                           ↓
    nova mensagem → sessão nova (histórico e identificação zerados) ← closed

Conversas escaladas pra humano ficam em "escalated": não recebem a pergunta e
o bot não responde nada até ESCALATION_RELEASE_MINUTES sem mensagens, quando a
sessão é encerrada em silêncio e a próxima mensagem volta a ser do bot.

Cada atendimento vira um documento na coleção `sessions` (início, fim, motivo
do encerramento, se era beneficiário, nº de mensagens) e toda mensagem em
`messages` carrega o `session_id`, pra achar a conversa inteira depois.
O estado operacional (status, última atividade) fica no documento do cliente.
As transições automáticas são feitas por `session_watcher`, uma task em
background iniciada no lifespan do main.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.core import config
from app.core.resources import get_db
from app.services.clients import IDENTITY_FIELDS
from app.services.conversation import save_message
from app.services.openwa import send_message

logger = logging.getLogger(__name__)

FOLLOWUP_TEXT = "Posso te ajudar em algo mais?"
CLOSING_TEXT = (
    "Foi um prazer te ajudar! Seu atendimento foi encerrado. ✅\n\n"
    "Se precisar de qualquer coisa, é só me mandar uma mensagem aqui "
    "que eu te respondo na hora. 😊"
)
CLOSING_NO_REPLY_TEXT = (
    "Como não tive retorno, seu atendimento foi encerrado. ✅\n\n"
    "Se precisar de qualquer coisa, é só me mandar uma mensagem aqui "
    "que eu te respondo na hora. 😊"
)

_NO_MORE_HELP = {
    "nao", "não", "n", "nao obrigado", "não obrigado", "nao obrigada", "não obrigada",
    "so isso", "só isso", "era so isso", "era só isso", "e so isso", "é só isso",
    "nada", "nada mais", "obrigado", "obrigada", "valeu", "tchau", "ok obrigado",
    "ok obrigada", "de nada", "tudo certo", "ta bom", "tá bom",
}

WATCH_INTERVAL_SECONDS = 30


def as_utc(dt: datetime) -> datetime:
    # Mongo devolve datetime sem timezone (mas em UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.strip().lower()).strip()


def wants_to_finish(message_body: str) -> bool:
    return _normalize(message_body) in _NO_MORE_HELP


async def _open_session(client: dict, now: datetime):
    result = await get_db().sessions.insert_one({
        "phone": client["phone"],
        "client_name": client.get("name"),
        "started_at": now,
        "last_message_at": now,
        "ended_at": None,
        "status": "open",
        "end_reason": None,
        "escalated": False,
        "message_count": 0,
    })
    return result.inserted_id


async def _end_session(client: dict, reason: str) -> None:
    """Fecha o registro da sessão. `client` é o documento ANTES de zerar a
    identificação, pra registrar como o atendimento terminou."""
    session_id = client.get("current_session_id")
    if session_id is None:
        return
    await get_db().sessions.update_one(
        {"_id": session_id, "status": "open"},
        {"$set": {
            "status": "closed",
            "ended_at": datetime.now(timezone.utc),
            "end_reason": reason,  # cliente_encerrou | sem_resposta | inatividade | escalado_humano
            "identity_status": client.get("identity_status"),
            "beneficiario": client.get("identity_status") == "confirmed",
            # Dono confirmado do atendimento: o resumo só volta pra quem confirmar esse CPF
            "beneficiario_cpf": client.get("beneficiario_cpf")
            if client.get("identity_status") == "confirmed" else None,
        }},
    )


async def start_session(client: dict, chat_id: str, wa_session_id: str) -> tuple[dict, bool, str | None]:
    """Chamar a cada mensagem recebida, antes de processá-la.

    Abre uma sessão nova (zerando a identificação) se a anterior foi encerrada
    ou nunca existiu; senão reativa a atual. Guarda chat/sessão do OpenWA pra
    que o watcher consiga mandar mensagens proativas depois.
    Devolve (client atualizado, se é sessão nova, status anterior da sessão).
    """
    now = datetime.now(timezone.utc)
    prev_status = client.get("session_status")
    last = client.get("last_activity_at")
    # Fallback caso o watcher não tenha rodado (API desligada no meio da sessão)
    if prev_status == "escalated":
        max_idle = timedelta(minutes=config.ESCALATION_RELEASE_MINUTES)
    else:
        max_idle = timedelta(minutes=config.SESSION_TIMEOUT_MINUTES + config.SESSION_CLOSE_MINUTES)
    new_session = (
        prev_status in (None, "closed")
        or last is None
        or now - as_utc(last) > max_idle
        or "current_session_id" not in client
    )

    fields = {
        # Conversa com atendente humano continua escalada (bot em silêncio)
        "session_status": "escalated" if prev_status == "escalated" and not new_session else "active",
        "last_activity_at": now,
        "chat_id": chat_id,
        "wa_session_id": wa_session_id,
    }
    update = {"$set": fields, "$unset": {"followup_at": ""}}
    if new_session:
        await _end_session(client, "inatividade")  # no-op se já estava fechada
        fields["current_session_id"] = await _open_session(client, now)
        update["$unset"].update({f: "" for f in IDENTITY_FIELDS})
        client = {k: v for k, v in client.items() if k not in IDENTITY_FIELDS}

    await get_db().clients.update_one({"phone": client["phone"]}, update)
    client.update(fields)
    return client, new_session, prev_status


async def close_session(client: dict) -> None:
    """Cliente respondeu que não precisa de mais nada."""
    await get_db().clients.update_one(
        {"phone": client["phone"]},
        {"$set": {"session_status": "closed"}, "$unset": {"followup_at": ""}},
    )
    await _end_session(client, "cliente_encerrou")


async def mark_escalated(client: dict, category: str) -> None:
    await get_db().clients.update_one({"phone": client["phone"]}, {"$set": {"session_status": "escalated"}})
    await get_db().sessions.update_one(
        {"_id": client["current_session_id"]},
        {"$set": {"escalated": True, "escalation_category": category}},
    )


async def register_human_message(client: dict, chat_id: str, wa_session_id: str, text: str) -> None:
    """Atendente humano mandou mensagem pelo número da empresa.

    Conta como atividade da conversa (o prazo de ESCALATION_RELEASE_MINUTES
    recomeça) e, se o bot ainda estava atendendo, o humano assume: a conversa
    vira "escalated" e o bot para de responder.
    """
    client, _, _ = await start_session(client, chat_id, wa_session_id)
    if client["session_status"] != "escalated":
        await mark_escalated(client, "atendente_assumiu")
    await save_message(client, "assistant", text, source="human")


async def _send_system(client: dict, text: str) -> None:
    try:
        await send_message(client["wa_session_id"], client["chat_id"], text)
    except httpx.HTTPError:
        logger.exception("Falha ao enviar mensagem proativa para %s", client["phone"])
        return
    await save_message(client, "assistant", text, source="system")


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    clients = get_db().clients

    # find_one_and_update "reivindica" cada cliente atomicamente: mesmo com
    # mais de um worker rodando, a mensagem sai uma vez só.
    idle_limit = now - timedelta(minutes=config.SESSION_TIMEOUT_MINUTES)
    while client := await clients.find_one_and_update(
        {
            "session_status": "active",
            "last_activity_at": {"$lt": idle_limit},
            "current_session_id": {"$exists": True},
        },
        {"$set": {"session_status": "awaiting_more_help", "followup_at": now}},
    ):
        logger.info("Sessão ociosa, perguntando se pode ajudar em algo mais: %s", client["phone"])
        await _send_system(client, FOLLOWUP_TEXT)

    close_limit = now - timedelta(minutes=config.SESSION_CLOSE_MINUTES)
    while client := await clients.find_one_and_update(
        {
            "session_status": "awaiting_more_help",
            "followup_at": {"$lt": close_limit},
            "current_session_id": {"$exists": True},
        },
        {"$set": {"session_status": "closed"}, "$unset": {"followup_at": ""}},
    ):
        logger.info("Sem resposta ao 'algo mais?', encerrando atendimento: %s", client["phone"])
        await _send_system(client, CLOSING_NO_REPLY_TEXT)
        await _end_session(client, "sem_resposta")

    # Conversa escalada parada: devolve pro bot sem mandar nada (quem estava
    # falando com o cliente era o atendente humano)
    release_limit = now - timedelta(minutes=config.ESCALATION_RELEASE_MINUTES)
    while client := await clients.find_one_and_update(
        {
            "session_status": "escalated",
            "last_activity_at": {"$lt": release_limit},
            "current_session_id": {"$exists": True},
        },
        {"$set": {"session_status": "closed"}},
    ):
        logger.info("Conversa escalada sem atividade, liberando para o bot: %s", client["phone"])
        await _end_session(client, "escalado_humano")


async def session_watcher() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erro no watcher de sessões")
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
