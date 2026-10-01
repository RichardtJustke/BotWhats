# app/models/

Modelos Pydantic dos dados que chegam de fora.

| Arquivo | O que descreve |
|---|---|
| [`webhook_payload.py`](webhook_payload.py) | evento de mensagem do OpenWA: `WebhookPayload` (evento, sessão, `idempotencyKey`) com `data: MessageData` (remetente, `chatId`, texto, tipo, `fromMe`, contato) |

O campo `from` do JSON vira `from_` no Python (palavra reservada), via alias.

Se o OpenWA mudar o formato do payload, a validação falha e o webhook loga
`Payload não bateu com o modelo esperado` e ignora o evento. É esse log que
aparece quando uma mensagem "some".
