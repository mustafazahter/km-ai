[English](../LLM-PROVIDERS.md) · Türkçe

# LLM sağlayıcıları

> EN: Pick a provider in the panel (Kurulum > Yapay zekâ modeli) or with `LLM_PROVIDER`. Every provider except
> Anthropic is reached through its OpenAI compatible `/chat/completions`; Anthropic uses a native Messages API
> adapter (tool_use/tool_result). "Bağlan" fetches the provider's model list. Base URLs are from the providers'
> public docs; none was called live from this repo (fake servers in tests).

Seçim: panel → **Kurulum → Yapay zekâ modeli** (kartlar) veya `LLM_PROVIDER` (varsayılan `auto`: adresten anlaşılır). Anahtarı girip **Bağlan** deyince model listesi otomatik gelir (aranabilir liste, son kullanılan üstte, araç desteği biliniyorsa rozet); liste gelmezse model adı elle yazılır. Anahtarlar yalnız yazılır, geri gösterilmez. Kayıtlı anahtar yalnız kayıtlı adresin host'una gönderilir. Bulut ön ayarlarının adresi **sabittir** (değiştirilemez); yerel sağlayıcılar localhost/özel ağa gidebilir; "Özel" sağlayıcı varsayılan yalnız genel adreslere gider, yerel/özel ağ için kutu işaretlenir (`LLM_ALLOW_PRIVATE_NETWORK`). Link-local, bulut metadata, `0.0.0.0` ve bu servisin kendi portu her zaman engelli: [SECURITY.md §6b](SECURITY.md).

| Sağlayıcı (`LLM_PROVIDER`) | Base URL ön ayarı | Model listesi | Araç çağırma | Durum |
|---|---|---|---|---|
| OpenRouter (`openrouter`) | `https://openrouter.ai/api/v1` | `GET /models` (model başına `supported_parameters` → rozet) | modele göre | belgeden; canlı denenmedi |
| OpenAI (`openai`) | `https://api.openai.com/v1` | `GET /models` | var | belgeden; canlı denenmedi |
| Anthropic (`anthropic`) | `https://api.anthropic.com` | `GET /v1/models` + `x-api-key`, `anthropic-version: 2023-06-01` | var (yerel adaptör) | belgeden; canlı denenmedi |
| Google Gemini (`gemini`) | `https://generativelanguage.googleapis.com/v1beta/openai` | `GET /models` (`models/` öneki atılır) | var | belgeden; canlı denenmedi; `models/` önekinin atılması doğrulanmadı |
| xAI Grok (`xai`) | `https://api.x.ai/v1` | `GET /models` | var | belgeden; canlı denenmedi |
| Groq (`groq`) | `https://api.groq.com/openai/v1` | `GET /models` | modele göre | belgeden; canlı denenmedi |
| DeepSeek (`deepseek`) | `https://api.deepseek.com/v1` | `GET /models` | var | belgeden; canlı denenmedi |
| Mistral (`mistral`) | `https://api.mistral.ai/v1` | `GET /models` | var | belgeden; canlı denenmedi |
| Together (`together`) | `https://api.together.xyz/v1` | `GET /models` | modele göre | belgeden; canlı denenmedi |
| Perplexity (`perplexity`) | `https://api.perplexity.ai` | yok: model adını elle yazın (`sonar`, `sonar-pro`) | yok | belgeden; **doğrulanmadı** |
| Ollama (`ollama`) | `http://127.0.0.1:11434/v1` | `GET /api/tags` (yoksa `/v1/models`) | modele göre | sahte sunucuyla tarayıcıda denendi |
| LM Studio (`lmstudio`) | `http://127.0.0.1:1234/v1` | `GET /models` | modele göre | sahte sunucuyla |
| vLLM (`vllm`) | `http://127.0.0.1:8000/v1` | `GET /models` | modele göre | sahte sunucuyla tarayıcıda denendi |
| Özel (`custom`) | kendin yazarsın | `GET /models` | — | — |

Docker içinden makinedeki yerel sunucu: `http://host.docker.internal:11434/v1`.

**Anthropic adaptörü** (`src/ai_bot/llm.py` → `AnthropicClient`, `to_anthropic`, `from_anthropic`): OpenAI biçimindeki mesajlar `POST /v1/messages`'a çevrilir. Sistem istemi ayrı `system` alanına gider (konuşma ortasındaki sistem mesajı `[system]` önekiyle kullanıcı turuna); asistanın `tool_calls`'ı `tool_use` bloklarına, `tool` mesajları tek kullanıcı turunda `tool_result` bloklarına çevrilir; ardışık aynı roller birleştirilir; `max_tokens` her zaman gönderilir; akışsızdır. Yanıttaki `tool_use` blokları yeniden OpenAI `tool_calls` biçimine döner. OpenAI uyumlu Anthropic ucu üretim için önerilmediğinden kullanılmaz.

**Hata mesajları** sağlayıcı adıyla ve durum koduna göre Türkçedir: 401/403 anahtar reddedildi, 402 bakiye, 429 hız/kota sınırı, 404 model veya adres yok, 5xx sağlayıcı hatası. Anahtar hiçbir mesajda yer almaz.

> Doğrulama: her sağlayıcının model listesi, sohbet, araç çağrısı ve hata yolları `tests/test_ui_v2.py`'de sahte sunucuyla test edildi; Anthropic eşlemesi birim testleriyle ve ajan döngüsünde (araç → tool_result → yanıt) denendi. Gerçek sağlayıcılara bu depodan canlı istek atılmadı.

## Ayarlar

| Env | Varsayılan | Anlamı |
|---|---|---|
| `LLM_PROVIDER` | `auto` | Sağlayıcı (yukarıdaki tablo); `auto` adresten anlaşılır |
| `LLM_BASE_URL` | OpenRouter | Uç adresi (`/chat/completions` veya Anthropic'te `/v1/messages` eklenir) |
| `LLM_API_KEY` | — | `Authorization: Bearer` (Anthropic: `x-api-key`); panelden yalnız yazılır |
| `LLM_MODEL` | `openai/gpt-4o-mini` | Model. Önerilen: `deepseek/deepseek-v4.1-flash-1017` (DeepSeek V4.1 flash: ucuz, araç çağırır, 1M bağlam); sihirbaz bunu önerir |
| `LLM_MODEL_CHOICES` | boş | `/model` ile seçilebilecek modeller (boş = serbest) |
| `LLM_MAX_TOKENS` | 1024 | Yanıt başına token sınırı |
| `LLM_TEMPERATURE` | 0.3 | |
| `LLM_TIMEOUT` | 60 | İstek süre sınırı (sn) |
| `LLM_RETRIES` | 2 | 408/409/425/429/5xx ve ağ hatasında yeniden deneme (üstel bekleme) |
| `MAX_TOOL_STEPS` | 6 | Bir mesajda en çok araç turu; dolunca araçsız son cevap istenir |
| `TURN_TIMEOUT` | 180 | Bir mesajın toplam süresi; aşılınca yeni tur başlatılmaz |
| `TOOL_RESULT_MAX_CHARS` | 6000 | Modele dönen araç sonucu kırpma sınırı |
| `SYSTEM_PROMPT_EXTRA` | boş | Genel sistem istemi: tüm kanallarda ve panel sohbetinde eklenir |
| `SYSTEM_PROMPT_TELEGRAM`, `_WHATSAPP`, `_TEAMS`, `_SLACK`, `_EMAIL` | boş | Yalnız o kanalın turlarında, genel istemden sonra eklenir (panel sohbetinde eklenmez) |

`/model <ad>` o sohbet için modeli değiştirir, `/model varsayilan` geri alır.

Sağlayıcıya özel düzeltme (başlık, alan): `.claude/skills/add-llm-provider`.
