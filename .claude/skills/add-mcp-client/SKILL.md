---
name: add-mcp-client
description: Connect Claude (Claude Code or Claude Desktop) to this stack's /mcp endpoint and check the handshake with curl. Use when the user wants an AI client to use the stack's tools.
---

# Claude'a bu stack'in /mcp ucunu ekle

1. Uç çalışıyor mu (servis adresi ve `BRIDGE_TOKEN` ile):
   ```bash
   T=<BRIDGE_TOKEN>; U=https://<servis-adresi>
   H=(-H "Authorization: Bearer $T" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
   curl -s -X POST $U/mcp "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
   curl -s -X POST $U/mcp "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
   ```
   Beklenen: `initialize` → `serverInfo.name` stack adı; `tools/list` → `mt5_buy` gibi noktasız adlar.
2. İstemciye ekle (istemci yapılandırmaları kit geliştirilirken doğrulanamadı; istemcinin güncel belgesine bak):
   - Claude Code: `claude mcp add --transport http <ad> $U/mcp --header "Authorization: Bearer <BRIDGE_TOKEN>"`
   - Claude Desktop: `npx -y mcp-remote $U/mcp --header "Authorization:${AUTH_HEADER}"` komutunu `claude_desktop_config.json` → `mcpServers` içine yaz, `env.AUTH_HEADER = "Bearer <BRIDGE_TOKEN>"`.
3. `confirm=True` araçlar MCP'de her zaman iki adımdır: ilk çağrı önizleme + `_confirm_id`; ikinci çağrı yalnız kullanıcı açıkça onaylarsa yapılır.

Güvenlik: token'ı commit'leme. Token'ı bir AI istemcisine veriyorsan serviste `BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP=true` ayarla.
