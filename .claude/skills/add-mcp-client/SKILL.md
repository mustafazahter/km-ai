---
name: add-mcp-client
description: Connect Claude (Claude Code or Claude Desktop) to this stack's /mcp endpoint and check the handshake with curl. Use when the user wants an AI client to use the stack's tools.
---

# Add this stack's /mcp endpoint to Claude

1. Is the endpoint working (with the service address and `BRIDGE_TOKEN`):
   ```bash
   T=<BRIDGE_TOKEN>; U=https://<service-address>
   H=(-H "Authorization: Bearer $T" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
   curl -s -X POST $U/mcp "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
   curl -s -X POST $U/mcp "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
   ```
   Expected: `initialize` → `serverInfo.name` is the stack name; `tools/list` → names without dots such as `mt5_buy`.
2. Add it to the client (client configurations could not be verified while the kit was developed; check the client's current documentation):
   - Claude Code: `claude mcp add --transport http <name> $U/mcp --header "Authorization: Bearer <BRIDGE_TOKEN>"`
   - Claude Desktop: put the `npx -y mcp-remote $U/mcp --header "Authorization:${AUTH_HEADER}"` command into `claude_desktop_config.json` → `mcpServers`, with `env.AUTH_HEADER = "Bearer <BRIDGE_TOKEN>"`.
3. `confirm=True` tools always take two steps over MCP: the first call returns a preview + `_confirm_id`; the second call is made only if the user explicitly approves.

Security: do not commit the token. If you hand the token to an AI client, set `BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP=true` on the service.
