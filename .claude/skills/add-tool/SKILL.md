---
name: add-tool
description: Add a tool to this stack as a plugins/*.py file with @tool, plus a pytest test. Use when the user wants a new command/tool callable over the bridge, MCP or Telegram.
---

# Add a tool (plugin)

1. Create `plugins/<name>.py` (or get the template with `python -m stack_kit new-plugin <name>`):
   ```python
   from stack_kit import ToolError, tool

   @tool("<name>.hello", "Says hello", scope="read")
   def hello(who: str = "world") -> dict:
       return {"message": f"Hello {who}!"}
   ```
   - Name is `<name>.<action>`, lower case; over MCP it becomes `<name>_<action>`.
   - Money movement / irreversible work → `confirm=True` (mandatory). `scope`: `read` | `trade` | `admin`.
   - Error shown to the user: `raise ToolError("message", code="code")`. Never return secrets.
2. Does it show up, does it work:
   ```bash
   python -m stack_kit tools
   python -m stack_kit call <name>.hello '{"who": "Ada"}'
   ```
3. Test: `tests/test_<name>.py`:
   ```python
   import asyncio

   from stack_kit import Stack, load_plugins


   def test_hello_tool():
       stack = Stack("test")
       load_plugins(stack, "plugins")
       assert stack.plugin_errors == []
       result = asyncio.run(stack.call("<name>.hello", {"who": "Ada"}))
       assert result == {"ok": True, "result": {"message": "Hello Ada!"}}
   ```
   ```bash
   python -m pytest -q tests
   ```
4. For a `confirm=True` tool, test both steps: the first call returns `needs_confirmation`, the second with `confirm_id=...` returns `ok`.
