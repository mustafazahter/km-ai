#!/usr/bin/env bash
# ai-bot etkileşimli kurulum sihirbazı: soruları sorar, .env üretir (izin 600), BRIDGE_TOKEN'ı yerelde üretir.
# Interactive setup wizard: asks a few questions, writes .env (mode 600), generates BRIDGE_TOKEN locally.
#
#   scripts/setup.sh            # .env yoksa oluşturur
#   scripts/setup.sh --force    # var olan .env'in üzerine yazar (önce .env.bak yedeği alır)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FORCE="${1:-}"
if [[ -f .env && "$FORCE" != "--force" ]]; then
  echo ".env zaten var; dokunmuyorum. Yeniden oluşturmak için: scripts/setup.sh --force"
  exit 0
fi
[[ -f .env ]] && cp .env .env.bak && echo "Eski .env -> .env.bak"

ask() {  # ask <değişken> <soru> <varsayılan> [secret]
  local __var="$1" __q="$2" __def="$3" __secret="${4:-}" __ans=""
  if [[ -n "$__secret" ]]; then
    if [[ -t 0 ]]; then read -r -s -p "$__q: " __ans; echo; else read -r __ans || true; fi
  else
    if [[ -t 0 ]]; then read -r -p "$__q [${__def}]: " __ans; else read -r __ans || true; fi
  fi
  __ans="${__ans:-$__def}"
  if [[ "$__ans" == *$'\n'* || "$__ans" == *" "* ]]; then
    echo "Geçersiz değer (boşluk/satır sonu olamaz): $__var" >&2; exit 1
  fi
  printf -v "$__var" '%s' "$__ans"
}

echo "== ai-bot kurulum sihirbazı =="
echo "Telegram bot tokenı: Telegram'da @BotFather -> /newbot (docs/SETUP.md)."
ask TG_TOKEN "Telegram bot tokenı (gizli yazılır)" "" secret
ask CHAT_IDS "İzinli sohbet kimlikleri, virgüllü (bilmiyorsan boş bırak, sonra bota /id yaz)" ""
ask LLM_URL "LLM adresi (OpenAI uyumlu)" "https://openrouter.ai/api/v1"
ask LLM_KEY "LLM API anahtarı (gizli yazılır; yerel sunucuda boş olabilir)" "" secret
ask LLM_MODEL "Model" "deepseek/deepseek-v4.1-flash-1017"
ask SEARCH "Arama sağlayıcısı (duckduckgo|tavily|brave|searxng)" "duckduckgo"
TAVILY=""; BRAVE=""; SEARX=""
case "$SEARCH" in
  tavily) ask TAVILY "Tavily API anahtarı (gizli yazılır)" "" secret ;;
  brave) ask BRAVE "Brave Search API anahtarı (gizli yazılır)" "" secret ;;
  searxng) ask SEARX "SearXNG adresi" "http://localhost:8888" ;;
  duckduckgo) ;;
  *) echo "Bilinmeyen sağlayıcı: $SEARCH" >&2; exit 1 ;;
esac
ask TZ_VALUE "Saat dilimi" "Europe/Istanbul"

BRIDGE=$(python3 -c "import secrets; print(secrets.token_urlsafe(36))")
UI_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
UI_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(36))")

umask 077
{
  echo "# scripts/setup.sh tarafından üretildi. Bu dosya git'e GİRMEZ (.gitignore)."
  echo "STACK_NAME=ai-bot"
  echo "LOG_LEVEL=info"
  echo "TZ=${TZ_VALUE}"
  echo "DATA_DIR=data"
  echo "PLUGINS_DIR=plugins"
  echo "HTTP_HOST=0.0.0.0"
  echo "HTTP_PORT=8080"
  echo "TELEGRAM_BOT_TOKEN=${TG_TOKEN}"
  echo "ALLOWED_CHAT_IDS=${CHAT_IDS}"
  echo "LLM_BASE_URL=${LLM_URL}"
  echo "LLM_API_KEY=${LLM_KEY}"
  echo "LLM_MODEL=${LLM_MODEL}"
  echo "MAX_TOOL_STEPS=6"
  echo "SEARCH_PROVIDER=${SEARCH}"
  echo "TAVILY_API_KEY=${TAVILY}"
  echo "BRAVE_API_KEY=${BRAVE}"
  echo "SEARXNG_URL=${SEARX}"
  echo "BRIDGE_TOKEN=${BRIDGE}"
  echo "# Yönetim arayüzü (/ui) parolası (verildiği için kurulum kodu adımı atlanır)."
  echo "UI_PASSWORD=${UI_PW}"
  echo "UI_SESSION_SECRET=${UI_SECRET}"
} > .env
chmod 600 .env

echo
echo ".env yazıldı (izin 600). BRIDGE_TOKEN yerelde üretildi; /bridge ve /mcp panelden (Entegrasyonlar) açılınca bu değerle de çalışır."
[[ -z "$TG_TOKEN" ]] && echo "Not: Telegram tokenı boş; bot kapalı açılır (köprü/MCP çalışır)."
[[ -z "$CHAT_IDS" ]] && echo "Not: ALLOWED_CHAT_IDS boş; /ui > Kurulum > Sohbet kimlikleri adımında bota yazan sohbeti tek tıkla ekle (ya da .env'e yazıp yeniden başlat)."
echo "Yönetim arayüzü: http://localhost:8080/ui/  (parola: .env içindeki UI_PASSWORD; kurulum sihirbazı orada)"
echo "Çalıştır:  docker compose up --build    ya da    PYTHONPATH=src:. python -m ai_bot"
