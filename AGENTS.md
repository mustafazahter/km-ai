# AGENTS.md — komuta ai-bot (for Codex, Cursor and other agents; same rules as CLAUDE.md)

AI foundation stack (Telegram, WhatsApp, Teams, Slack, e-mail and panel channels): a tool-calling LLM (OpenAI-compatible), web search/reading, scheduled jobs, memory, bridges. The bridge/MCP/Telegram foundation is the vendored `stack_kit/` (komuta-stack-kit); the admin panel foundation is the vendored `stack_ui/` (komuta-stack-ui). Docs are English-primary: `README.md` and `docs/` (guide set: `ADMIN-UI`, `CHANNELS`, `SECURITY`, `SETUP`, `DEVELOPER`, `BRIDGES`, `TOOLS`, `MEMORY`, `SCHEDULING`, `LLM-PROVIDERS`, `FAQ`); Turkish translations live in `README.tr.md` and `docs/tr/`. Agent-facing files (CLAUDE.md, AGENTS.md, plugins/README.md, every SKILL.md) are English only.

## Map

- `src/ai_bot/__init__.py` `setup(stack, bot=None)` — public entry point; the combined service (mt5-trader) relies on this signature, do not break it
- `src/ai_bot/agent.py` tool loop + confirmation rule · `llm.py` OpenAI-compatible client · `core.py` `AiBot` container
- `src/ai_bot/builtin_tools.py` `web.*`, `schedule.*`, `memory.*`, `ai.ask` · `telegram_ui.py` built-in commands + Telegram free text · `commands.py` dispatcher that runs commands on the other channels + `@command` (every channel)
- `src/ai_bot/safefetch.py` SSRF-safe fetcher · `search.py` search providers · `htmltext.py` HTML to text
- `src/ai_bot/scheduler.py` + `timeparse.py` (cron/relative/ISO) · `memory.py` + `db.py` (SQLite, FTS5)
- Admin UI `/ui`: `src/ai_bot/panel.py` (API, `mount_panel`, `default_auth="password"`) · `runtime_config.py` (settings schema, panel > env > default, `LiveConfig`) · `providers.py` (14 LLM providers, model list, cloud host lock) · `netpolicy.py` (SSRF policy for addresses entered in the panel) · `llm.py` (OpenAI-compatible + Anthropic adapter) · `ui/` (`app.js`, `widgets.js`, `pages/*.js`) · `main.py` `build_app()` (bridge gate closed by default) · doc `docs/ADMIN-UI.md`
- `plugins/` developer tools (loaded at startup) · `tests/` pytest (`tests/fakes.py`: fake LLM/Telegram)
- `komuta.yaml`, `examples/*.yaml` Komuta manifests · `docs/` docs · `.claude/skills/` skills
- `stack_kit/` and `stack_ui/` are VENDORED — do not edit here; copy with `stack-kit/scripts/sync-kit.sh . --skills` and `stack-ui/scripts/sync-ui.sh . --skills`

## Commands (all verified)

- Install + test: `uv venv --python 3.12 && uv pip install -r requirements-dev.txt && .venv/bin/python -m pytest`
- Run locally: `scripts/setup.sh` then `PYTHONPATH=src:. .venv/bin/python -m ai_bot`
- Fake LLM: `.venv/bin/python scripts/fake_llm.py 9999` + `LLM_BASE_URL=http://127.0.0.1:9999/v1`
- Plugin tools: `.venv/bin/python -m stack_kit tools`
- Manifest check: `.venv/bin/python ../stack-spec/tools/validate_stack.py . --allow-placeholders`
- Open the UI locally: `LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:9999/v1 PYTHONPATH=src:. .venv/bin/python -m ai_bot` → with the `Kurulum kodu` (setup code) from the log, open `http://127.0.0.1:8080/ui/setup` (or set `UI_PASSWORD=...`)
- Bridge gate: `.venv/bin/python -m stack_kit gate status` (all closed by default)

## Never do

- Do not commit secrets, tokens, chat ids or e-mail addresses (`.env` is ignored; `.env.example` stays empty; manifests carry no values, secrets come from the wizard or a Secret).
- Do not let the AI run a money/action tool without approval: do not show `confirm_id` to the model, do not pass `confirmed`, do not loosen the `HUMAN_SCOPES`/`needs_human` rule, do not add an unapproved path.
- Do not loosen SSRF protection (`safefetch.py`: IP check, pinning, redirect check, port/size/time limits). Use `app.fetcher` for outbound requests.
- Do not share memory across chats; do not loosen the secret patterns (`memory.BLOCK_PATTERNS`).
- Do not remove the prompt-injection lock (`UNTRUSTED_OUTPUT_TOOLS`/`PERSISTENT_TOOLS`, the memory data block, the ban on `schedule.add` in a scheduled turn); do not enable chat tools when `ALLOWED_CHAT_IDS` is empty.
- Do not chown `/data` to anything other than 1000; do not add chown/setpriv/root startup at runtime. For the Komuta disk the app uid must be 1000 (`USER 1000`); changing disk ownership prevents the next rollout from starting.
- Do not let the UI switch off a security lock: the confirmation rule, SSRF limits (`WEB_READ_*` port/redirect/size), "chat tools closed when the allow-list is empty", the prompt-injection lock, `BRIDGE_TOKEN` and `UI_*` never enter `runtime_config.SCHEMA`; lower bounds never go below the default.
- Do not show a secret back: no `/ui/api` response, HTML/JS, log or error message contains a token/key (only `secrets.describe` → `…last4`); test endpoints do not echo the supplied value.
- Do not open `/ui/api` write endpoints without identity/CSRF (`mount_ui` adds them; do not attach your own router outside `mount_ui`); do not make `UI_AUTH=off` the default; do not loosen `default_auth`.
- Do not make the bridge/MCP/hook open by default (`main.GATE_DEFAULT` is all `False`); do not show a token more than once; do not send a saved LLM key to another host (`panel._llm_target`).
- Do not write the setup code anywhere except the log (once, to the startup log; the kit does it).
- Do not loosen the network policy (`netpolicy.py`): the host lock of cloud presets, the link-local/metadata/0.0.0.0/own-port block, IP pinning, no redirect following; do not reflect a raw provider body on panel endpoints.
- Do not edit inside `stack_kit/`; do not git commit/push (the user does that).
