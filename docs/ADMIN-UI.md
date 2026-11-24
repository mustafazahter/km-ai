English · [Türkçe](tr/ADMIN-UI.md)

# Admin UI (`/ui`)

> Admin panel at `/ui`, protected by a password by default (first start: a one-time setup code in the service
> log, then `/ui/setup`). The default manifest also keeps Komuta sign-in on, so the panel has two gates. Wizard:
> panel password, channel choice (setup continues with the chosen channel: Telegram token and chat ids, or the WhatsApp/Teams/Slack/e-mail form), LLM provider + automatic model list, search. Values
> from env vars show up filled (panel value > env > default). `/bridge`, `/mcp`, `/hook` start closed and are
> opened from the panel, with tokens shown once. Secrets are write-only; security locks cannot be switched off.

Contents

1. [Screens](#1-screens)
2. [First start: two gates](#2-first-start-two-gates)
3. [Setup wizard](#3-setup-wizard)
4. [Environment variables and the wizard](#4-environment-variables-and-the-wizard)
5. [Bridge, MCP and webhook](#5-bridge-mcp-and-webhook)
6. [Panel protection](#6-panel-protection)
7. [Komuta sign-in and machines](#7-komuta-sign-in-and-machines)
8. [On your own server](#8-on-your-own-server)
9. [Live apply](#9-live-apply)
10. [Security model](#10-security-model)
11. [Environment variables](#11-environment-variables)
12. [Developer notes](#12-developer-notes)
13. [Language, helpers and the updating screen](#13-language-helpers-and-the-updating-screen)

## 1. Screens

| Screen | Address | What it does |
|---|---|---|
| Overview | `/ui/#/` | Telegram, model, search, memory/task counts, bridge surfaces (on/off), panel protection, recent errors, setup progress. |
| Chat | `/ui/#/sohbet` | Write to the bot from the panel: pick a model (from the list or by hand), tools run, and the ones that need approval are asked in the browser. Conversations are stored on the server (SQLite inside `DATA_DIR`): plain user/AI text only, secrets masked, per admin. Open one from the **History** list to continue it; delete them one by one or in bulk (at most 200 chats, 400 messages per chat). Endpoints: `GET /ui/api/chat/conversations`, `GET/DELETE /ui/api/chat/conversations/{id}`, `DELETE /ui/api/chat/conversations`. Channel (Telegram, WhatsApp, ...) histories are not written here. Failed or cancelled turns are not saved. When panel protection is off (everyone becomes the same "user"), no chat is stored or listed. |
| Setup | `/ui/#/kurulum` | Setup status: the state of every step (Done / Not verified / Missing), missing ones in red, *Edit / Verify / Complete* on every row. Clicking a step opens only that step. |
| Welcome wizard | `/ui/#/hos-geldin` | Full-screen, step-by-step first setup (below). It opens by itself only on the first sign-in; after *Do it later* (stored on the server) or after finishing setup it does not open again. Open it by hand with *Restart the wizard* on the Setup page. |
| Settings | `/ui/#/ayarlar` | Sections in the left menu: Model, Replies and tools, Search, Memory and tasks, Telegram, **Security** (panel protection, locks; `#/ayarlar/guvenlik`), Secrets (write-only), Change history. Source badge: panel / environment / default. |
| Telegram | `/ui/#/telegram` | Bot token (write it, `getMe` test), connection status and reconnect, allowed chats + live chat-id capture, command menu sync (`setMyCommands`), recent Telegram errors. |
| Channels | `/ui/#/kanallar` | Telegram, WhatsApp, Microsoft Teams, Slack and e-mail (the Telegram card opens its own `#/telegram` page, the on/off switch is on the card): per-channel on/off, setup guide, settings form (secrets are write-only), allow list, Test, a verification code shown once. Details: [CHANNELS.md](CHANNELS.md). |
| Integrations | `/ui/#/kopru` | `/bridge`, `/mcp`, `/hook` on/off, generate/rotate/revoke the token and webhook secret (shown once, with a copy button), copy-paste examples, audit log. |
| Memory, Tasks | `/ui/#/hafiza`, `/ui/#/gorevler` | Per-chat memories (search, delete), scheduled tasks (cancel). |
| Tools, Logs | `/ui/#/araclar`, `/ui/#/gunluk` | Built-in/plugin/bridge tools (the ones that need approval are marked), redacted log and events. |

## 2. First start: two gates

The default `komuta.yaml` sets up two gates:

1. **Komuta sign-in** (path rule `access.paths: [{path: /ui, signIn: true}]`, `forwardIdentity: true`): only `/ui` requires a Komuta account sign-in first; the rest of the site (`/health`, `/bridge`, `/mcp`, `/hook`, `/channels`) stays open without sign-in and carries its own protection.
2. **Panel password** (`mount_ui(default_auth="password")`): there is no password on first start. When the service starts it writes a **setup code** to the log:
   ```
   admin UI first-time setup required. Kurulum kodu / setup code (one-time, changes on restart): XXXX-XXXX-XXXX
   ```
   Get the code from the Komuta console → service → **Logs** tab, then open `https://<service-address>/ui/`: `/ui/setup` asks for the code and a new password (at least 10 characters). The code is single-use and changes on restart. If `UI_PASSWORD` is set, this step is skipped; you can supply your own code with `UI_SETUP_CODE`.

Why two gates: by default the app treats the Komuta identity (JWT) only as an extra gate; the panel is also protected by its own password. Because the manifest has `forwardIdentity: true`, with `UI_AUTH=auto` no password is asked when a Komuta identity is present, and a password is asked when it is not (`UI_AUTH=komuta`: Komuta identity only). The path rule and identity forwarding can be written in the manifest; external callers (n8n, MCP, webhook) are outside `/ui`, so they are not stopped at the gate.

If you forgot the password: on the sign-in page press **Forgot my password** (`/ui/reset`). A single-use code, valid for 15 minutes, is written to the service log (Komuta console → Logs, search for `sıfırlama`, the Turkish word for "reset"); enter the code and a new password, and on save all old sessions are closed. If the password comes from the `UI_PASSWORD` environment variable, change it there.

## 3. Setup wizard

1. **Panel password.** If it is set up you see "Done" (`UI_PASSWORD` from the environment, set in the panel, or Komuta identity). If protection is off, a warning and a "Turn protection on" button appear here.
2. **Channel.** You choose which channel you will talk through: Telegram, WhatsApp, Microsoft Teams, Slack, e-mail, or *None for now* (panel chat only). The choice is stored on the server; the wizard continues with that channel's setup, and you add the other channels later from the **Channels** page. On installs that already configured Telegram or another channel, the choice is set automatically from that.
3. **Setup of the chosen channel.**
   - **Telegram:** two steps. *Telegram bot* (the BotFather token; *Test* checks it with `getMe`, *Save and connect* stores it) and *Chat IDs* (send the bot a message, the chat shows up in the list within a few seconds, *Allow*; adding by hand is also possible; while the list is empty the chat tools are off).
   - **WhatsApp, Teams, Slack, e-mail:** one step, *<Channel> setup*. The same form as on the Channels page is embedded: setup guide, connection address, settings and secrets (write-only), allow list, channel-specific system prompt, on switch and *Test*. The step is done when the channel is **on, its settings are complete and the allow list has at least one person**; *Skip for now* returns to panel chat only.
4. **AI model.** Provider cards: OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity, Ollama, LM Studio, vLLM, custom. Enter the key and press **Connect**: the model list arrives (searchable, last used on top, tool badge). The model the wizard suggests is `deepseek/deepseek-v4.1-flash-1017` (the built-in default `openai/gpt-4o-mini` stayed the same so that working installs do not change). Details: [LLM-PROVIDERS.md](LLM-PROVIDERS.md).
5. **Web search.** Automatic mode (recommended), Parallel, DuckDuckGo, Tavily, Brave, Serper, SearXNG. A provider with a missing key is not saved.
6. **Summary and apply.** *Apply and finish*; no restart needed.

The step list changes with the choice (for example, choosing Telegram gives two channel steps, choosing WhatsApp gives one); the progress bar and *Back*/*Continue* follow it. Server side: `POST /ui/api/setup/channel {"channel": "telegram|whatsapp|teams|slack|email|none"}`; the `GET /ui/api/setup` response has `channel` and the step ids (`channel`, `channel_setup`, `telegram`, `chats`, ...).

The flow is one card and one action: when a step succeeds it moves to the next one, *Continue* does not open until the step is verified; the search step can be skipped.

Step states: **Done** (verified: a real test or a confirmation), **Not verified** (the value came from an environment variable/default; a *Verify* button is offered, it does not count as done), **Pending**. A value that comes from the environment is never counted as "done"; the chat list and search need *confirm*, Telegram and the model need verification with a real request. The chat id `0` is a placeholder; the list counts as empty.

## 4. Environment variables and the wizard

Every setting can be entered both as an environment variable/Komuta Secret and from the panel. Precedence:

```
value saved in the panel  >  environment variable  >  default
```

- The wizard shows values that come from the environment as filled in: "From environment variable: `TELEGRAM_BOT_TOKEN` …abcd" (for secrets, only the last 4 characters).
- If you type a new value in the panel, it overrides the environment value; the field says "Saved in the panel · overriding the environment value" and the **Back to environment variable** button deletes the panel value.
- Values from the environment look filled in, but the step stays open until verified (a wrong key does not look "ready"). Example manifest: [`examples/komuta-env-ile.yaml`](../examples/komuta-env-ile.yaml).

## 5. Bridge, MCP and webhook

`/bridge/*`, `/mcp`, `/hook/*` are **off by default**: a closed surface returns **404** before any identity check. To open them:

- Panel → **Integrations**: a switch per surface; when the bridge/MCP is opened and there is no token, one is generated and shown **once** (copy button). The token and webhook secret can be rotated/revoked; every change goes into the audit log. The server stores only a digest of the token.
- Environment: `BRIDGE_ENABLED=true`, `MCP_ENABLED=true`, `HOOKS_ENABLED=true` (the starting value; example [`examples/komuta-n8n-ile.yaml`](../examples/komuta-n8n-ile.yaml)). The `BRIDGE_TOKEN` / `HOOK_SECRET` env vars are still valid but are not in the manifest.
- Command line: `python -m stack_kit gate status | enable bridge | rotate`.

Ready-made examples on the page: curl, n8n HTTP Request, Claude Code (`claude mcp add --transport http ...`), Claude Desktop (via `mcp-remote`), TradingView webhook (`/hook/notify`, a `"secret"` field in the body). `/hook/notify` forwards the incoming text as a Telegram message to the first allowed chat (it does not run the AI).

The **Quick connect** card at the top of the page (stack_ui `KUI.quickConnect`, `/ui/api/bridge/quickstart`): pick a client (Claude Code, Claude Desktop, generic/curl, n8n), copy the prompt and paste it to the agent; the agent sets up the MCP connection. While MCP is off the card is inactive ("turn it on first"); at the moment a new token is generated, a one-time "copy with token" option appears (the token is kept bound to the session on the server for a short time, it is not stored on the page).

## 6. Panel protection

Settings → **Panel protection**:

- **Change password** (current + new). When it changes, all other sessions are dropped. If `UI_PASSWORD` comes from the environment, it can only be changed there.
- **Sign out of all sessions**: everyone, you included, signs in again.
- **Turn protection off (not recommended)**: you need the current password + typing `KORUMAYI KAPAT` (Turkish for "turn protection off") + reading the risk warnings and ticking the acknowledgement box. While it is off, a permanent red banner shows on every page and one click turns it back on; a warning is written to the log on every start; identity/password settings cannot be changed anonymously, and CSRF still applies to write requests. `UI_FORCE_PROTECTION=1` forbids turning it off entirely.

## 7. Komuta sign-in and machines

The default manifest ties only the `/ui` path to Komuta sign-in (`access.paths`). The bridge, MCP, webhook and channel addresses are open without sign-in and carry their own protection: Bearer token, HMAC signature, JWT. This is why n8n, an MCP client, TradingView and the WhatsApp/Teams/Slack webhooks are not stopped at the gate. If you want to turn Komuta sign-in off completely, use `examples/komuta-giris-kapali.yaml`; to put the whole site behind Komuta sign-in, write `access.signIn: true` (in that case you need to open the relevant paths without sign-in for machine callers).

| Manifest | Komuta sign-in | Panel | Machines |
|---|---|---|---|
| `komuta.yaml` (default) | `/ui` only | password | direct, with their own token/signature |
| `examples/komuta-giris-kapali.yaml` | none ("unprotected" in the console) | password | direct, with the token |
| `examples/komuta-env-ile.yaml` | on | `UI_PASSWORD` Secret | — |
| `examples/komuta-n8n-ile.yaml` | none | password | bridge open from the manifest |

## 8. On your own server

```bash
scripts/setup.sh            # .env: generates UI_PASSWORD and UI_SESSION_SECRET
docker compose up --build   # http://localhost:8080/ui/
```

If you do not set `UI_PASSWORD`, use the setup code from the log at `/ui/setup`. Locally with a fake LLM:

```bash
.venv/bin/python scripts/fake_llm.py 9999 &
LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:9999/v1 PYTHONPATH=src:. .venv/bin/python -m ai_bot
# with the "Kurulum kodu" from the log, http://127.0.0.1:8080/ui/setup
```

## 9. Live apply

Settings apply to the running bot the moment they are saved: when the model/provider/key changes, a new LLM client is built (the local adapter if Anthropic is chosen), search, the allow list, time zone, limits and the approval timeout take effect immediately, and when the Telegram token changes the connection is set up again. The one exception: if the bot belongs to another module (the combined mt5-trader), the service must be restarted for the token; the UI says so.

### Network policy (addresses entered in the panel)

The LLM Base URL, the model list, the LLM test and `SEARXNG_URL` send requests to addresses the admin typed; to guard against SSRF, every request goes through `src/ai_bot/netpolicy.py`:

| Provider | Address | Allowed target |
|---|---|---|
| Cloud presets (OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity) | **locked to the preset's host**; any other address gets 422 | only that host |
| Local (Ollama, LM Studio, vLLM), SearXNG | free | public + loopback + private network |
| Custom (OpenAI-compatible) | free | public IPs only; if the "local/private network" box (`LLM_ALLOW_PRIVATE_NETWORK`) is ticked, the private network too |

Blocked in every case: link-local (169.254.0.0/16, fe80::/10), cloud metadata (169.254.169.254, fd00:ec2::254, `metadata.google.internal` ...), `0.0.0.0`/`::`, multicast/reserved addresses, this service's own port (`HTTP_PORT`, on a local address) and the `/ui`, `/bridge`, `/mcp`, `/hook` paths. After the name is resolved, **every** address is checked and the connection is pinned to the checked IP (it carries the `Host` and TLS SNI name; DNS rebinding does not work); IPv4-mapped IPv6 is unwrapped. Redirects are not followed. The saved LLM key goes only to the saved host. Responses do not reflect a raw body: from a model list only model names with a valid format are returned (at most 200 characters, no control characters); error messages are short Turkish texts chosen by status code, and the provider's body is not shown.

## 10. Security model

- **The link is assumed public.** The panel has a password (scrypt, `ui-auth.json` with mode 600), first setup uses a single-use code; in the default manifest Komuta sign-in sits in front as well.
- **Machine paths start closed** (404); when opened they require a token (only its digest is stored) or HMAC.
- **Secrets are never shown back**: responses carry only "saved" + the last 4 characters; test endpoints do not echo the value they were given; the setup code is written only once, to the startup log.
- **The saved LLM key goes only to the saved host**: if another address is typed in the UI, the saved key is not sent.
- **Locks cannot be turned off**: human approval, SSRF protection, chat tools closed on an empty allow list, the prompt-injection lock, and `BRIDGE_TOKEN`/`UI_*` are not in the settings schema; lower bounds are kept.
- **CSRF + Origin** on every write request; strict CSP; audit records (settings, password/protection, bridge gate).

Details: [SECURITY.md](SECURITY.md).

## 11. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `UI_AUTH` | (unset → password) | `password`, `auto` (Komuta identity if present, otherwise password), `komuta`, `off` (only with `UI_ALLOW_INSECURE=1`) |
| `UI_PASSWORD` | — | Panel password from the environment (the setup-code step is skipped; it can be changed only here) |
| `UI_SETUP_CODE` | random | First setup code (written to the log if not given) |
| `UI_SESSION_SECRET` | random | So that sessions do not drop on restart |
| `UI_FORCE_PROTECTION` | `false` | Forbids turning protection off |
| `UI_ALLOWED_HOSTS`, `UI_ALLOWED_EMAILS` | — | Only in the Komuta identity modes |
| `BRIDGE_ENABLED`, `MCP_ENABLED`, `HOOKS_ENABLED` | off | Starting value of the surfaces |
| `BRIDGE_TOKEN`, `HOOK_SECRET` | — | Valid in addition to the panel token |
| `LLM_PROVIDER` | `auto` | Provider |
| `TELEGRAM_API_BASE` | Telegram | From the environment only |

## 12. Developer notes

- Pages: `src/ai_bot/ui/` (single page, hash routing: `index.html`, `app.js`, `widgets.js`, `pages/*.js`). The sign-in, first-setup and no-access pages come from the kit (`mount_ui(login_card_width=460, setup_card_width=460, setup_hint=...)`).
- API: `src/ai_bot/panel.py`; providers `providers.py`; network policy `netpolicy.py`; Anthropic adapter `llm.py`; live settings `runtime_config.py`. The identity, first-setup, protection and bridge-gate endpoints come from the vendored `stack_ui` (`/ui/api/auth*`, `/ui/api/bridge`).
- Tests: `tests/test_ui.py`, `tests/test_ui_v2.py`.


## 13. Language, helpers and the updating screen

- **Language (TR / EN):** On first start the wizard's first screen has you choose a language; the choice is stored on the server, and a new browser opens in the same language. You can change it at any time with the TR / EN button in the top bar. The translation dictionary is `src/ai_bot/ui/i18n-en.js` (the key is the Turkish text); `python scripts/i18n_extract.py --missing` lists what is missing.
- **Mini helpers:** For the Telegram token, the chat id, each LLM provider's key, the search providers and the panel password, a "How to?" button opens a step-by-step guide window (copyable values, links, notes). On the Channels page each channel has its own setup guide.
- **Updating screen:** While the server moves to a new version, requests can fail for a few seconds. The panel notices this, shows an "Updating" screen, and refreshes the page by itself when `/health` answers. Sessions do not drop: the session key is stored in the `DATA_DIR/ui-session.key` file.
- **Database:** During a version change the old and new instance can open the same SQLite file for a few seconds; the connection knows to wait 30 s (busy timeout) and to retry an open that stays locked, and it cleans up the WAL on close.
- **Search:** The default is `auto`: your own SearXNG, keyed providers (Tavily, Brave, Serper), keyless Parallel, DuckDuckGo, and public SearXNG are tried in that order; one that errors is skipped and remembered for 10-15 min.
