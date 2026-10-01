import re
import unicodedata
from datetime import datetime, timezone

from app.core import config
from app.core.resources import get_db

# Qualquer coisa que remeta a cancelamento vai direto pra atendente humano, sem
# o bot explicar processo nem confirmar nada. Na dúvida, encaminha: um falso
# positivo custa pouco, um bot "conduzindo" cancelamento não pode acontecer.
# Os padrões rodam sobre texto sem acento (ver _normalize).
_PLANO = r"(o\s+|meu\s+|minha\s+|esse\s+|este\s+|do\s+|da\s+)*(plano|contrato|convenio|adesao|assinatura)"

_CANCELAMENTO_PATTERNS = [
    r"\bcancel\w*",                     # cancelar, cancela, cancelei, cancelamento...
    r"\brescin\w*|\brescis\w*",         # rescindir, rescisão
    r"\bdistrat\w*",                    # distrato
    r"\bdesist\w*",                     # desistir, desistência
    r"\bportabilidade\b",               # trocar de plano/operadora = sair deste
    rf"\b(encerr|finaliz|termin|suspend|congel|exclu|desativ|inativ)\w*\s+{_PLANO}",
    r"\b(exclu|retir|remov|tir)\w*\s+(o\s+|a\s+|um\s+|uma\s+|meu\s+|minha\s+)*(dependente|filh|espos|marido|conjuge|mae|pai)\w*",
    rf"\b(sair|saio|me\s+desligar|desligar|largar|tirar|dar\s+baixa\s+n[oa])\s+{_PLANO}",
    rf"\bnao\s+(quero|desejo|vou)\s+(mais\s+)?(ficar\s+com\s+|continuar\s+com\s+|manter\s+|pagar\s+)?{_PLANO}",
    rf"\bnao\s+quero\s+mais\s+(ficar|continuar|pagar)\b",
    r"\bparar\s+de\s+pagar\b",
    r"\bnao\s+vou\s+(mais\s+)?pagar\b",
]

# Carta/declaração de permanência é emitida pela equipe, nunca pelo bot
_CARTA_PERMANENCIA_PATTERNS = [
    r"\bpermane?n?cia\b",  # "permanência" (e o erro comum "permanecia") só aparece nesse sentido
    r"\b(carta|declaracao|comprovante|certidao|atestado)\s+(de\s+)?(tempo\s+de\s+(plano|contrato|vinculo|convenio)|vinculo)",
]

# Checagem da resposta do LLM: se ele falar disso mesmo assim, vira encaminhamento
_CANCELAMENTO_RESPONSE_RE = re.compile(r"cancel|rescis|rescin|distrat", re.IGNORECASE)

_ATENDENTE_PATTERNS = [
    r"\batendente\b",
    r"\bfalar\s+com\s+(um[a]?\s+)?(humano|pessoa|alguem|alguém)\b",
    r"\bsupervisor\b",
    r"\bquero\s+um\s+humano\b",
    r"\bquero\s+falar\s+com\s+algu[eé]m\b",
]

_ABUSO_PALAVRAS = [
    "porra", "caralho", "merda", "vsf", "fdp", "desgraça", "desgraca",
    "idiota", "imbecil", "otário", "otario", "vagabundo", "vagabunda",
    "arrombado", "seu lixo", "vc é burro", "voce é burro",
]

_CANCELAMENTO_RE = re.compile("|".join(_CANCELAMENTO_PATTERNS), re.IGNORECASE)
_CARTA_PERMANENCIA_RE = re.compile("|".join(_CARTA_PERMANENCIA_PATTERNS), re.IGNORECASE)
_ATENDENTE_RE = re.compile("|".join(_ATENDENTE_PATTERNS), re.IGNORECASE)

_RESPONSES = {
    # Não menciona cancelamento nem confirma nada: só encaminha
    "cancelamento": (
        "Entendi! Para cuidar disso com toda a atenção que você merece, vou te "
        "encaminhar agora para um dos nossos atendentes. É só aguardar um "
        "instantinho que logo alguém da nossa equipe continua a conversa com você."
    ),
    "carta_permanencia": (
        "Claro! Quem emite a carta de permanência é a nossa equipe, então vou te "
        "encaminhar agora para um dos nossos atendentes. É só aguardar um "
        "instantinho que logo alguém continua a conversa com você."
    ),
    "atendente_humano": (
        "Claro, já vou te transferir para um atendente humano, só um instante."
    ),
    "abuso": (
        "Vou te transferir para um atendente humano para continuar esse atendimento."
    ),
    # LLM decidiu transferir mas não escreveu o aviso junto da marcação
    "transferencia": (
        "Vou te encaminhar para um dos nossos atendentes. É só aguardar um "
        "instantinho que logo alguém da nossa equipe continua a conversa com você."
    ),
}

# Marcação que o LLM põe no fim da resposta quando decide transferir (ver rag.py)
_TRANSFER_RE = re.compile(r"\[\s*transferir\s*\]", re.IGNORECASE)


def _normalize(text: str) -> str:
    """minúsculo e sem acento: "Rescisão" -> "rescisao", "não" -> "nao"."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def detect_escalation(message_body: str) -> str | None:
    lowered = message_body.strip().lower()
    if not lowered:
        return None

    normalized = _normalize(message_body)
    if _CANCELAMENTO_RE.search(normalized):
        return "cancelamento"
    if _CARTA_PERMANENCIA_RE.search(normalized):
        return "carta_permanencia"
    if _ATENDENTE_RE.search(lowered):
        return "atendente_humano"
    if any(palavra in lowered for palavra in _ABUSO_PALAVRAS):
        return "abuso"
    return None


# Fora do horário não há atendente pra receber o cancelamento: o cliente é
# orientado a fazer pelo app, como na base (cancelamento/como-solicitar-o-cancelamento-pelo-aplicativo.md).
# Portabilidade e exclusão de dependente não são "Cancelamento do Plano" no app
# e seguem pra fila humana mesmo fora do horário.
_NAO_E_CANCELAMENTO_DO_PLANO_RE = re.compile(
    r"\bportabilidade\b|\bdependente|\bfilh|\bespos|\bmarido\b|\bconjuge\b|\bmae\b|\bpai\b"
)
_ODONTO_RE = re.compile(r"\bodonto\w*|\bdent\w*")

_STORE_LINKS = "\n".join(
    f"{loja}: {url}"
    for loja, url in (("Apple Store", config.APP_STORE_URL), ("Google Play", config.PLAY_STORE_URL))
    if url
)

_CANCELAMENTO_APP_TEXT = (
    "No momento estamos fora do horário de atendimento, mas você pode solicitar o "
    f"cancelamento a qualquer hora pelo aplicativo {config.COMPANY_APP_NAME}: é só entrar na aba "
    "\"Solicitações\", escolher \"Cancelamento do Plano\", preencher as informações e enviar "
    "o pedido. Por lá você acompanha todo o andamento da solicitação.\n\n"
    "Se ainda não tiver cadastro no app, basta criar uma conta (esse acesso é separado do "
    "login da operadora)."
    + (f"\n\n{_STORE_LINKS}" if _STORE_LINKS else "")
)

# Odontológico não é cancelado pelo app (cancelamento/cancelamento-do-plano-odontologico.md)
_CANCELAMENTO_ODONTO_TEXT = (
    "No momento estamos fora do horário de atendimento. O cancelamento do plano "
    "odontológico não é feito pelo aplicativo: é só enviar um e-mail para "
    f"{config.SUPPORT_EMAIL} com seu nome completo, CPF e o motivo do cancelamento."
)


def off_hours_cancelamento_response(message_body: str) -> str | None:
    """Resposta pra pedido de cancelamento fora do horário, ou None se o pedido
    não for do plano em si e deve ir pra fila humana normalmente."""
    normalized = _normalize(message_body)
    if _NAO_E_CANCELAMENTO_DO_PLANO_RE.search(normalized):
        return None
    if _ODONTO_RE.search(normalized):
        return _CANCELAMENTO_ODONTO_TEXT
    return _CANCELAMENTO_APP_TEXT


def split_transfer(text: str) -> tuple[str, bool]:
    """Tira a marcação [TRANSFERIR] da resposta do LLM: (texto limpo, pediu transferência)."""
    if not _TRANSFER_RE.search(text):
        return text, False
    return _TRANSFER_RE.sub("", text).strip(), True


def mentions_cancelamento(text: str) -> bool:
    return bool(_CANCELAMENTO_RESPONSE_RE.search(_normalize(text)))


async def register_escalation(phone: str, category: str, message_body: str) -> None:
    await get_db().escalations.insert_one({
        "phone": phone,
        "category": category,
        "message": message_body,
        "created_at": datetime.now(timezone.utc),
        "status": "pending",
    })


def escalation_response(category: str) -> str:
    return _RESPONSES[category]
