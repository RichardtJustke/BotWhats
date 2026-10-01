#!/usr/bin/env bash
# Sobe o ambiente de dev do chat-service ponta a ponta:
#   1. garante Mongo do chat e OpenWA de pé
#   2. (re)inicia a API (uvicorn) — relendo o .env
#   3. abre um quick tunnel do cloudflared e captura a URL
#   4. aponta o webhook do OpenWA pra URL nova, com o secret do .env
#   5. confirma que o OpenWA gravou a URL e que a assinatura passa (teste → 200)
# Ctrl+C derruba API e túnel juntos.
#
# Uso: ./scripts/dev.sh            (a partir da raiz do projeto)
# Opcional no .env: OPENWA_SESSION_ID (senão usa a primeira sessão do OpenWA)
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
LOG_DIR="${TMPDIR:-/tmp}/chat-service-dev"
mkdir -p "$LOG_DIR"
CF_LOG="$LOG_DIR/cloudflared.log"

ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
info() { printf '\033[36m→ %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

set -a; source .env; set +a
: "${OPENWA_API_URL:?falta no .env}" "${OPENWA_API_KEY:?falta no .env}" "${OPENWA_WEBHOOK_SECRET:?falta no .env}"
[ "${#OPENWA_WEBHOOK_SECRET}" -ge 16 ] || die "OPENWA_WEBHOOK_SECRET precisa ter pelo menos 16 caracteres (exigência do OpenWA)"

retry() {  # retry TENTATIVAS INTERVALO cmd...
  local n=$1 d=$2; shift 2
  for _ in $(seq "$n"); do "$@" && return 0; sleep "$d"; done
  return 1
}

owa() {  # owa METHOD PATH [JSON]
  curl -sS -X "$1" "$OPENWA_API_URL$2" -H "X-API-Key: $OPENWA_API_KEY" \
    -H "Content-Type: application/json" ${3:+-d "$3"}
}

# ---------- 1. dependências ----------
info "Subindo containers (Mongo, MySQL fake e OpenWA)"
docker compose up -d chat-mongo chat-mysql >/dev/null
docker start openwa-api >/dev/null
retry 30 1 curl -sf -m 3 -o /dev/null "${OPENWA_API_URL%/api}/api/docs-json" \
  || die "OpenWA não respondeu em $OPENWA_API_URL"
ok "Containers de pé"

# ---------- 2. limpa execuções anteriores ----------
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null || true
sleep 1

cleanup() { info "Derrubando API e túnel"; kill "${API_PID:-}" "${CF_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ---------- 3. API ----------
info "Iniciando API na porta $PORT"
uv run uvicorn app.main:app --reload --port "$PORT" &
API_PID=$!
retry 60 1 curl -sf -m 3 -o /dev/null "localhost:$PORT/health" \
  || die "API não respondeu /health"
ok "API saudável: $(curl -s "localhost:$PORT/health")"

# ---------- 4. túnel ----------
info "Abrindo túnel cloudflared"
: > "$CF_LOG"
cloudflared tunnel --url "http://localhost:$PORT" >"$CF_LOG" 2>&1 &
CF_PID=$!
TUNNEL_URL=""
for _ in $(seq 30); do
  TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$CF_LOG" | head -1 || true)
  [ -n "$TUNNEL_URL" ] && break; sleep 1
done
[ -n "$TUNNEL_URL" ] || die "Não peguei a URL do túnel (veja $CF_LOG)"
WEBHOOK_URL="$TUNNEL_URL/webhook/openwa"
# Resolve via DoH da Cloudflare: o resolver local guarda o NXDOMAIN da primeira
# consulta (feita antes do subdomínio existir) e fica falhando por minutos.
retry 30 2 curl -sf -m 8 -o /dev/null --doh-url https://1.1.1.1/dns-query "$TUNNEL_URL/health" \
  || die "Túnel não ficou acessível: $TUNNEL_URL"
ok "Túnel no ar: $TUNNEL_URL"

# ---------- 5. webhook no OpenWA ----------
SESSION_ID="${OPENWA_SESSION_ID:-$(owa GET /sessions | jq -r '.[0].id // empty')}"
[ -n "$SESSION_ID" ] || die "Nenhuma sessão encontrada no OpenWA"

WEBHOOK_ID=$(owa GET "/sessions/$SESSION_ID/webhooks" | jq -r '.[0].id // empty')
EVENTS="${OPENWA_WEBHOOK_EVENTS:-message.received}"
BODY=$(jq -nc --arg url "$WEBHOOK_URL" --arg secret "$OPENWA_WEBHOOK_SECRET" --arg events "$EVENTS" \
  '{url: $url, secret: $secret, events: ($events | split(",") | map(gsub("^\\s+|\\s+$"; "")))}')
if [ -n "$WEBHOOK_ID" ]; then
  info "Atualizando webhook $WEBHOOK_ID"
  owa PUT "/sessions/$SESSION_ID/webhooks/$WEBHOOK_ID" "$BODY" >/dev/null
else
  info "Nenhum webhook na sessão, criando"
  WEBHOOK_ID=$(owa POST "/sessions/$SESSION_ID/webhooks" "$BODY" | jq -r '.id')
fi

SAVED_URL=$(owa GET "/sessions/$SESSION_ID/webhooks/$WEBHOOK_ID" | jq -r '.url')
[ "$SAVED_URL" = "$WEBHOOK_URL" ] || die "OpenWA ficou com URL diferente: $SAVED_URL"
ok "OpenWA aponta para $SAVED_URL"

# ---------- 6. teste de assinatura ----------
# O DNS dentro do container do OpenWA também pode demorar a enxergar o subdomínio novo
for _ in $(seq 10); do
  TEST=$(owa POST "/sessions/$SESSION_ID/webhooks/$WEBHOOK_ID/test")
  STATUS=$(jq -r '.statusCode // "sem resposta"' <<<"$TEST")
  [ "$STATUS" != "sem resposta" ] && break
  info "OpenWA ainda não alcança o túnel, tentando de novo..."; sleep 3
done
case "$STATUS" in
  2*)  ok "Teste de entrega OK ($STATUS) — assinatura aceita" ;;
  401) die "API recusou a assinatura (401): secret do OpenWA ≠ OPENWA_WEBHOOK_SECRET" ;;
  *)   die "Teste de entrega falhou: $TEST" ;;
esac

printf '\n\033[1;32mTudo pronto! Manda um "oi" no WhatsApp.\033[0m  (Ctrl+C para parar)\n\n'
wait "$API_PID"
