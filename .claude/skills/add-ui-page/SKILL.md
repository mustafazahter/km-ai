---
name: add-ui-page
description: Add a page to this stack's admin UI (/ui) with the shared komuta-ui design language, a protected /ui/api endpoint and a test. Use when the user wants a new screen, form or status view in the panel.
---

# Add a page to the admin UI

The infrastructure lives in `stack_ui` (vendored `stack_ui/`, next to `stack_kit/`); details are in `docs/UI.md` of the `komuta-stack-ui` repo.

1. Find this stack's page folder (`<static_dir>`; it can differ per stack):
   ```bash
   grep -rn "mount_ui(" --include='*.py' --exclude-dir=stack_kit --exclude-dir=stack_ui --exclude-dir=.venv .
   ```
   The path given with `static_dir=` is `<static_dir>` (usually an in-package path such as `Path(__file__).parent / "ui"`). If there is no `mount_ui` at all, add it to the core: `mount_ui(app, stack, static_dir=<path>)`. If the pages are routed within a single file by `#/route` (hash routing), add a new route instead of a new file.
   For a new file, copy the skeleton:
   ```bash
   cp stack_ui/assets/example/index.html <static_dir>/<page>.html
   ```
   Change what is inside `<main class="content">`; add the link to both `.nav` and `.bottombar`; use `<script src="<page>.js" defer>` instead of `<script src="example.js" defer>`.
2. **Do not write an inline `<script>` or `style="..."`**: CSP blocks them. For style use the `komuta-ui.css` classes (`.card`, `.btn`, `.field`, `.table`, `.badge`…), for code a separate `.js`. Add text with `KUI.el(...)`/`textContent`, do not use `innerHTML`.
3. Data endpoint (`APIRouter`; identity and CSRF are added by `mount_ui`):
   ```python
   from fastapi import APIRouter, Depends
   from stack_ui import UIUser, current_user

   router = APIRouter()

   @router.get("/<name>")
   async def list_items(user: UIUser = Depends(current_user)):
       return {"ok": True, "items": []}
   ```
   and add it to the `api_router=[...]` list of the existing `mount_ui(...)` call (`mount_ui` is called once per application; a second call raises an error). In the browser: `KUI.api("/<name>")`, for writes: `KUI.api("/<name>", {method: "POST", body: {...}})` (the CSRF header is automatic).
4. Money movement / an irreversible button: `KUI.confirm({title, body, danger: true})` and, on the server, a `confirm=True` tool (`stack.call` → `needs_confirmation` → `confirm_id`). Never return secret values (`SecretStore.describe` gives only `…son4`).
5. Test (`tests/test_ui_<name>.py`):
   ```python
   from fastapi.testclient import TestClient
   from stack_kit import BridgeConfig, Stack, create_app
   from stack_ui import UIConfig, mount_ui

   STATIC_DIR = "<static_dir>"   # the path from step 1

   def test_page_api():
       stack = Stack("test")
       app = create_app(stack, config=BridgeConfig(token="test-token-0123456789abcdef"), load_plugins=False)
       mount_ui(app, stack, static_dir=STATIC_DIR, api_router=router, config=UIConfig(auth="off", allow_insecure=True))
       with TestClient(app) as c:
           assert c.get("/ui/<page>.html").status_code == 200
           assert c.get("/ui/api/<name>").json()["ok"] is True
   ```
   ```bash
   python -m pytest -q tests
   ```
6. To look by hand, run in password mode (`UI_AUTH=password UI_PASSWORD=<8+ characters>`; `UI_PASSWORD` needs at least 8 characters, a password set in the panel at least 10) and open `/ui/`; there must be no horizontal scroll at 375 px width.

Layout, verifying endpoints (calling them with `TestClient` instead of listing the FastAPI routes), the combined service: `docs/UI.md` §10–12 in the `komuta-stack-ui` repo.

Design rules: the accent is platinum/steel blue; no gold, brown, orange, beige, purple; no serif and no cards with a left rule; no external CDN/web font.

## In ai-bot (this repo)

The ai-bot panel is a single-page app; follow this instead of the generic steps above:

1. **Page:** write `src/ai_bot/ui/pages/<name>.js` and register it with `A.pages.<name> = { title: "...", render: function (main, route) {...} }`. Helpers live in `window.AIB` (`widgets.js`: `chatManager`, `telegramToken`, `llmPicker`): `pageHead`, `card`, `button`, `field`, `badge`, `empty`, `skeleton`, `errorBox`, `callout`, `secretInput`, `icon`, `onLeave` (stop `KUI.poll` when leaving the page).
2. **Registration:** add `<script src="pages/<name>.js" defer></script>` to `src/ai_bot/ui/index.html` (before boot.js); add `"<address>": "<name>"` to the `routes` table in `app.js`; add the menu link with `data-route="<name>"` to both `.nav` and (if needed) the `[data-sheet]` "Diğer" (More) list. The bottom menu has at most 5 items.
3. **API:** add the endpoint to `Panel._build()` in `src/ai_bot/panel.py` (`/ui/api/<name>`; identity and CSRF are added by `mount_ui`). Every write that changes a setting goes through `self._save(values, secrets, user)`: it validates, stores and applies to the running bot with `LiveConfig.apply()`.
4. **New setting:** `src/ai_bot/runtime_config.py` → `SCHEMA` (+ `FIELD_MAP`, `GROUP_OF`). Do NOT add anything that carries a security lock (the confirmation rule, SSRF limits, the allow-list rule, `BRIDGE_TOKEN`, `UI_*`) to the schema; do not lower lower bounds below the default.
5. **Secret:** add it to `SECRET_NAMES`; never return the value in any response (`secrets.describe` gives only `…last4`).
6. **Test:** use the `Env` fixture in `tests/test_ui.py` (password mode, fake LLM/Telegram):
   ```bash
   .venv/bin/python -m pytest -q tests/test_ui.py
   ```
7. **Translation:** texts are written in Turkish; the English goes into the `src/ai_bot/ui/i18n-en.js` dictionary (key = the Turkish text). `python scripts/i18n_extract.py --missing` lists what is missing; for joined dynamic texts add a regular expression to the rules at the end of the file (`AIB_I18N_RULES`).
8. **Periodically refreshed page** (`KUI.poll`): do not wipe and rebuild the content on every round; build the structure on the first paint, on later rounds update only what changed in place with `A.morph(host, newNode)` (no flicker, focus stays; only for markup without listeners), keep the latest data in a module variable and paint it right away when returning to the page, and on a refresh error do not break the screen (add an "may be stale" note). Example: `pages/overview.js`.
9. **Password/secret field:** use `A.secretInput`; the kit adds an eye button to every `type=password` field (`KUI.passwordField`), do not write one by hand. No emoji in UI texts.
10. **Setup wizard:** the steps come from the server (`panel.setup_status`, ids `panel`, `channel`, `channel_setup`/`telegram`+`chats`, `llm`, `search`, `summary`) and vary with the chosen channel; `STEPS` in `pages/setup.js` needs a function for each id. If you add a step, define it both on the server and in `STEPS`/`HINT`; `ORDER` is synced from the server (`syncOrder`).
11. **Visual check:** `UI_AUTH=password UI_PASSWORD=<8+> LLM_BASE_URL=http://127.0.0.1:9999/v1 PYTHONPATH=src:. .venv/bin/python -m ai_bot` (first `scripts/fake_llm.py 9999`), open `/ui/`; desktop + 375 px, light + dark theme, no errors in the console.
