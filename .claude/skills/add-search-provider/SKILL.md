---
name: add-search-provider
description: Add a new web search backend to km-ai (selected with SEARCH_PROVIDER and offered in the setup wizard; keyed or keyless providers can also join the auto chain) next to parallel, duckduckgo, tavily, brave, serper and searxng, with a mocked test. Use when the user wants another search API (Kagi, Bing, an internal search...).
---

# Arama sağlayıcısı ekle

Sağlayıcılar `src/ai_bot/search.py` içinde `@register_provider("<ad>")` ile kayıtlıdır; `web.search` aracı `SEARCH_PROVIDER` ile seçer. Varsayılan `auto` modu ücretsiz kanalları sırayla dener, hata vereni atlar ve hatırlar. Hazır olanlar: `parallel` (anahtarsız MCP), `duckduckgo`, `tavily`, `brave`, `serper`, `searxng`.

1. `src/ai_bot/search.py` sonuna ekle (gerçek örnek: aynı dosyadaki `serper`):
   ```python
   @register_provider("kagi")
   async def kagi(client: SearchClient, query: str, n: int) -> list[Result]:
       key = client.settings.kagi_api_key               # anahtarlı ise Settings'ten oku
       if not key:
           raise ToolError("SEARCH_PROVIDER=kagi needs KAGI_API_KEY", code="config")
       resp = await client.http.get("https://kagi.com/api/v0/search", params={"q": query, "limit": n},
                                    headers={"Authorization": f"Bot {key}"})
       _check(resp, "kagi")
       return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("snippet")}
               for r in resp.json().get("data", []) if r.get("t") == 0]
   ```
   `auto` modu sabit bir zincir dener (`search.py` içindeki `_channels`: önce kendi SearXNG'n, anahtarı olanlar, sonra Parallel ve DuckDuckGo). Yeni sağlayıcının otomatik modda da denenmesini istiyorsan `_channels` içine, anahtarı varsa anahtar denetimiyle, ekle; eklemezsen yalnız `SEARCH_PROVIDER=<ad>` ile seçilince çalışır.
   Panelde görünmesi için: `src/ai_bot/config.py` (`kagi_api_key` alanı + `KAGI_API_KEY` env), `src/ai_bot/runtime_config.py` (`SEARCH_PROVIDERS` listesi, gizli değer adı `SECRET_NAMES`/`SECRET_FIELDS`), `src/ai_bot/ui/pages/setup.js` (`SEARCH_INFO` kartı), isteğe bağlı rehber `src/ai_bot/ui/help.js` (`search-kagi`) ve `src/ai_bot/ui/i18n-en.js` çevirileri.
   - Dönüş: `{"title", "url", "snippet"}` listesi; kırpma/temizlik `SearchClient.search` içinde yapılır.
   - Anahtar adı `..._API_KEY` olsun: komuta.yaml'da `secrets` (prompt) + `valueFrom.secret` ile bağlanır, asla `value:` ile yazılmaz (bkz. `examples/komuta-arama-saglayicili.yaml`).
2. Test: `tests/test_plugins.py::test_web_search_providers` kalıbını kopyala; `h.app.search.http` yerine `httpx.MockTransport` koy, gerçek ağa çıkma.
   ```bash
   .venv/bin/python -m pytest tests/test_plugins.py
   ```
3. Belgele: `docs/ARACLAR.md` (web.search bölümü), `README.md`/`README.en.md` ve `.env.example`'a boş `KAGI_API_KEY=` satırı.
4. Manifesti doğrula:
   ```bash
   .venv/bin/python ../stack-spec/tools/validate_stack.py . --allow-placeholders
   ```
   (stack-spec reposu başka yerdeyse yolu ona göre değiştir.)
