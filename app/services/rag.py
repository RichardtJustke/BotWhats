import asyncio
import logging
import re

import httpx

from app.core import config
from app.core.resources import get_http_client, get_knowledge_collection

LLM_TIMEOUT = 30.0

logger = logging.getLogger(__name__)


async def embed_query(text: str) -> list[float]:
    resp = await get_http_client().post(
        "https://openrouter.ai/api/v1/embeddings",
        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
        json={"model": "openai/text-embedding-3-small", "input": text},
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def search_knowledge(query: str, n_results: int = 5) -> list[dict]:
    embedding = await embed_query(query)

    results = get_knowledge_collection().query(
        query_embeddings=[embedding],
        n_results=n_results,
        # Cancelamento é sempre com atendente humano (ver escalation.py): o bot
        # não pode ter esse conteúdo em mãos pra explicar processo
        where={"tema": {"$ne": "cancelamento"}},
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        chunks.append({
            "content": doc,
            "source": meta.get("source"),
            "tema": meta.get("tema"),
            "distance": dist,
        })

    return chunks


# Persona, regras e formato ficam em prompts/system.md
SYSTEM_PROMPT_BASE = config.load_prompt("system")

# Palavras que indicam pergunta sobre canal de contato: nesses casos a operadora
# do cliente entra na busca, senão "qual o telefone?" traz o de qualquer operadora
_CONTACT_TERMS = re.compile(
    r"telefone|fone|contato|whats|zap|ligar|liga[çc][aã]o|n[úu]mero|central|canal|canais|0800|site|aplicativo|\bapp\b",
    re.IGNORECASE,
)

# Sequência com cara de telefone: dígitos com espaço, ponto, hífen, parênteses ou +
_PHONE_RE = re.compile(r"\+?\(?\d[\d\s().-]{6,}\d")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

UNVERIFIED_PHONE_REPLY = (
    "Não encontrei esse contato exato aqui comigo e não quero te passar um número errado. "
    "Você consegue confirmar no aplicativo ou no site oficial da sua operadora, e se precisar "
    "de mais alguma coisa é só me falar."
)


def _phone_digits(text: str) -> set[str]:
    found = set()
    for match in _PHONE_RE.findall(text):
        if _DATE_RE.match(match.strip()):
            continue
        digits = re.sub(r"\D", "", match)
        if 8 <= len(digits) <= 13:
            found.add(digits)
    return found


def find_unverified_phones(response: str, allowed_text: str) -> set[str]:
    """Telefones da resposta que não aparecem em nenhum lugar do texto permitido.

    A comparação é só por dígitos e aceita variação de prefixo/DDD: "91234-5678"
    bate com "(11) 91234-5678", e "+55 11 91234-5678" também.
    """
    allowed = _phone_digits(allowed_text)
    return {
        phone for phone in _phone_digits(response)
        if not any(phone in known or known in phone for known in allowed)
    }


def _build_search_query(query: str, history: list[dict], operadora: str | None) -> str:
    # Pergunta de seguimento ("e o whatsapp deles?") não tem sentido sozinha:
    # as últimas mensagens do cliente dão o assunto pra busca
    recent_user = [m["content"] for m in history if m["role"] == "user"][-2:]
    parts = recent_user + [query]
    if operadora and _CONTACT_TERMS.search(query):
        parts.append(f"beneficiário {operadora}")
    return "\n".join(parts)


def build_system_prompt(chunks: list[dict], client_context: str = "Cliente ainda não identificado.") -> str:
    context = "\n---\n".join(c["content"] for c in chunks)
    # replace em vez de format: o .md pode ter chaves soltas
    return SYSTEM_PROMPT_BASE.replace("{{cliente_atual}}", client_context).replace("{{contexto}}", context)


async def generate_response(
    query: str,
    history: list[dict],
    client_context: str = "Cliente ainda não identificado.",
    operadora: str | None = None,
) -> str:
    chunks = await search_knowledge(_build_search_query(query, history, operadora), n_results=3)
    logger.info(
        "Fontes usadas: %s",
        ", ".join(f"{c['source']} ({c['distance']:.3f})" for c in chunks),
    )
    system_prompt = build_system_prompt(chunks, client_context)
    # Tudo que o modelo "viu": um telefone fora disso foi inventado
    allowed_text = "\n".join(
        [system_prompt, query] + [m["content"] for m in history]
    )

    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": query}]
    content = await chat_completion(messages)

    invented = find_unverified_phones(content, allowed_text)
    if invented:
        logger.warning(
            "Telefone fora do contexto na resposta, trocando por resposta segura: %s | resposta original: %s",
            ", ".join(sorted(invented)), content,
        )
        return UNVERIFIED_PHONE_REPLY
    return content


async def chat_completion(messages: list[dict], temperature: float = 0.2) -> str:
    """Chama os modelos em ordem de preferência até um responder."""
    for model in config.OPENROUTER_MODELS:
        for attempt in range(2):
            try:
                resp = await get_http_client().post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    timeout=LLM_TIMEOUT,
                    headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                    },
                )
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning("Modelo %s: %s (tentativa %d)", model, type(e).__name__, attempt + 1)
                if attempt == 0:
                    await asyncio.sleep(2)
                continue

            if resp.status_code != 200:
                logger.warning("Modelo %s falhou: %s %s", model, resp.status_code, resp.text[:300])
                break

            content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content")
            if not content or not content.strip():
                logger.warning("Modelo %s devolveu resposta vazia", model)
                break

            logger.info("Resposta gerada com %s", model)
            return content

    raise RuntimeError(f"Nenhum modelo conseguiu responder: {config.OPENROUTER_MODELS}")