# scripts/

Rodar sempre a partir da raiz do projeto. Os scripts shell leem o
`.env` e precisam de `curl` e `jq`.

| Script | Quando usar |
|---|---|
| [`ingest.py`](ingest.py) | indexa a `knowledge_base/` no Chroma (`chroma_data/`). |
| [`dev.sh`](dev.sh) | ambiente de dev completo na sua máquina. |
| [`register-webhook.sh`](register-webhook.sh) | cadastrar ou atualizar o webhook do OpenWA no servidor. |
| [`server-tunnel.sh`](server-tunnel.sh) | expor o chat-service do servidor por túnel cloudflared (serviço systemd). |
| [`server_tunnel.sh`](server_tunnel.sh) | versão antiga do anterior; não lê `CHAT_SERVICE_URL` do `.env`. |
| [`deploy-openwa-chat.sh`](deploy-openwa-chat.sh) | deploy do zero de OpenWA + chat-service (ver aviso abaixo). |

## ingest.py

```bash
uv run python scripts/ingest.py
```

- Quebra cada `.md` nas seções `## ` e ignora trechos com menos de 30 caracteres.
- Gera o embedding pelo OpenRouter (`openai/text-embedding-3-small`), com
  nova tentativa em 429/5xx.
- É incremental: guarda o hash de cada trecho e só reindexa o que mudou.
  Trecho de arquivo apagado sai do índice.
- O metadado `tema` é o nome da pasta do artigo (ex: `boletos`). O RAG
  exclui o tema `cancelamento`.
- Roda sozinho no boot do container (`docker-entrypoint.sh`); se falhar, a
  API sobe com o índice que já existir.

## dev.sh

```bash
./scripts/dev.sh
```

1. sobe `chat-mongo` e `chat-mysql` (compose) e o container `openwa-api`;
2. (re)inicia o uvicorn com `--reload` na porta `PORT` (8000);
3. abre um túnel `cloudflared` e pega a URL `trycloudflare.com`;
4. aponta o webhook do OpenWA para essa URL e testa a entrega.

`Ctrl+C` derruba API e túnel. A URL do túnel muda a cada execução, por isso
o script recadastra o webhook sempre.

## register-webhook.sh

```bash
./scripts/register-webhook.sh                                    # http://chat-service:8000/webhook/openwa
./scripts/register-webhook.sh https://meu.dominio/webhook/openwa # URL própria
```

Webhook pela rede Docker exige `SSRF_ALLOWED_HOSTS=chat-service` no `.env`
do OpenWA e os dois containers na rede `chat-network`. Variáveis opcionais:
`OPENWA_HOST_URL` e `OPENWA_SESSION_ID`.

## deploy-openwa-chat.sh

Clona o OpenWA, gera os segredos, sobe o OpenWA com o
`docker-compose.dev.yml` desta pasta, conecta as redes, pareia a sessão do
WhatsApp por código e cadastra o webhook. É idempotente.

