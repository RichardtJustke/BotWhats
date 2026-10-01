from datetime import datetime, timezone

from app.core.resources import get_db


async def get_or_create_client(phone: str, push_name: str | None = None) -> dict:
    client = await get_db().clients.find_one({"phone": phone})

    if client:
        return client

    new_client = {
        "phone": phone,
        "name": push_name,
        "created_at": datetime.now(timezone.utc),
    }
    await get_db().clients.insert_one(new_client)
    return new_client


# Campos do fluxo de identificação; zerados quando começa uma sessão nova
IDENTITY_FIELDS = (
    "identity_status", "pending_cpf", "pending_nome", "beneficiario_cpf", "resumo_financeiro",
)


async def update_identity(phone: str, **fields) -> None:
    await get_db().clients.update_one({"phone": phone}, {"$set": fields})