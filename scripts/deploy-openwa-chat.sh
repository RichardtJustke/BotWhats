#!/usr/bin/env bash
# Deploy ponta a ponta do OpenWA + chat-service no servidor:
#   1. clona (ou atualiza) o OpenWA em ./openwa
#   2. garante a rede compartilhada `chat-network`
#   3. gera os segredos do OpenWA (API key mestre) e sobe o stack dele
#   4. conecta o openwa-api na rede compartilhada + libera o SSRF allowlist
#   5. preenche o .env do chat-service com os segredos gerados e sobe
#      chat-service + chat-mongo + chat-mysql (docker-compose.yml da raiz)
#   6. cria/pareia uma sessão do WhatsApp (código de pareamento por telefone)
#   7. cadastra o webhook do OpenWA apontando pro chat-service
#
# Idempotente: pode rodar de novo a qualquer momento (não sobrescreve
# segredos já preenchidos, só completa o que falta).
#
# Uso (de qualquer lugar):
#   ./scripts/deploy-openwa-chat.sh
#
# Variáveis opcionais:
#   OPENWA_DIR           pasta onde clonar o OpenWA (padrão: ./openwa)
#   OPENWA_REPO          URL do repo do OpenWA (padrão: github.com/rmyndharis/OpenWA)
#   OPENWA_PHONE_NUMBER  telefone p/ pareamento, só dígitos com DDI (ex: 5511999999999)
#                        se não for passado, o script pergunta interativamente
set -euo pipefail

CHAT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

OPENWA_DIR="${OPENWA_DIR:-$CHAT_DIR/openwa}"
OPENWA_REPO="${OPENWA_REPO:-https://github.com/rmyndharis/OpenWA.git}"
NETWORK="chat-network"

ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
info() { printf '\033[36m→ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

for bin in git docker curl jq openssl; do
  command -v "$bin" >/dev/null || die "Precisa do '$bin' instalado"
done
docker compose version >/dev/null 2>&1 || die "Precisa do plugin 'docker compose' (v2)"

# set_env_var FILE KEY VALUE [PLACEHOLDERS...]
# Só grava se a chave não existir ou estiver vazia/placeholder — não pisa em
# valor real já configurado à mão.
set_env_var() {
  local file="$1" key="$2" value="$3"; shift 3
  local placeholders=("$@")
  local current=""
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    current="$(grep "^${key}=" "$file" | head -1 | cut -d= -f2-)"
  fi
  local is_placeholder=0
  [ -z "$current" ] && is_placeholder=1
  for p in "${placeholders[@]:-}"; do
    [ "$current" = "$p" ] && is_placeholder=1
  done
  if [ -z "$current" ] || [ "$is_placeholder" = "1" ]; then
    if grep -q "^${key}=" "$file" 2>/dev/null; then
      sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
      printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
  fi
}

get_env_var() {
  local file="$1" key="$2"
  grep -q "^${key}=" "$file" 2>/dev/null && grep "^${key}=" "$file" | head -1 | cut -d= -f2- || true
}

# ---------- 1. clona/atualiza o OpenWA ----------
if [ -d "$OPENWA_DIR/.git" ]; then
  info "OpenWA já clonado em $OPENWA_DIR, atualizando"
  git -C "$OPENWA_DIR" pull --ff-only
else
  info "Clonando OpenWA em $OPENWA_DIR"
  git clone "$OPENWA_REPO" "$OPENWA_DIR"
fi
ok "Repositório OpenWA pronto"

# ---------- 2. rede compartilhada ----------
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  info "Criando rede $NETWORK"
  docker network create "$NETWORK" >/dev/null
fi
ok "Rede $NETWORK disponível"

# ---------- 3. compose customizado + .env do OpenWA + sobe o stack ----------
# usamos o docker-compose.dev.yml (container único, SQLite, bind mount em
# ./data) com as modificações feitas aqui no chat-service — sobrescreve o
# arquivo padrão do repo clonado, sempre, pra manter as duas cópias em sync.
cp "$CHAT_DIR/docker-compose.dev.yml" "$OPENWA_DIR/docker-compose.dev.yml"

cd "$OPENWA_DIR"
[ -f .env ] || cp .env.example .env

OPENWA_MASTER_KEY="$(get_env_var .env API_MASTER_KEY)"
if [ -z "$OPENWA_MASTER_KEY" ]; then
  OPENWA_MASTER_KEY="$(openssl rand -hex 32)"
fi
set_env_var .env API_MASTER_KEY "$OPENWA_MASTER_KEY"
set_env_var .env SSRF_ALLOWED_HOSTS "chat-service"
ok "Segredos do OpenWA prontos (.env)"

info "Subindo OpenWA (build + up)"
docker compose -f docker-compose.dev.yml up -d --build

info "Aguardando openwa-api ficar saudável"
for _ in $(seq 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' openwa-api 2>/dev/null || echo starting)"
  [ "$status" = "healthy" ] && break
  sleep 2
done
[ "$status" = "healthy" ] || die "openwa-api não ficou saudável a tempo (docker compose -f docker-compose.dev.yml logs openwa)"
ok "OpenWA no ar"

# ---------- 4. conecta na rede compartilhada ----------
if ! docker network inspect "$NETWORK" -f '{{range .Containers}}{{.Name}} {{end}}' | grep -qw openwa-api; then
  info "Conectando openwa-api na rede $NETWORK"
  docker network connect "$NETWORK" openwa-api
fi
ok "openwa-api acessível pela rede $NETWORK"

cd "$CHAT_DIR"

# ---------- 5. .env do chat-service + sobe chat-service/chat-mongo ----------
[ -f "$CHAT_DIR/.env" ] || cp "$CHAT_DIR/.env.example" "$CHAT_DIR/.env"

WEBHOOK_SECRET="$(get_env_var "$CHAT_DIR/.env" OPENWA_WEBHOOK_SECRET)"
if [ -z "$WEBHOOK_SECRET" ] || [ "$WEBHOOK_SECRET" = "dev-secret" ]; then
  WEBHOOK_SECRET="$(openssl rand -hex 24)"
fi
set_env_var "$CHAT_DIR/.env" OPENWA_WEBHOOK_SECRET "$WEBHOOK_SECRET" "dev-secret"
# os dois abaixo trazem um default de dev não-vazio no .env.example (localhost) —
# passa esse valor como "placeholder" pra sobrescrever com o nome do container
set_env_var "$CHAT_DIR/.env" OPENWA_API_URL "http://openwa-api:2785/api" "http://localhost:2785/api"
set_env_var "$CHAT_DIR/.env" OPENWA_API_KEY "$OPENWA_MASTER_KEY"
set_env_var "$CHAT_DIR/.env" MONGO_URI "mongodb://chat-mongo:27017" "mongodb://localhost:27018"
set_env_var "$CHAT_DIR/.env" MONGO_DB_NAME "chat_service"

missing=()
needs_attention() {  # KEY DEV_DEFAULT
  local v; v="$(get_env_var "$CHAT_DIR/.env" "$1")"
  [ -z "$v" ] || [ "$v" = "$2" ]
}
needs_attention OPENROUTER_API_KEY "" && missing+=(OPENROUTER_API_KEY)
# MySQL: sem configuração própria, usa o chat-mysql (bancoFake) do compose
set_env_var "$CHAT_DIR/.env" MYSQL_HOST "chat-mysql" "localhost"
set_env_var "$CHAT_DIR/.env" MYSQL_PASSWORD "chatservice"
if [ "${#missing[@]}" -gt 0 ]; then
  if [ -t 0 ]; then
    warn "Faltam segredos que eu não posso inventar — preenche agora (Enter pula e deixa em branco):"
    for key in "${missing[@]}"; do
      if [ "$key" = "MYSQL_PASSWORD" ]; then
        read -r -s -p "  $key: " val; echo
      else
        read -r -p "  $key: " val
      fi
      [ -n "$val" ] && set_env_var "$CHAT_DIR/.env" "$key" "$val"
    done
  else
    warn "Faltam no $CHAT_DIR/.env: ${missing[*]} — preenche à mão e roda o script de novo"
  fi
fi

info "Subindo chat-service + chat-mongo + chat-mysql"
docker compose up -d --build

info "Aguardando chat-service ficar saudável"
for _ in $(seq 60); do
  curl -sf -m 3 -o /dev/null http://localhost:8001/health && break
  sleep 2
done
curl -sf -m 3 -o /dev/null http://localhost:8001/health \
  || die "chat-service não respondeu em /health (docker compose logs chat-service)"
ok "chat-service no ar em :8001"

# ---------- 6. cria/pareia sessão do WhatsApp ----------
owa() { curl -sS -m 30 -X "$1" "http://localhost:2785/api$2" -H "X-API-Key: $OPENWA_MASTER_KEY" -H "Content-Type: application/json" ${3:+-d "$3"}; }

SESSION_ID="$(owa GET /sessions | jq -r '.[0].id // empty')"
if [ -z "$SESSION_ID" ]; then
  info "Nenhuma sessão no OpenWA, criando"
  SESSION_ID="$(owa POST /sessions '{"name":"chat-service"}' | jq -r '.id')"
  owa POST "/sessions/$SESSION_ID/start" >/dev/null
fi

STATUS="$(owa GET "/sessions/$SESSION_ID" | jq -r '.status')"
if [ "$STATUS" != "ready" ]; then
  info "Aguardando sessão ficar pronta pra parear (qr_ready)"
  for _ in $(seq 30); do
    STATUS="$(owa GET "/sessions/$SESSION_ID" | jq -r '.status')"
    [ "$STATUS" = "qr_ready" ] && break
    sleep 2
  done
  if [ "$STATUS" = "qr_ready" ]; then
    PHONE="${OPENWA_PHONE_NUMBER:-}"
    if [ -z "$PHONE" ] && [ -t 0 ]; then
      read -r -p "Número de WhatsApp p/ parear (DDI+DDD+número, só dígitos, ex 5511999999999): " PHONE
    fi
    if [ -n "$PHONE" ]; then
      CODE="$(owa POST "/sessions/$SESSION_ID/pairing-code" "{\"phoneNumber\":\"$PHONE\"}" | jq -r '.pairingCode // empty')"
      if [ -n "$CODE" ]; then
        printf '\n\033[1;33m→ No celular: WhatsApp > Configurações > Aparelhos conectados > Conectar com número > digite: %s\033[0m\n\n' "$CODE"
        info "Aguardando pareamento (até 2 min)"
        for _ in $(seq 60); do
          STATUS="$(owa GET "/sessions/$SESSION_ID" | jq -r '.status')"
          [ "$STATUS" = "ready" ] && break
          sleep 2
        done
      else
        warn "Não consegui gerar o código de pareamento — pareia manualmente pelo dashboard (http://localhost:2785)"
      fi
    else
      warn "Sem telefone informado — pareia manualmente pelo dashboard (http://localhost:2785) antes de mandar mensagem"
    fi
  fi
fi
[ "$STATUS" = "ready" ] && ok "Sessão do WhatsApp pareada e pronta" || warn "Sessão ainda não está 'ready' (status atual: $STATUS) — confira o dashboard"

# ---------- 7. cadastra o webhook ----------
info "Cadastrando webhook do OpenWA -> chat-service"
( cd "$CHAT_DIR" && OPENWA_HOST_URL="http://localhost:2785/api" OPENWA_SESSION_ID="$SESSION_ID" ./scripts/register-webhook.sh )

printf '\n\033[1;32mDeploy completo.\033[0m\n'
printf '  OpenWA:       http://localhost:2785  (dashboard)\n'
printf '  chat-service: http://localhost:8001/health\n'
if [ "${#missing[@]}" -gt 0 ]; then
  warn "Lembra de conferir/preencher em $CHAT_DIR/.env: ${missing[*]}, depois: docker compose up -d --build chat-service"
fi
exit 0
