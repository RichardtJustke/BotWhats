# app/api/

Rotas HTTP do chat-service.

## `POST /webhook/openwa` — [webhook.py](webhook.py)

Recebe os eventos do OpenWA e orquestra a resposta. É o ponto de entrada de
toda mensagem.

1. **Assinatura**: valida o header `X-OpenWA-Signature` (HMAC-SHA256 do corpo
   com `OPENWA_WEBHOOK_SECRET`). Inválida → `401`.
2. **Filtro de evento**: aceita `message.received` e mensagens `fromMe`
   (enviadas pelo número da empresa). Payload fora do formato → ignorado.
3. **Idempotência**: o OpenWA pode reentregar o mesmo evento; o
   `idempotencyKey` é gravado com índice único e repetidos são descartados.
4. **Mensagem da empresa (`fromMe`)**: se não for eco do próprio bot, é o
   atendente humano digitando. A mensagem vai para o histórico e a conversa
   passa para o humano (`_handle_outgoing`).
5. **Mensagem do cliente**, nesta ordem:
   - resolve o telefone real quando o remetente vem como `@lid`;
   - acha/cria o cliente e abre sessão nova se a anterior acabou;
   - conversa com humano → só registra, não responde;
   - easter egg → envia imagem;
   - "não" ao "posso ajudar em algo mais?" → encerra o atendimento;
   - palavra-chave de escalonamento → `_escalate` (fora do horário,
     cancelamento recebe o passo a passo do app em vez de ir para a fila);
   - identificação em andamento → resposta fixa do fluxo de identificação;
   - senão → RAG + LLM, depois as checagens: resposta falando de
     cancelamento vira escalonamento e `[TRANSFERIR]` manda para a fila humana.
6. Envia a resposta pelo OpenWA.

A rota sempre devolve `200` com um `status` (`ok`, `ignored`,
`duplicate_ignored`, `escalated_silenced`, `error`…), menos na assinatura
inválida. Assim o OpenWA não fica reenviando eventos que já foram tratados.
