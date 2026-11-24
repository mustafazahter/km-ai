---
name: add-search-provider
description: Add a new web search backend to km-ai (selected with SEARCH_PROVIDER and offered in the setup wizard; keyed or keyless providers can also join the auto chain) next to parallel, duckduckgo, tavily, brave, serper and searxng, with a mocked test. Use when the user wants another search API (Kagi, Bing, an internal search...).
---

# Add a search provider

Providers are registered in `src/ai_bot/search.py` with `@register_provider("<name>")`; the `web.search` tool selects one with `SEARCH_PROVIDER`. The default `auto` mode tries the free channels in turn, skips the ones that fail and remembers them. Ready-made: `parallel` (keyless MCP), `duckduckgo`, `tavily`, `brave`, `serper`, `searxng`.

1. Add it at the end of `src/ai_bot/search.py` (a real example: `serper` in the same file):
   ```python
   @register_provider("kagi")
   async def kagi(client: SearchClient, query: str, n: int) -> list[Result]:
       key = client.settings.kagi_api_key               # if keyed, read it from Settings
       if not key:
           raise ToolError("SEARCH_PROVIDER=kagi needs KAGI_API_KEY", code="config")
       resp = await client.http.get("https://kagi.com/api/v0/search", params={"q": query, "limit": n},
                                    headers={"Authorization": f"Bot {key}"})
       _check(resp, "kagi")
       return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("snippet")}
               for r in resp.json().get("data", []) if r.get("t") == 0]
   ```
   `auto` mode tries a fixed chain (`_channels` in `search.py`: your own SearXNG first, those with a key, then Parallel and DuckDuckGo). If you want the new provider to be tried in auto mode too, add it to `_channels` (with a key check if it has a key); if you do not, it works only when selected with `SEARCH_PROVIDER=<name>`.
   To make it show up in the panel: `src/ai_bot/config.py` (a `kagi_api_key` field + the `KAGI_API_KEY` env), `src/ai_bot/runtime_config.py` (the `SEARCH_PROVIDERS` list, the secret value name in `SECRET_NAMES`/`SECRET_FIELDS`), `src/ai_bot/ui/pages/setup.js` (a `SEARCH_INFO` card), optionally a guide in `src/ai_bot/ui/help.js` (`search-kagi`) and the translations in `src/ai_bot/ui/i18n-en.js`.
   - Return: a list of `{"title", "url", "snippet"}`; trimming/cleanup is done in `SearchClient.search`.
   - Name the key `..._API_KEY`: in komuta.yaml it is bound with `secrets` (prompt) + `valueFrom.secret` and is never written with `value:` (see `examples/komuta-arama-saglayicili.yaml`).
2. Test: copy the pattern of `tests/test_plugins.py::test_web_search_providers`; put `httpx.MockTransport` in place of `h.app.search.http`, never go out to the real network.
   ```bash
   .venv/bin/python -m pytest tests/test_plugins.py
   ```
3. Document it: `docs/TOOLS.md` and `docs/tr/TOOLS.md` (the web.search section), `README.md`/`README.tr.md`, and an empty `KAGI_API_KEY=` line in `.env.example`.
4. Validate the manifest:
   ```bash
   .venv/bin/python ../stack-spec/tools/validate_stack.py . --allow-placeholders
   ```
   (If the stack-spec repo is elsewhere, change the path accordingly.)
