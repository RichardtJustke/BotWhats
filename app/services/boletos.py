"""Consulta de faturas no boleto-service (que consulta o sistema de faturamento)."""

import logging
from datetime import date, datetime

import httpx

from app.core import config
from app.core.resources import get_http_client
from app.services.business_hours import BRASILIA

logger = logging.getLogger(__name__)

# O boleto-service faz uma chamada ao faturamento por contrato, em sequência
_TIMEOUT = 20.0


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d/%m/%Y").replace(tzinfo=BRASILIA).date()
    except (TypeError, ValueError):
        return None


def _format_valor(valor: str) -> str:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return f"R$ {valor}"
    return "R$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


async def fetch_faturas_abertas(cpf: str) -> list[dict] | None:
    """Faturas em aberto do CPF, da mais antiga pra mais nova.

    Retorna None se o boleto-service não estiver configurado ou falhar
    (diferente de [], que significa "consultou e não há nada em aberto").
    """
    if not config.BOLETO_SERVICE_URL or not config.BOLETO_SERVICE_API_KEY:
        return None

    digits = "".join(c for c in cpf if c.isdigit())
    try:
        resp = await get_http_client().get(
            f"{config.BOLETO_SERVICE_URL}/faturas/{digits}",
            headers={"X-API-KEY": config.BOLETO_SERVICE_API_KEY},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.exception("Falha ao consultar faturas no boleto-service")
        return None

    if not body.get("success"):
        logger.warning("boleto-service respondeu sem sucesso: %s", body.get("error"))
        return None

    hoje = datetime.now(BRASILIA).date()
    faturas = []
    for bloco in body.get("data", []):
        for f in bloco.get("faturas", []):
            status = (f.get("status") or "").lower()
            # O boleto-service já filtra por status em aberto; isso é só defesa
            if "paga" in status or "cancel" in status:
                continue
            vencimento = _parse_date(f.get("dataVencimento"))
            faturas.append({
                **f,
                "contrato": bloco.get("contrato"),
                "vencida": "vencid" in status or (vencimento is not None and vencimento < hoje),
                "_vencimento": vencimento or date.max,
            })

    faturas.sort(key=lambda f: f["_vencimento"])
    return faturas


def _linha(f: dict) -> str:
    situacao = "vencido" if f["vencida"] else "a vencer"
    return (
        f"Vencimento {f.get('dataVencimento')}, {_format_valor(f.get('valorCobranca'))} ({situacao})\n"
        f"{f.get('linkFatura')}"
    )


def format_faturas_abertas(faturas: list[dict]) -> str:
    """Bloco pra mandar no WhatsApp; só chamar com a lista não vazia."""
    if len(faturas) == 1:
        cabecalho = "Identificamos no sistema que há um boleto em aberto:"
    else:
        cabecalho = f"Identificamos no sistema que há {len(faturas)} boletos em aberto:"
    return cabecalho + "\n\n" + "\n\n".join(_linha(f) for f in faturas)


def resumo_financeiro(faturas: list[dict] | None) -> str:
    """Resumo pro contexto do LLM, sem links (esses vão direto pro cliente)."""
    if faturas is None:
        return "Situação financeira: não foi possível consultar os boletos no momento."
    if not faturas:
        return "Situação financeira: em dia, nenhum boleto em aberto."
    vencidas = sum(f["vencida"] for f in faturas)
    itens = "; ".join(
        f"vencimento {f.get('dataVencimento')}, {_format_valor(f.get('valorCobranca'))}, "
        f"{'vencido' if f['vencida'] else 'a vencer'}"
        for f in faturas
    )
    situacao = f"{vencidas} boleto(s) vencido(s)" if vencidas else "boletos a vencer, nenhum atrasado"
    return (
        f"Situação financeira: {situacao}. Boletos em aberto: {itens}. "
        "Os links desses boletos já foram enviados ao cliente nesta conversa."
    )
