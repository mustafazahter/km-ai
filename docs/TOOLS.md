English · [Türkçe](tr/TOOLS.md)

# Built-in tools

> Built-in tools. Each is a stack_kit `@tool`: visible to the AI (dots become underscores), callable at
> `POST /bridge/call/<name>` and over MCP. `chat_id` is hidden from the AI and filled with the current chat.

Every tool comes with the kit's `@tool` registration; it can be called from three places:

| From | Name | Example |
|---|---|---|
| AI (Telegram chat) | `web_search` | "What is the dollar rate today?" |
| HTTP bridge | `web.search` | `POST /bridge/call/web.search` body `{"args": {...}}` |
| MCP | `web_search` | Claude Desktop etc. `tools/call` |

The `chat_id` parameter is hidden from the AI and filled with the current chat. A bridge/MCP caller may supply it; if it does not, the first entry of `ALLOWED_CHAT_IDS` is used, and an ID that is not in the list is rejected (`forbidden`). If `ALLOWED_CHAT_IDS` is empty, all tools that take `chat_id` (`memory.*`, `schedule.*`, `ai.ask`) are disabled.

## web.search

| Argument | Type | Default |
|---|---|---|
| `query` | text (1-400) | — |
| `max_results` | 1-10 | 5 |

Returns: `{"provider", "results": [{"title", "url", "snippet"}]}`. The provider is selected with `SEARCH_PROVIDER`:

| Provider | Setting | Note |
|---|---|---|
| `auto` (default) | — | **Automatic mode:** tries the free channels in order, skips any that fails and remembers that for 10-15 minutes. Order: your own SearXNG (if any), Tavily/Brave/Serper with a key entered, keyless Parallel, DuckDuckGo, then public SearXNG servers picked from searx.space (A/B rated, JSON enabled; at most 5 attempts). Public servers are called with a strict network policy (public addresses only). Your queries may go to third-party servers. |
| `duckduckgo` | — | Keyless, HTML scraping; **best effort**. Some networks reject automated requests with bot protection (observed on a development machine); in that case a `search_unavailable` error is returned. |
| `tavily` | `TAVILY_API_KEY` | Recommended; has a free quota. |
| `parallel` | — | Parallel's hosted search service (MCP, Streamable HTTP): **no account or key needed**, returns real web results and excerpts. The rate limit for anonymous use is low (the limit is not published). Queries go to Parallel. |
| `brave` | `BRAVE_API_KEY` | Brave Search API. Sign-up may ask for a card; terms change. |
| `serper` | `SERPER_API_KEY` | A service that returns Google results as JSON (serper.dev); sign-up gives a one-time free query allowance, no card needed. |
| `searxng` | `SEARXNG_URL` | Your own SearXNG server (`format=json` must be enabled). The address is an operator setting and does not go through the SSRF filter. |

New provider: `.claude/skills/add-search-provider`.

## web.read

| Argument | Type | Default |
|---|---|---|
| `url` | http(s) URL | — |
| `max_chars` | 200-50000 | `WEB_READ_MAX_CHARS` (8000) |

Returns: `{"url", "title", "text", "truncated"}`. HTML is converted to readable text (script/style/nav are dropped); plain text, JSON and XML are returned as they are.

**SSRF protection** (cannot be loosened; details in [SECURITY.md](SECURITY.md)): http/https only, no credentials in the URL, allowed ports (`WEB_READ_PORTS`, default 80/443/8080/8443), after DNS resolution **every** address must be public (loopback, private network, link-local/metadata, CGNAT, ULA and IPv4-mapped IPv6 are blocked), the connection is pinned to the checked IP, every redirect is checked again (at most `WEB_READ_MAX_REDIRECTS`=5), and the size is limited by `WEB_READ_MAX_BYTES` (2 MB) and the time by `WEB_READ_TIMEOUT` (15 s).

## schedule.add · schedule.list · schedule.cancel

Details: [SCHEDULING.md](SCHEDULING.md).

| Tool | Arguments |
|---|---|
| `schedule.add` | `when` (cron / relative / ISO), `text`, `kind` (`message`/`prompt`), `description` |
| `schedule.list` | — |
| `schedule.cancel` | `task_id` |

## memory.remember · memory.recall · memory.forget · memory.list

Details: [MEMORY.md](MEMORY.md).

| Tool | Arguments |
|---|---|
| `memory.remember` | `content` (one sentence, ≤500), `tags` |
| `memory.recall` | `query` (empty = the newest), `limit` |
| `memory.forget` | `memory_id` |
| `memory.list` | `limit`, `offset` |

## ai.ask (bridge/MCP only)

Asks the AI a question from outside; the AI works with its tools and returns the answer. If `notify: true`, the answer is also sent to Telegram (only to `ALLOWED_CHAT_IDS` chats). To avoid loops it is **not shown** to the AI itself; `schedule.add` is also unavailable in this turn. Tools that require confirmation are still asked with user confirmation (a button on Telegram, a code on other channels); if there is no confirmation channel, they do not run.

```bash
curl -s -H "Authorization: Bearer $BRIDGE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"args":{"prompt":"zar at"}}' http://127.0.0.1:8091/bridge/call/ai.ask
```

## Plugin tools (examples)

`haber.basliklar` (RSS headlines), `ornek.zar`, `ornek.bagis` (a confirmed example). Your own tool: [DEVELOPER.md](DEVELOPER.md).

## Events

To `GET /bridge/events` and the `WEBHOOKS_OUT` targets: `ai.reply` (`chat_id`, `prompt`, `answer`, `scheduled`), `schedule.fired`, `memory.remembered`, `memory.forgotten`.
