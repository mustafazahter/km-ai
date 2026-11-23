---
name: add-llm-provider
description: Add or change an LLM provider of km-ai (14 are built in: OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity, Ollama, LM Studio, vLLM, custom) - a card in the setup wizard and the Ayarlar page, an auto-loaded model list - or adapt the client for a provider quirk, and verify with the fake LLM server. Use when the user wants another model provider or a local model.
---

# LLM sağlayıcısı ekle / değiştir

İstemci `src/ai_bot/llm.py` (`LLMClient`) OpenAI `/chat/completions` biçimini konuşur; çoğu sağlayıcı için kod gerekmez, yalnız env:

| Env | Örnek |
|---|---|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` (varsayılan), `https://api.openai.com/v1`, `http://localhost:11434/v1` (Ollama) |
| `LLM_API_KEY` | sağlayıcı anahtarı (yerel sunucuda boş olabilir) |
| `LLM_PROVIDER` | `auto` (adresten anlaşılır) ya da sağlayıcı kimliği (`openrouter`, `openai`, `anthropic`, `deepseek`, `ollama` ...) |
| `LLM_MODEL` | sağlayıcının model adı; araç çağırmayı (tools) desteklemeli. Önerilen: `deepseek/deepseek-v4.1-flash-1017` (OpenRouter); yerleşik varsayılan `openai/gpt-4o-mini` çalışan kurulumlar değişmesin diye aynı kaldı, değiştirme |

0. **Yeni sağlayıcı kartı:** `src/ai_bot/providers.py` içindeki `PROVIDERS` listesine `Provider(kimlik, ad, taban_adres, model=<önerilen model>, hint=..., ...)` ekle. Sihirbaz bu modeli ön doldurur. Bulut sağlayıcıların anahtarı yalnız kayıtlı adresin host'una gider (`panel._llm_target`), özel ağ adresleri `netpolicy.py` kurallarına uyar; bunları gevşetme. Model listesi ucu varsa `models=` ile bağla. Test: `tests/test_ui.py` (`test_llm_*`).
1. Yerelde sahte sunucuyla uçtan uca dene (anahtar ve internet gerekmez):
   ```bash
   .venv/bin/python scripts/fake_llm.py 9999
   LLM_BASE_URL=http://127.0.0.1:9999/v1 BRIDGE_TOKEN=dev-token-change-me-0123456789 ALLOWED_CHAT_IDS=1 \
     HTTP_PORT=8091 PYTHONPATH=src:. .venv/bin/python -m ai_bot
   curl -s -H "Authorization: Bearer dev-token-change-me-0123456789" -H 'Content-Type: application/json' \
     -d '{"args":{"prompt":"zar at"}}' http://127.0.0.1:8091/bridge/call/ai.ask
   ```
2. Sağlayıcı farklı bir başlık/alan istiyorsa `LLMClient.__init__` (başlıklar) veya `LLMClient.chat` (gövde) içinde değiştir; yanıttan yalnız `choices[0].message` okunur. Anthropic (`/v1/messages`) için ayrı yerel adaptör `llm.py` içindedir (`kind="anthropic"`); onun biçimini OpenAI uyumlu sağlayıcıya uygulama.
3. Test: `tests/test_agent.py` sahte LLM'i (`tests/fakes.py::FakeLLM`) kullanır; yeni davranış için oraya test ekle:
   ```bash
   .venv/bin/python -m pytest tests/test_agent.py
   ```
4. Komuta'da: `komuta.yaml` → `LLM_BASE_URL` ve `LLM_MODEL` literal, anahtar `llm-api-key` Secret'ı. Belge: `docs/LLM-SAGLAYICILAR.md`.
