---
name: add-tool
description: Add a tool to this stack as a plugins/*.py file with @tool, plus a pytest test. Use when the user wants a new command/tool callable over the bridge, MCP or Telegram.
---

# Araç ekle (plugin)

1. `plugins/<ad>.py` oluştur (ya da `python -m stack_kit new-plugin <ad>` ile şablon al):
   ```python
   from stack_kit import ToolError, tool

   @tool("<ad>.hello", "Selam verir / Says hello", scope="read")
   def hello(who: str = "dünya") -> dict:
       return {"message": f"Merhaba {who}!"}
   ```
   - Ad `<ad>.<eylem>`, küçük harf; MCP'de `<ad>_<eylem>` olur.
   - Para hareketi / geri alınamaz iş → `confirm=True` (zorunlu). `scope`: `read` | `trade` | `admin`.
   - Kullanıcıya hata: `raise ToolError("mesaj", code="kod")`. Sır döndürme.
2. Görünüyor mu, çalışıyor mu:
   ```bash
   python -m stack_kit tools
   python -m stack_kit call <ad>.hello '{"who": "Ada"}'
   ```
3. Test — `tests/test_<ad>.py`:
   ```python
   import asyncio

   from stack_kit import Stack, load_plugins


   def test_hello_tool():
       stack = Stack("test")
       load_plugins(stack, "plugins")
       assert stack.plugin_errors == []
       result = asyncio.run(stack.call("<ad>.hello", {"who": "Ada"}))
       assert result == {"ok": True, "result": {"message": "Merhaba Ada!"}}
   ```
   ```bash
   python -m pytest -q tests
   ```
4. `confirm=True` araç için testte iki adımı da dene: ilk çağrı `needs_confirmation`, ikincisi `confirm_id=...` ile `ok`.
