import logging

import httpx

from app.core import config
from app.core.resources import get_http_client

logger = logging.getLogger(__name__)


async def resolve_phone(session_id: str, contact_id: str) -> str | None:
    url = f"{config.OPENWA_API_URL}/sessions/{session_id}/contacts/{contact_id}/phone"
    try:
        resp = await get_http_client().get(url, headers={"X-API-Key": config.OPENWA_API_KEY})
    except httpx.HTTPError:
        logger.exception("Falha ao resolver telefone via OpenWA")
        return None
    if resp.status_code == 200:
        return resp.json().get("phone")
    return None


async def send_message(session_id: str, chat_id: str, text: str) -> None:
    url = f"{config.OPENWA_API_URL}/sessions/{session_id}/messages/send-text"
    resp = await get_http_client().post(
        url,
        headers={"X-API-Key": config.OPENWA_API_KEY},
        json={"chatId": chat_id, "text": text},
    )
    resp.raise_for_status()


async def send_image(session_id: str, chat_id: str, image_url: str, caption: str | None = None) -> None:
    """Envia uma imagem a partir de uma URL pública (o OpenWA baixa e envia)."""
    url = f"{config.OPENWA_API_URL}/sessions/{session_id}/messages/send-image"
    body = {"chatId": chat_id, "url": image_url}
    if caption:
        body["caption"] = caption
    resp = await get_http_client().post(
        url,
        headers={"X-API-Key": config.OPENWA_API_KEY},
        json=body,
        timeout=30.0,  # o OpenWA baixa a imagem antes de enviar
    )
    if resp.is_error:
        logger.error("OpenWA recusou send-image (%s): %s", resp.status_code, resp.text[:300])
    resp.raise_for_status()
