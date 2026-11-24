[English](../BRIDGES.md) · Türkçe

# Köprüler: mt5-trader, n8n, MCP

> EN: Connect ai-bot to other stacks (their tools become AI tools; trade tools always need a Telegram button press),
> to n8n (events out, `ai.ask` in) and to MCP clients (Claude Desktop etc.).

## Güvenlik kuralı (değişmez)

AI başka bir stack'in aracını çağırdığında:

- Araç `needs_confirmation` dönerse bot Telegram'da **önizleme + Onayla/İptal** düğmesi gösterir; yalnız sen **Onayla**'ya basarsan aynı argümanlarla ve tek kullanımlık `confirm_id` ile ikinci çağrı yapılır.
- `confirm_id` modele hiç gösterilmez; model `confirmed`/`confirm_id` gönderse bile atılır.
- `scope: trade` veya `admin` olan araç, `confirm` bayrağı olmasa bile önce düğmeyle onay ister.
- Düğmeye yalnız aynı (izinli) sohbetten basılabilir; 120 sn içinde basılmazsa işlem yapılmaz.
- Onay kanalı yoksa (ör. `ai.ask` köprüden çağrıldı ve hiçbir kanal onay veremiyor) bu araçlar **çalışmaz**.

Bu davranış `tests/test_confirm_flow.py` ile test edilir.

## mt5-trader ile

### Ayrı iki Stack (köprü)

`examples/komuta-mt5-ile.yaml`:

```yaml
- name: BRIDGE_MT5_URL
  valueFrom:
    service:
      stack: mt5-trader        # mt5-trader Stack'inin adı
      name: mt5-trader         # o Stack'teki servis adı
      property: url
- name: BRIDGE_MT5_TOKEN
  sensitive: true
  valueFrom:
    secret: mt5-bridge-token   # mt5-trader'ın BRIDGE_TOKEN değeri (prompt Secret)
```

- Hedef servis `network.public: true` **olmalı**; `valueFrom.service` yalnız public servislere bağlanır (plan `SERVICE_REFERENCE_NOT_PUBLIC` verir).
- Önce mt5-trader'ı yayınla, sonra ai-bot'u planla.
- Yerelde: `.env`'e `BRIDGE_MT5_URL=https://...` ve `BRIDGE_MT5_TOKEN=...`.

Sonra Telegram'da: "Altının fiyatı ne?" (okuma aracı, onaysız), "0.01 lot altın al" (onay düğmesi gelir).

### Tek servis (birleşik)

mt5-trader kendi sürecinde `ai_bot.setup(stack, bot)` çağırır; araçlar köprüsüz aynı kayıtta durur, onay kuralı aynıdır. Kurulumu mt5-trader reposu anlatır; imza: [DEVELOPER.md](DEVELOPER.md#başka-bir-stacke-gömme-ai_botsetup).

## n8n ile

`examples/komuta-n8n-ile.yaml`.

**ai-bot → n8n (olaylar):** `WEBHOOKS_OUT_URL` (n8n Webhook düğümü adresi) + `WEBHOOKS_OUT_SECRET`. Olaylar: `ai.reply`, `schedule.fired`, `memory.remembered`, `memory.forgotten`. Başlıklar: `X-Stack-Event`, `X-Stack-Timestamp`, `X-Stack-Signature: sha256=<HMAC(secret, timestamp + "." + gövde)>`. n8n'de Code düğümüyle imzayı doğrula (kit belgesi `stack-kit/docs/RECIPES.md`).

**n8n → ai-bot (soru):** HTTP Request düğümü:

```
POST https://<ai-bot-adresi>/bridge/call/ai.ask
Authorization: Bearer <BRIDGE_TOKEN>
Content-Type: application/json

{"args": {"prompt": "Bugünün döviz kurlarını özetle", "notify": true}}
```

Cevap `{"ok": true, "result": {"answer": "...", "chat_id": ...}}`. `notify: true` cevabı Telegram'a da yollar. Diğer araçlar da aynı biçimle çağrılır (`/bridge/call/memory.remember`, `/bridge/call/schedule.add` ...). Araç listesi: `GET /bridge/manifest` (token ister).

**n8n akışını AI aracı yapmak:** n8n'de bir Webhook tetikleyicili akış kur ve onu küçük bir plugin ile sar (plugin içinde `app.fetcher` public adresler için çalışır; n8n özel ağdaysa kendi `httpx` istemcini kullan ve adresi env'den al).

## MCP istemcileri (Claude Desktop vb.)

ai-bot'un tüm araçları `POST /mcp` (MCP Streamable HTTP, JSON) üzerinden açıktır: `web_search`, `web_read`, `memory_*`, `schedule_*`, `ai_ask`, plugin araçları. `Authorization: Bearer <BRIDGE_TOKEN>` şart. Onaylı araçlar MCP'de her zaman iki adımlıdır. İstemci yapılandırması: `.claude/skills/add-mcp-client`.

```bash
curl -s -H "Authorization: Bearer $BRIDGE_TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:8091/mcp
```
