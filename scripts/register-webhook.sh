#!/usr/bin/env bash
# Cadastra/atualiza o webhook do OpenWA apontando pro chat-service, com o
# secret do .env, e confirma com um teste de entrega. Pra servidor/Docker:
# não sobe API nem túnel (pra dev local com cloudflared, use scripts/dev.sh).
#
# Uso (a partir da raiz do projeto, no host onde o OpenWA roda):
#   ./scripts/register-webhook.sh                       # URL padrão (rede Docker)
#   ./scripts/register-webhook.sh https://meu.dominio/webhook/openwa
#
# Variáveis opcionais:
#   OPENWA_HOST_URL  API do OpenWA vista do host (padrão http://localhost:2785/api;
#                    o OPENWA_API_URL do .env usa o nome do container, que o host
#                    não resolve)
#   OPENWA_SESSION_ID  sessão do WhatsApp (padrão: a primeira do OpenWA)
#
# Webhook por rede interna (http://chat-service:8000/...) exige no .env do OpenWA:
#   SSRF_ALLOWED_HOSTS=chat-service
# e os dois containers na mesma rede: docker network connect chat-network openwa-api
set -euo pipefail

cd "$(dirname "$0")/.."
WEBHOOK_URL="${1:-http://chat-service:8000/webhook/openwa}"
OWA_URL="${OPENWA_HOST_URL:-http://localhost:2785/api}"

ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
info() { printf '\033[36m→ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

command -v jq >/dev/null || die "Precisa do jq instalado"
set -a; source .env; set +a
: "${OPENWA_API_KEY:?falta no .env}" "${OPENWA_WEBHOOK_SECRET:?falta no .env}"
[ "${#OPENWA_WEBHOOK_SECRET}" -ge 16 ] || die "OPENWA_WEBHOOK_SECRET precisa ter pelo menos 16 caracteres"

owa() {  # owa METHOD PATH [JSON]
  curl -sS -m 30 -X "$1" "$OWA_URL$2" -H "X-API-Key: $OPENWA_API_KEY" \
    -H "Content-Type: application/json" ${3:+-d "$3"}
}

curl -sf -m 5 -o /dev/null "${OWA_URL%/api}/api/docs-json" || die "OpenWA não responde em $OWA_URL"

SESSION_ID="${OPENWA_SESSION_ID:-$(owa GET /sessions | jq -r '.[0].id // empty')}"
[ -n "$SESSION_ID" ] || die "Nenhuma sessão encontrada no OpenWA (pareou o WhatsApp?)"
info "Sessão $SESSION_ID"

WEBHOOKS=$(owa GET "/sessions/$SESSION_ID/webhooks")
if [ "$(jq length <<<"$WEBHOOKS")" -gt 1 ]; then
  warn "Há mais de um webhook nessa sessão; os outros continuam recebendo eventos:"
  jq -r '.[] | "    \(.id)  \(.url)"' <<<"$WEBHOOKS"
fi

# OPENWA_WEBHOOK_EVENTS no .env (separado por vírgula) inclui o evento de
# mensagem enviada, pra registrar o que o atendente humano digita
EVENTS="${OPENWA_WEBHOOK_EVENTS:-message.received}"
BODY=$(jq -nc --arg url "$WEBHOOK_URL" --arg secret "$OPENWA_WEBHOOK_SECRET" --arg events "$EVENTS" \
  '{url: $url, secret: $secret, events: ($events | split(",") | map(gsub("^\\s+|\\s+$"; "")))}')
# Reaproveita um webhook que já aponte pro chat-service; senão o primeiro; senão cria
WEBHOOK_ID=$(jq -r --arg url "$WEBHOOK_URL" '(map(select(.url == $url)) + .)[0].id // empty' <<<"$WEBHOOKS")
if [ -n "$WEBHOOK_ID" ]; then
  info "Atualizando webhook $WEBHOOK_ID"
  owa PUT "/sessions/$SESSION_ID/webhooks/$WEBHOOK_ID" "$BODY" >/dev/null
else
  info "Criando webhook"
  WEBHOOK_ID=$(owa POST "/sessions/$SESSION_ID/webhooks" "$BODY" | jq -r '.id // empty')
  [ -n "$WEBHOOK_ID" ] || die "OpenWA não criou o webhook"
fi

SAVED_URL=$(owa GET "/sessions/$SESSION_ID/webhooks/$WEBHOOK_ID" | jq -r '.url')
[ "$SAVED_URL" = "$WEBHOOK_URL" ] || die "OpenWA ficou com URL diferente: $SAVED_URL"
ok "Webhook $WEBHOOK_ID → $SAVED_URL"

TEST=$(owa POST "/sessions/$SESSION_ID/webhooks/$WEBHOOK_ID/test")
STATUS=$(jq -r '.statusCode // "sem resposta"' <<<"$TEST")
case "$STATUS" in
  2*)  ok "Teste de entrega OK ($STATUS), assinatura aceita. Pode mandar mensagem!" ;;
  401) die "chat-service recusou a assinatura (401): secret do webhook ≠ OPENWA_WEBHOOK_SECRET do container (recriou o container depois de mudar o .env?)" ;;
  502|503|504|530) die "O destino não respondeu ($STATUS). Com túnel: o cloudflared não alcança o chat-service (container fora do ar ou porta errada — teste curl localhost:3307/health)" ;;
  *)   die "Teste de entrega falhou: $TEST
  Se for bloqueio SSRF/host interno: SSRF_ALLOWED_HOSTS=chat-service no .env do OpenWA
  Se não resolver o host: docker network connect chat-network openwa-api" ;;
esac
