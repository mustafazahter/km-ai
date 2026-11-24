---
name: add-ai-tool
description: Give the ai-bot's AI a new ability by adding a plugins/*.py tool, with the right confirm/scope so money-moving tools always need the user's approval (Telegram button or channel code), plus a test that drives it through the fake LLM. Use when the user wants the bot to "be able to do X".
---

# Add a new tool for the AI

Every `@tool` is exposed to the AI, `/bridge` and `/mcp` at the same time. The AI sees a tool under its name with `_` instead of `.` (`not.ekle` -> `not_ekle`).

1. Write `plugins/<name>.py` (template: `plugins/ornek_arac.py`, a real example that uses the network: `plugins/haber_ozet.py`):
   ```python
   from stack_kit import ToolError, tool

   @tool("not.ekle", "Kısa bir not kaydeder / Saves a short note", scope="read")
   def ekle(metin: str) -> dict:
       if len(metin) > 200:
           raise ToolError("not çok uzun", code="invalid_args")
       return {"kaydedildi": metin}
   ```
   For tools that go out to the network, get the ai-bot container with `get_stack().app_ai_bot` and use `await app.fetcher.fetch(url)` (example: `plugins/haber_ozet.py`).
   - The description (docstring or the 2nd argument) decides when the AI picks the tool: write it clearly.
   - Money movement / irreversible work: `confirm=True` and `scope="trade"` (or `"admin"`). The agent does not run these tools without the user's approval (a **Onayla** (Confirm) button in Telegram, an `EVET 123456` code in WhatsApp/Teams/Slack; on e-mail and in turns with no approval channel the tool is refused); a tool whose `scope` is trade/admin asks for approval even without the `confirm` flag. Do not loosen this.
   - For outbound requests use `app.fetcher.fetch(url)` instead of `httpx` (private/loopback/metadata IPs are blocked).
   - Error to the user: `raise ToolError("message", code="code")`. A parameter named `chat_id` is hidden from the AI and filled with the current chat.
2. Check it loaded:
   ```bash
   .venv/bin/python -m stack_kit tools
   ```
3. Test (`tests/test_<name>.py`): have the AI call the tool through the fake LLM:
   ```python
   import asyncio
   from stack_kit import load_plugins
   from tests.conftest import CHAT
   from tests.fakes import reply, tool_call

   def test_ai_uses_my_tool(make_harness):
       h = make_harness([reply(calls=[tool_call("ornek_zar", {"sides": 6})]), reply("Attım.")])
       load_plugins(h.stack, "plugins")
       assert asyncio.run(h.app.run_ai(CHAT, "zar at")) == "Attım."
   ```
   For confirmed tools copy the pattern in `tests/test_confirm_flow.py` (also test that the tool does not run without approval).
4. Run everything:
   ```bash
   .venv/bin/python -m pytest
   ```
