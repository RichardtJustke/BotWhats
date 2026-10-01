import logging

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from app.core import config
from app.core.security import verify_signature
from app.models.webhook_payload import WebhookPayload
from app.services.business_hours import is_business_hours
from app.services.clients import get_or_create_client
from app.core.resources import get_db
from app.services.conversation import (
    get_session_messages,
    is_recent_bot_message,
    mark_processed,
    save_message,
)
from app.services.easter_eggs import match_easter_egg
from app.services.escalation import (
    detect_escalation,
    escalation_response,
    mentions_cancelamento,
    off_hours_cancelamento_response,
    register_escalation,
    split_transfer,
)
from app.services.identification import handle_identification
from app.services.openwa import resolve_phone, send_image, send_message
from app.services.rag import generate_response
from app.services.session import (
    CLOSING_NO_REPLY_TEXT,
    CLOSING_TEXT,
    FOLLOWUP_TEXT,
    close_session,
    mark_escalated,
    register_human_message,
    start_session,
    wants_to_finish,
)

router = APIRouter()
logger = logging.getLogger(__name__)


async def _build_history_for_llm(client: dict) -> list[dict]:
    # +1 porque a mensagem do usuário atual já foi salva e volta no fim da lista;
    # ela é excluída aqui pois `generate_response` já a anexa separadamente.
    recent = await get_session_messages(client["current_session_id"], limit=11)
    return [{"role": m["role"], "content": m["content"]} for m in recent[:-1]]


# Textos que o watcher de sessão manda sozinho: ele envia antes de salvar, então
# o eco pode chegar antes de a mensagem existir no histórico
_BOT_FIXED_TEXTS = {FOLLOWUP_TEXT, CLOSING_TEXT, CLOSING_NO_REPLY_TEXT}
_MEDIA_TYPES = {"image", "video", "audio", "ptt", "document", "sticker"}


async def _handle_outgoing(payload: WebhookPayload) -> dict:
    """Mensagem enviada pelo número da empresa: registra se foi o atendente."""
    body = payload.data.body
    # Mídia (inclui as imagens dos easter eggs) não entra no histórico de texto
    if payload.data.type in _MEDIA_TYPES or not body.strip() or body in _BOT_FIXED_TEXTS:
        return {"status": "ignored"}

    client = await get_db().clients.find_one({"chat_id": payload.data.chatId})
    if not client or "current_session_id" not in client:
        # Conversa que nunca passou pelo bot: nada a registrar
        return {"status": "ignored"}

    if await is_recent_bot_message(client["current_session_id"], body):
        return {"status": "bot_echo_ignored"}

    logger.info("Mensagem do atendente para %s: %s", client["phone"], body)
    await register_human_message(client, payload.data.chatId, payload.sessionId, body)
    return {"status": "human_recorded"}


async def _escalate(client: dict, category: str, message_body: str) -> str:
    """Encaminha pra fila humana e devolve o texto a enviar.

    Cancelamento fora do horário não tem atendente pra receber: o cliente recebe
    o passo a passo do app e a conversa continua com o bot (o pedido fica
    registrado pra equipe ver).
    """
    if category == "cancelamento" and not is_business_hours():
        off_hours = off_hours_cancelamento_response(message_body)
        if off_hours:
            await register_escalation(client["phone"], "cancelamento_fora_horario", message_body)
            await save_message(client, "assistant", off_hours, source="system")
            return off_hours

    response_text = escalation_response(category)
    await register_escalation(client["phone"], category, message_body)
    await mark_escalated(client, category)
    await save_message(client, "assistant", response_text, source="system")
    return response_text


@router.post("/webhook/openwa")
async def receive_webhook(
    request: Request,
    x_openwa_signature: str | None = Header(default=None),
):
    raw_body = await request.body()

    if not verify_signature(raw_body, x_openwa_signature, config.OPENWA_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="assinatura inválida")

    try:
        payload = WebhookPayload(**await request.json())
    except ValidationError as e:
        logger.warning("Payload não bateu com o modelo esperado: %s", e)
        return {"status": "ignored"}

    # fromMe: mensagem que saiu do número da empresa (bot ou atendente humano),
    # venha no evento que vier (o nome do evento de envio é configurado no OpenWA)
    if not payload.data.fromMe and payload.event != "message.received":
        return {"status": "ignored"}

    if not await mark_processed(payload.idempotencyKey):
        logger.info("Evento já processado, ignorando: %s", payload.idempotencyKey)
        return {"status": "duplicate_ignored"}

    if payload.data.fromMe:
        return await _handle_outgoing(payload)

    sender_id = payload.data.from_
    phone = await resolve_phone(payload.sessionId, sender_id) if sender_id.endswith("@lid") else None

    logger.info(
        "Mensagem de %s (%s, tel: %s): %s",
        sender_id, payload.data.contact.pushName, phone, payload.data.body,
    )

    client = await get_or_create_client(phone or sender_id, payload.data.contact.pushName)
    client, new_session, prev_status = await start_session(client, payload.data.chatId, payload.sessionId)
    if new_session:
        logger.info("Nova sessão para %s (identificação reiniciada)", client["phone"])
    await save_message(client, "user", payload.data.body, source="user")

    if client["session_status"] == "escalated":
        # Conversa está com atendente humano: registra a mensagem e não responde
        logger.info("Conversa escalada, bot em silêncio: %s", client["phone"])
        return {"status": "escalated_silenced"}

    easter_egg = match_easter_egg(payload.data.body)
    if easter_egg:
        # whatsapp-web.js quebra ao enviar mídia para chats @lid ("Data passed to
        # getter must include an id property"); pro número real (@c.us) funciona
        media_chat_id = f"{phone}@c.us" if phone else payload.data.chatId
        try:
            await send_image(payload.sessionId, media_chat_id, easter_egg.image_url, caption=easter_egg.caption)
        except httpx.HTTPError:
            logger.exception("Falha ao enviar imagem do easter egg")
            return {"status": "error", "detail": "falha ao enviar imagem"}
        await save_message(client, "assistant", easter_egg.history_text, source="system")
        return {"status": "ok"}

    escalation_category = detect_escalation(payload.data.body)

    if prev_status == "awaiting_more_help" and wants_to_finish(payload.data.body):
        # Cliente respondeu "não" ao "posso ajudar em algo mais?"
        response_text = CLOSING_TEXT
        await close_session(client)
        await save_message(client, "assistant", response_text, source="system")
    elif escalation_category:
        response_text = await _escalate(client, escalation_category, payload.data.body)
    else:
        hardcoded_reply, plano_contexto, operadora = await handle_identification(client, payload.data.body)

        if hardcoded_reply:
            response_text = hardcoded_reply
            await save_message(client, "assistant", response_text, source="system")
        else:
            history_for_llm = await _build_history_for_llm(client)

            try:
                response_text = await generate_response(
                    payload.data.body, history_for_llm, plano_contexto, operadora
                )
            except (httpx.HTTPError, RuntimeError):  # RuntimeError: todos os modelos falharam
                logger.exception("Falha ao gerar resposta via LLM")
                return {"status": "error", "detail": "falha ao gerar resposta"}

            response_text, wants_transfer = split_transfer(response_text)

            if mentions_cancelamento(response_text):
                # Detecção da mensagem falhou mas o LLM entrou no assunto: encaminha
                logger.warning("LLM falou de cancelamento, encaminhando para humano: %s", response_text)
                response_text = await _escalate(client, "cancelamento", payload.data.body)
            elif wants_transfer:
                # LLM seguiu um critério de transferência do prompt: vai pra fila humana
                logger.info("LLM pediu transferência para humano: %s", client["phone"])
                response_text = response_text or escalation_response("transferencia")
                await register_escalation(client["phone"], "transferencia", payload.data.body)
                await mark_escalated(client, "transferencia")
                await save_message(client, "assistant", response_text, source="llm")
            else:
                await save_message(client, "assistant", response_text, source="llm")

    try:
        await send_message(payload.sessionId, payload.data.chatId, response_text)
    except httpx.HTTPError:
        logger.exception("Falha ao enviar resposta via OpenWA")
        return {"status": "error", "detail": "falha ao enviar resposta"}

    return {"status": "ok"}
