#!/usr/bin/env bash
# Túnel rápido do cloudflared pro chat-service no servidor + cadastro automático
# do webhook no OpenWA. A URL do trycloudflare muda a cada início, então este
# script sempre recadastra. Feito pra rodar como serviço systemd (reinicia
# sozinho se o túnel cair, e sobe junto com o servidor).
#
# Uso manual (a partir de services/chat-service):
#   ./scripts/server-tunnel.sh
#
# Variáveis opcionais:
#   CHAT_SERVICE_URL  onde o chat-service responde no host (padrão http://localhost:3307)
#   METRICS_ADDR      endereço de métricas do cloudflared (padrão 127.0.0.1:20241)
#   + as de scripts/register-webhook.sh (OPENWA_HOST_URL, OPENWA_SESSION_ID)
set -euo pipefail

cd "$(dirname "$0")/.."
CHAT_SERVICE_URL="${CHAT_SERVICE_URL:-http://localhost:8001}"
METRICS_ADDR="${METRICS_ADDR:-127.0.0.1:20241}"

info() { printf '→ %s\n' "$*"; }
die()  { printf '✘ %s\n' "$*" >&2; exit 1; }

command -v cloudflared >/dev/null || die "cloudflared não instalado"
command -v jq >/dev/null || die "Precisa do jq instalado"

cloudflared tunnel --no-autoupdate --metrics "$METRICS_ADDR" --url "$CHAT_SERVICE_URL" &
CF_PID=$!
trap 'kill "$CF_PID" 2>/dev/null || true' EXIT INT TERM

# O cloudflared expõe o hostname do quick tunnel no servidor de métricas
HOST=""
for _ in $(seq 60); do
  kill -0 "$CF_PID" 2>/dev/null || die "cloudflared morreu antes de gerar a URL"
  HOST=$(curl -sf -m 2 "http://$METRICS_ADDR/quicktunnel" | jq -r '.hostname // empty' || true)
  [ -n "$HOST" ] && break
  sleep 1
done
[ -n "$HOST" ] || die "Não consegui pegar a URL do túnel"
info "Túnel no ar: https://$HOST"

# O subdomínio novo pode levar alguns segundos pra propagar no DNS; tenta de novo
registered=""
for i in $(seq 12); do
  if ./scripts/register-webhook.sh "https://$HOST/webhook/openwa"; then
    registered=1; break
  fi
  info "Cadastro falhou (tentativa $i), tentando de novo em 10s..."
  sleep 10
done
[ -n "$registered" ] || die "Não consegui cadastrar o webhook"

# Fica preso ao cloudflared: se ele cair, o script sai e o systemd reinicia tudo
wait "$CF_PID"

