# chat-service — atendente virtual de WhatsApp para administradoras de benefícios

Bot de atendimento a beneficiários de planos de saúde pelo WhatsApp. Nome do
bot, empresa e contatos são configuráveis pelo `.env` (o padrão é a empresa
fictícia "Acme Benefícios"). Ele recebe as
mensagens pelo [OpenWA](https://github.com/rmyndharis/OpenWA), identifica o
cliente no cadastro de beneficiários, responde dúvidas com base na central de
ajuda (RAG), já mostra os boletos em aberto e passa a conversa para um
atendente humano quando o assunto exige (cancelamento, pedido de atendente,
ofensa, falta de informação segura).

## O que o bot faz

- **Identifica o cliente**: procura o telefone no MySQL de beneficiários e pede
  confirmação ("É você?"). Se não achar, atende como visitante e só pede o CPF
  quando a pessoa quer algo do próprio plano.
- **Mostra os boletos**: ao confirmar a identidade, consulta o `boleto-service`
  e, se houver boleto em aberto, já manda vencimento, valor, situação e link.
- **Responde dúvidas**: busca os trechos mais parecidos da base de
  conhecimento (Chroma) e gera a resposta com um LLM via OpenRouter, com
  checagens contra alucinação (ex: telefone que não está no contexto é barrado).
- **Transfere para humano**: por palavras-chave (cancelamento, carta de
  permanência, "falar com atendente", ofensa) ou quando o próprio LLM decide
  (marcação `[TRANSFERIR]`). Enquanto a conversa está com o humano o bot fica
  em silêncio.
- **Cancelamento fora do horário**: sem atendente disponível, orienta o cliente
  a pedir o cancelamento pelo app da empresa (ou por e-mail, no plano
  odontológico).
- **Controla a sessão**: pergunta "posso ajudar em algo mais?" depois de um
  tempo parado, encerra o atendimento e resume cada atendimento para dar
  contexto ao próximo do mesmo beneficiário.

## Fluxo de uma mensagem

```
OpenWA ──POST /webhook/openwa──▶ chat-service
  1. valida a assinatura HMAC e descarta evento repetido (idempotência)
  2. mensagem enviada pelo número da empresa? → registra se foi o atendente humano
  3. acha/cria o cliente (Mongo) e abre sessão nova se a anterior acabou
  4. conversa com humano?            → só registra, não responde
  5. easter egg ("gato", "pokemon")? → manda imagem
  6. "não" ao "posso ajudar em algo mais?" → encerra o atendimento
  7. palavra-chave de escalonamento  → encaminha pra humano
                                       (cancelamento fora do horário: passo a passo do app)
  8. identificação ainda em andamento → resposta fixa (pede confirmação/CPF,
                                       mostra boletos ao confirmar)
  9. senão: RAG + LLM → checagens (cancelamento, [TRANSFERIR], telefone inventado)
 10. envia a resposta pelo OpenWA e salva no histórico
```

Em paralelo, duas tarefas de fundo rodam desde o startup: `session_watcher`
(timeouts de sessão) e `summary_watcher` (resumos dos atendimentos encerrados).

## Estrutura

Cada pasta tem um README próprio com os detalhes.

```
chat-service/
├── app/                  código da API              → app/README.md
│   ├── main.py           FastAPI, startup e /health
│   ├── api/              webhook do OpenWA          → app/api/README.md
│   ├── core/             config, conexões, segurança → app/core/README.md
│   ├── models/           payloads (Pydantic)        → app/models/README.md
│   └── services/         regras de negócio          → app/services/README.md
├── knowledge_base/       artigos da base de conhecimento (.md) → knowledge_base/README.md
├── scripts/              ingestão, dev e deploy     → scripts/README.md
├── bancoFake/            MySQL de mentira pra dev   → bancoFake/README.md
├── chroma_data/          base vetorial gerada pelo ingest (não versionar)
├── Dockerfile / docker-entrypoint.sh
└── docker-compose.dev.yml   compose do OpenWA usado pelo deploy-openwa-chat.sh
```

## Dependências externas

| O quê | Pra quê |
|---|---|
| **OpenWA** | gateway do WhatsApp: entrega as mensagens no webhook e envia as respostas |
| **OpenRouter** | LLM (lista de modelos com fallback) e embeddings (`openai/text-embedding-3-small`) |
| **MongoDB** | clientes, sessões, mensagens, escalonamentos, resumos |
| **MySQL** | cadastro de beneficiários, tabela `beneficiarios` (só leitura) |
| **boleto-service** | serviço à parte que devolve as faturas em aberto por CPF (opcional) |
| **ChromaDB** | base vetorial local, em `chroma_data/` |

### Coleções do Mongo

| Coleção | Conteúdo |
|---|---|
| `clients` | um documento por telefone: estado da sessão e da identificação |
| `sessions` | um documento por atendimento: início, fim, motivo, escalonamento, resumo |
| `messages` | toda mensagem (cliente, bot, atendente), ligada à sessão |
| `escalations` | pedidos que foram para a fila humana (`status: pending`) |
| `processed_events` | chaves de idempotência dos eventos do webhook |

## Configuração

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `OPENWA_API_URL` | | API do OpenWA (padrão `http://localhost:2785/api`) |
| `OPENWA_API_KEY` | ✅ | chave de API do OpenWA |
| `OPENWA_WEBHOOK_SECRET` | | segredo HMAC do webhook (mín. 16 caracteres, exigência do OpenWA) |
| `OPENWA_WEBHOOK_EVENTS` | | eventos assinados (usado pelos scripts); inclua `message.sent` para registrar o atendente |
| `OPENROUTER_API_KEY` | ✅ | chave do OpenRouter |
| `OPENROUTER_MODELS` | | modelos em ordem de preferência, separados por vírgula |
| `MONGO_URI` / `MONGO_DB_NAME` | | Mongo (padrão `mongodb://localhost:27018` / `chat_service`) |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_DB` | | MySQL de beneficiários |
| `MYSQL_PASSWORD` | ✅ | senha do MySQL |
| `MYSQL_POOL_MIN` / `MYSQL_POOL_MAX` | | tamanho do pool de conexões |
| `SESSION_TIMEOUT_MINUTES` | | parado até perguntar "posso ajudar em algo mais?" (60) |
| `SESSION_CLOSE_MINUTES` | | sem resposta a essa pergunta até encerrar (10) |
| `ESCALATION_RELEASE_MINUTES` | | conversa com humano volta pro bot após esse tempo parada (120) |
| `ATENDIMENTO_DIAS` | | dias com atendente, `0`=segunda … `6`=domingo (`0,1,2,3,4`) |
| `ATENDIMENTO_INICIO` / `ATENDIMENTO_FIM` | | horário com atendente, fuso de Brasília (`08:00` / `18:00`) |
| `BOLETO_SERVICE_URL` | | boleto-service (padrão `http://boleto-service:3308`) |
| `BOLETO_SERVICE_API_KEY` | | mesmo `API_KEY` do boleto-service; vazio desliga a consulta de boletos |
| `BOT_NAME` / `COMPANY_NAME` | | nome do bot e da empresa (`Iza` / `Acme Benefícios`) |
| `COMPANY_APP_NAME` | | nome do app da empresa, citado no cancelamento fora do horário |
| `SUPPORT_EMAIL` / `OMBUDSMAN_EMAIL` | | e-mails de atendimento e ouvidoria |
| `SUPPORT_PHONE` / `SUPPORT_WHATSAPP` | | central e WhatsApp da empresa |
| `APP_STORE_URL` / `PLAY_STORE_URL` | | links do app nas lojas; vazios não aparecem |

As variáveis de identidade também preenchem os `{{placeholders}}` da
`knowledge_base/` na indexação: depois de mudá-las, rode o ingest de novo.

A API não sobe se faltar alguma obrigatória.

## Rodando localmente

Pré-requisitos: [uv](https://github.com/astral-sh/uv), Docker, `cloudflared` e
`jq`. O OpenWA (`openwa-api`) e o Mongo (`chatservice-mongo`) rodam em
containers.

```bash
uv sync
uv run python scripts/ingest.py     # indexa a base de conhecimento no Chroma
./scripts/dev.sh                    # sobe API + túnel e aponta o webhook do OpenWA pra ela
```

Só a API, sem túnel: `uv run uvicorn app.main:app --reload`.
Health check: `GET /health` (verifica Mongo e MySQL).

Sem acesso ao MySQL real, use o `bancoFake/` (ver o README de lá).

## Deploy

Em produção o chat-service roda como container, na rede `chat-network`
(a mesma do OpenWA e do boleto-service):

```bash
docker network create chat-network   # se ainda não existir
docker build -t chat-service .
docker run -d --name chat-service --network chat-network --env-file .env -p 8001:8000 chat-service
```

No `.env`, use os nomes dos containers em vez de `localhost`
(ex: `OPENWA_API_URL=http://openwa-api:2785/api`). Aponte `MONGO_URI` para a instância usada.

No boot, o container roda `scripts/ingest.py` antes de subir a API. Ele é
incremental (só reindexa o que mudou), sem um volume em `/app/chroma_data` um container recriado reindexa a base inteira.

Depois, cadastre o webhook do OpenWA apontando para o chat-service:

```bash
./scripts/register-webhook.sh
```

Isso exige o OpenWA na mesma rede (`docker network connect chat-network openwa-api`)
e `SSRF_ALLOWED_HOSTS=chat-service` no `.env` do OpenWA.

## Pendências conhecidas

- `scripts/deploy-openwa-chat.sh` sobe `chat-service` e `chat-mongo` por um
  `docker-compose.yml` dentro desta pasta, que ainda não existe.
- `scripts/server_tunnel.sh` e `scripts/server-tunnel.sh` são quase iguais;
  vale manter só um.
- `GEMINI_API_KEY` (config) e a dependência `google-genai` não são usadas.
- O horário de atendimento não considera feriados.
- O OpenWA é uma API não oficial do WhatsApp. Botões interativos e templates
  só existem na Cloud API oficial da Meta; avaliar a migração antes de escalar
  o uso com dados de saúde.
