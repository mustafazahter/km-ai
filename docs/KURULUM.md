# Kurulum

> EN: Step-by-step install: pick a chat channel in the wizard (Telegram needs a bot token and your chat id from `/id`; WhatsApp, Teams, Slack and e-mail have their own guides), get an OpenRouter (or other) key,
> then either paste `komuta.yaml` into Komuta or run locally with docker compose / Python.

İçindekiler: [1. Telegram bot tokenı](#1-telegram-bot-tokenı) · [2. Sohbet kimliği](#2-sohbet-kimliği-id) · [3. LLM anahtarı](#3-llm-anahtarı) · [4. Komuta](#4-komutada-kurulum) · [5. Docker compose](#5-docker-compose-ile-yerel) · [6. Python ile yerel](#6-python-ile-yerel-geliştirme) · [Sorun giderme](#sorun-giderme)

## 1. Telegram bot tokenı

1. Telegram'da **@BotFather**'ı aç, `/newbot` yaz.
2. Bota bir görünen ad ve `_bot` ile biten bir kullanıcı adı ver.
3. BotFather'ın verdiği `123456789:AA...` biçimindeki metin bot tokenıdır. Kimseyle paylaşma; repoya yazma.
4. (İsteğe bağlı) Grupta kullanacaksan BotFather → `/setprivacy` → Disable, yoksa bot gruptaki düz mesajları görmez.

## 2. Sohbet kimliği (`/id`)

Bot yalnız `ALLOWED_CHAT_IDS` içindeki sohbetlere cevap verir. Liste boşken **yalnız `/id` ve `/start`** çalışır; bu sayede kimliğini güvenle öğrenirsin:

1. Botu `ALLOWED_CHAT_IDS` boş olarak başlat (Komuta'da Secret'a geçici olarak `0` girebilirsin).
2. Bota `/id` yaz → `Chat ID: 12345678` gibi bir cevap gelir. Gruplarda kimlik eksiyle başlar (`-100...`).
3. Bu değeri `ALLOWED_CHAT_IDS`'e yaz (birden fazlaysa virgülle), botu yeniden başlat.

Komuta'da Secret değerini değiştirmek için servis → ortam değişkenleri ekranını kullan; sonra yeniden dağıt.

## 3. LLM anahtarı

Varsayılan sağlayıcı **OpenRouter** (tek anahtarla birçok model):

1. openrouter.ai → hesap aç → Keys → **Create Key**.
2. Biraz kredi yükle veya ücretsiz modelleri dene (`LLM_MODEL` adını OpenRouter model listesinden al).
3. Araç çağırmayı (tool calling) destekleyen bir model seç; önerilen `deepseek/deepseek-v4.1-flash-1017` (ucuz, araç çağırır, 1M bağlam; sihirbaz bunu önerir, yerleşik varsayılan `openai/gpt-4o-mini`).

Başka sağlayıcı (OpenAI, Groq, DeepSeek, yerel Ollama...) için: [LLM-SAGLAYICILAR.md](LLM-SAGLAYICILAR.md).

## 4. Komuta'da kurulum

1. Bu repoyu GitHub'da **fork**'la (Komuta kaynaktan build eder).
2. Komuta panelinde bir **proje** oluştur (Stack proje yaratmaz) ve GitHub hesabını bağla (Git bağlantısı).
3. `komuta.yaml` içinde üç yer tutucuyu doldur: `<proje-adi>`, `<git-baglanti-adi>`, `<kullanici>` (fork'unun sahibi).
4. (İsteğe bağlı) Yerelde doğrula: `.venv/bin/python ../stack-spec/tools/validate_stack.py komuta.yaml`.
5. `console.komuta.io/stacks/new` → **Kaynak** → **komuta.yaml** kutusuna dosyayı yapıştır → **Planı incele**.
6. **Yayınla** (zorunlu Secret yok). Build bitince servis `/health` ile hazır olur.
7. Servis → **Loglar**: `Kurulum kodu` satırını bul. `https://<servis-adresi>/ui/` aç (önce Komuta girişi), kodla panel parolasını belirle ve sihirbazı izle: kanal seçimi, seçilen kanalın kurulumu (Telegram için token ve sohbet kimliği), yapay zekâ sağlayıcısı, arama ([ARAYUZ.md](ARAYUZ.md)). Secret ile vermek istersen `examples/komuta-env-ile.yaml`.

Paket: `dz-dev` (0.5 CPU / 1 GiB, aylık ~9 USD), disk `Small` (`/data`: SQLite). Opsiyonlu manifestler `examples/` altında:

| Dosya | Ne ekler |
|---|---|
| `examples/komuta-mt5-ile.yaml` | mt5-trader Stack'inin araçlarını AI'ya açar (köprü) |
| `examples/komuta-n8n-ile.yaml` | Olayları n8n'e gönderir; n8n `ai.ask` ile AI'ya soru sorar (köprü manifestten açık, Komuta girişi yok) |
| `examples/komuta-env-ile.yaml` | Bütün ayarlar Secret/ortamdan |
| `examples/komuta-giris-kapali.yaml` | Komuta girişi yok, yalnız panel parolası |
| `examples/komuta-arama-saglayicili.yaml` | Tavily ile güvenilir web araması |

## 5. Docker compose ile yerel

```bash
scripts/setup.sh            # soruları sorar, .env üretir (BRIDGE_TOKEN yerelde üretilir)
docker compose up --build
```

İmaj uid 1000 ile çalışır ve `/data` imajda 1000'e aittir; varsayılan named volume ek ayar istemez. Bind mount (`./data:/data`) kullanırsan önce `chown 1000:1000 ./data` ([GUVENLIK.md](GUVENLIK.md#5-konteyner-kullanıcısı-ve-disk-sahipliği)).

> Not: Docker imajı bu depoda henüz build edilerek denenmedi (yazım anında Docker servisi kapalıydı). Sorun görürsen issue aç.

## 6. Python ile yerel (geliştirme)

```bash
uv venv --python 3.12
uv pip install -r requirements-dev.txt
scripts/setup.sh
PYTHONPATH=src:. .venv/bin/python -m ai_bot
```

Anahtarsız deneme için sahte LLM:

```bash
.venv/bin/python scripts/fake_llm.py 9999
LLM_BASE_URL=http://127.0.0.1:9999/v1 BRIDGE_TOKEN=dev-token-change-me-0123456789 ALLOWED_CHAT_IDS=1 \
  HTTP_PORT=8091 PYTHONPATH=src:. .venv/bin/python -m ai_bot
curl -s -H "Authorization: Bearer dev-token-change-me-0123456789" -H 'Content-Type: application/json' \
  -d '{"args":{"prompt":"zar at"}}' http://127.0.0.1:8091/bridge/call/ai.ask
```

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Bot hiç cevap vermiyor | `TELEGRAM_BOT_TOKEN` doğru mu? Log'da "token reddedildi" var mı? Aynı token başka yerde polling yapıyorsa 409 görürsün; tek örnek çalıştır. |
| Yalnız `/id` cevap veriyor | Sohbet kimliğin `ALLOWED_CHAT_IDS`'de değil. |
| "AI servisine ulaşılamadı" | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` doğru mu; modelde kredi var mı? |
| Log'da `entrypoint: WARNING ... not writable` | `/data` uygulama kullanıcısına (uid 1000) yazılabilir değil. Yerel bind mount ise `chown 1000:1000 ./data`; uygulama disk sahipliğini kendisi değiştirmez. |
| web.search "bot protection" | DuckDuckGo bazı ağlardan otomatik isteği reddeder; `SEARCH_PROVIDER=tavily` veya `brave` kullan. |
| `/bridge` 401 | `Authorization: Bearer <BRIDGE_TOKEN>` başlığı eksik/yanlış. |
