from app.core import config
from app.services.beneficiarios import find_by_cpf, find_by_phone
from app.services.boletos import fetch_faturas_abertas, format_faturas_abertas, resumo_financeiro
from app.services.clients import update_identity
from app.services.summaries import previous_sessions_context

_AFFIRMATIVE = {"sim", "s", "isso", "correto", "exato", "certo", "confirmo", "sou eu", "isso mesmo", "é isso"}


def _only_digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def _is_affirmative(text: str) -> bool:
    return text.strip().lower() in _AFFIRMATIVE


def _mask_cpf(cpf: str) -> str:
    digits = _only_digits(cpf)
    return f"**{digits[-2:]}" if len(digits) >= 2 else "**"


VISITOR_CONTEXT = (
    "Visitante: NÃO foi identificado como beneficiário (pode ser alguém "
    "conhecendo os planos ou um cliente com telefone diferente do cadastro). "
    "Atenda normalmente dúvidas gerais. Só se ele pedir algo pessoal do plano "
    "(boleto, segunda via, carteirinha, dados do contrato), peça com gentileza "
    "o CPF (somente números) para localizar o cadastro."
)

_INTRO = f"Oi, tudo bem? Eu sou a {config.BOT_NAME}, assistente virtual da {config.COMPANY_NAME}. 😊"


def is_beneficiario(client: dict) -> bool:
    """Cliente foi localizado no banco de beneficiários e confirmou a identidade."""
    return client.get("identity_status") == "confirmed"


def _plano_contexto(beneficiario: dict) -> str:
    return (
        f"Cliente: {beneficiario['nome_beneficiario']}, "
        f"Operadora: {beneficiario.get('operadora') or 'não informada'}, "
        f"Plano: {beneficiario['plano']}, Status: {beneficiario['status']}"
    )


async def _try_cpf(phone: str, cpf_digits: str) -> tuple[str | None, str | None]:
    beneficiario = await find_by_cpf(cpf_digits)
    if not beneficiario:
        # Não insiste: segue como visitante, sem prender a pessoa pedindo CPF
        await update_identity(phone, identity_status="visitor")
        return (
            "Não encontrei esse CPF entre os nossos beneficiários, mas sem problema! "
            f"Posso te ajudar com informações sobre os planos e serviços da {config.COMPANY_NAME}. "
            "Me conta, em que posso te ajudar?",
            None,
        )

    await update_identity(
        phone,
        identity_status="awaiting_confirmation",
        pending_cpf=beneficiario["cpf"],
        pending_nome=beneficiario["nome_beneficiario"],
    )
    return (
        f"Encontrei o cadastro de {beneficiario['nome_beneficiario']}, CPF final "
        f"{_mask_cpf(beneficiario['cpf'])}. É você? Se sim, é só responder 'sim'.",
        None,
    )


async def _confirm_identity(client: dict) -> str:
    """Confirma o beneficiário e já consulta os boletos: se houver algum em
    aberto, ele vem na própria mensagem de confirmação."""
    cpf = client["pending_cpf"]
    faturas = await fetch_faturas_abertas(cpf)
    await update_identity(
        client["phone"],
        identity_status="confirmed",
        beneficiario_cpf=cpf,
        resumo_financeiro=resumo_financeiro(faturas),
    )

    nome = client.get("pending_nome", "").split(" ")[0]
    if not faturas:  # None (falhou) ou [] (nada em aberto): segue o atendimento normal
        return f"Perfeito, {nome}! Identidade confirmada. Como posso te ajudar hoje?"
    return (
        f"Perfeito, {nome}! Identidade confirmada.\n\n"
        f"{format_faturas_abertas(faturas)}\n\n"
        "Caso o motivo do seu contato não seja esse, me conta como posso te ajudar."
    )


async def handle_identification(
    client: dict, message_body: str
) -> tuple[str | None, str | None, str | None]:
    """Avança a máquina de estados de identificação do cliente.

    Identificar é um bônus, não uma barreira: quem não é achado no banco vira
    "visitor" e é atendido normalmente pelo LLM.

    Retorna (resposta_hardcoded, plano_contexto, operadora):
    - Se resposta_hardcoded não for None, envie-a direto e NÃO chame o RAG.
    - Se for None, plano_contexto já está pronto para a chamada do RAG.
    - operadora só vem preenchida para beneficiário confirmado (usada na busca).
    """
    if is_beneficiario(client):
        cpf = client.get("beneficiario_cpf")
        beneficiario = await find_by_cpf(cpf) if cpf else None
        if beneficiario:
            contexto = _plano_contexto(beneficiario)
            if client.get("resumo_financeiro"):
                contexto += "\n" + client["resumo_financeiro"]
            # Histórico só depois da identidade confirmada, e só do mesmo CPF
            anteriores = await previous_sessions_context(cpf, client.get("current_session_id"))
            if anteriores:
                contexto += "\n\n" + anteriores
            return None, contexto, beneficiario.get("operadora")
        return None, (
            f"Cliente identificado (CPF final {_mask_cpf(cpf or '')}), "
            "mas o cadastro do plano não foi encontrado no momento."
        ), None

    reply, contexto = await _advance_identification(client, message_body)
    return reply, contexto, None


async def _advance_identification(client: dict, message_body: str) -> tuple[str | None, str | None]:
    """Cliente ainda não confirmado: tenta localizar/confirmar sem virar barreira."""
    phone = client["phone"]
    status = client.get("identity_status")
    digits = _only_digits(message_body)
    sent_cpf = len(digits) == 11

    if status == "awaiting_confirmation" and client.get("pending_cpf"):
        if _is_affirmative(message_body):
            return await _confirm_identity(client), None
        if sent_cpf:
            return await _try_cpf(phone, digits)
        # Não confirmou nem mandou CPF: não insiste, segue atendendo como visitante
        await update_identity(phone, identity_status="visitor")
        return None, VISITOR_CONTEXT

    if status == "visitor":
        if sent_cpf:
            return await _try_cpf(phone, digits)
        return None, VISITOR_CONTEXT

    # Sem estado ainda (início da sessão): tenta pelo telefone, sem exigir nada
    beneficiario = await find_by_phone(phone)
    if beneficiario:
        await update_identity(
            phone,
            identity_status="awaiting_confirmation",
            pending_cpf=beneficiario["cpf"],
            pending_nome=beneficiario["nome_beneficiario"],
        )
        return (
            f"{_INTRO} Estou falando com {beneficiario['nome_beneficiario']}, CPF final "
            f"{_mask_cpf(beneficiario['cpf'])}? Se for você, é só responder 'sim'.",
            None,
        )

    if sent_cpf:
        return await _try_cpf(phone, digits)

    await update_identity(phone, identity_status="visitor")
    return None, VISITOR_CONTEXT
