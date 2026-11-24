English · [Türkçe](tr/BRIDGES.md)

# Bridges: mt5-trader, n8n, MCP

> Connect ai-bot to other stacks (their tools become AI tools; trade tools always need a Telegram button press),
> to n8n (events out, `ai.ask` in) and to MCP clients (Claude Desktop etc.).

## Security rule (unchangeable)

When the AI calls another stack's tool:

- If the tool returns `needs_confirmation`, the bot shows a **preview + Confirm/Cancel** button on Telegram; only if you press **Confirm** is a second call made with the same arguments and a single-use `confirm_id`.
- `confirm_id` is never shown to the model; even if the model sends `confirmed`/`confirm_id`, it is discarded.
- A tool with `scope: trade` or `admin` asks for confirmation with a button first, even if it has no `confirm` flag.
- The button can only be pressed from the same (allowed) chat; if it is not pressed within 120 s, no action is taken.
- If there is no confirmation channel (e.g. `ai.ask` was called from the bridge and no channel can give confirmation), these tools **do not run**.

This behavior is tested with `tests/test_confirm_flow.py`.

## With mt5-trader

### Two separate Stacks (bridge)

`examples/komuta-mt5-ile.yaml`:

```yaml
- name: BRIDGE_MT5_URL
  valueFrom:
    service:
      stack: mt5-trader        # name of the mt5-trader Stack
      name: mt5-trader         # service name in that Stack
      property: url
- name: BRIDGE_MT5_TOKEN
  sensitive: true
  valueFrom:
    secret: mt5-bridge-token   # mt5-trader's BRIDGE_TOKEN value (prompt Secret)
```

- The target service **must** be `network.public: true`; `valueFrom.service` only connects to public services (the plan gives `SERVICE_REFERENCE_NOT_PUBLIC`).
- Publish mt5-trader first, then plan ai-bot.
- Locally: put `BRIDGE_MT5_URL=https://...` and `BRIDGE_MT5_TOKEN=...` in `.env`.

Then on Telegram: "What is the price of gold?" (a read tool, no confirmation), "Buy 0.01 lot gold" (a confirmation button appears).

### Single service (combined)

mt5-trader calls `ai_bot.setup(stack, bot)` in its own process; the tools sit in the same registry without a bridge, and the confirmation rule is the same. The mt5-trader repo describes the setup; signature: [DEVELOPER.md](DEVELOPER.md#embedding-in-another-stack-ai_botsetup).

## With n8n

`examples/komuta-n8n-ile.yaml`.

**ai-bot → n8n (events):** `WEBHOOKS_OUT_URL` (the address of the n8n Webhook node) + `WEBHOOKS_OUT_SECRET`. Events: `ai.reply`, `schedule.fired`, `memory.remembered`, `memory.forgotten`. Headers: `X-Stack-Event`, `X-Stack-Timestamp`, `X-Stack-Signature: sha256=<HMAC(secret, timestamp + "." + body)>`. Verify the signature in n8n with a Code node (kit doc `stack-kit/docs/RECIPES.md`).

**n8n → ai-bot (question):** an HTTP Request node:

```
POST https://<ai-bot-adresi>/bridge/call/ai.ask
Authorization: Bearer <BRIDGE_TOKEN>
Content-Type: application/json

{"args": {"prompt": "Bugünün döviz kurlarını özetle", "notify": true}}
```

The answer is `{"ok": true, "result": {"answer": "...", "chat_id": ...}}`. `notify: true` also sends the answer to Telegram. Other tools are called in the same format (`/bridge/call/memory.remember`, `/bridge/call/schedule.add` ...). Tool list: `GET /bridge/manifest` (needs a token).

**Making an n8n flow an AI tool:** set up a flow with a Webhook trigger in n8n and wrap it with a small plugin (inside the plugin `app.fetcher` works for public addresses; if n8n is on a private network, use your own `httpx` client and take the address from env).

## MCP clients (Claude Desktop etc.)

All of ai-bot's tools are exposed over `POST /mcp` (MCP Streamable HTTP, JSON): `web_search`, `web_read`, `memory_*`, `schedule_*`, `ai_ask`, plugin tools. `Authorization: Bearer <BRIDGE_TOKEN>` is required. Tools that need confirmation are always two-step in MCP. Client configuration: `.claude/skills/add-mcp-client`.

```bash
curl -s -H "Authorization: Bearer $BRIDGE_TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:8091/mcp
```
