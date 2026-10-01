from pydantic import BaseModel, Field


class Contact(BaseModel):
    pushName: str | None = None


class MessageData(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    chatId: str
    body: str
    type: str
    timestamp: int
    fromMe: bool
    isGroup: bool
    kind: str
    isLidSender: bool
    contact: Contact

    model_config = {"populate_by_name": True}


class WebhookPayload(BaseModel):
    event: str
    timestamp: str
    sessionId: str
    idempotencyKey: str
    deliveryId: str
    data: MessageData