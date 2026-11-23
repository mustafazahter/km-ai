---
name: add-ai-tool
description: Give the ai-bot's AI a new ability by adding a plugins/*.py tool, with the right confirm/scope so money-moving tools always need the user's approval (Telegram button or channel code), plus a test that drives it through the fake LLM. Use when the user wants the bot to "be able to do X".
---

# AI'ya yeni araç ekle

Her `@tool` aynı anda AI'ya, `/bridge` ve `/mcp`'ye açılır. AI aracı adının `_`'li hâliyle görür (`not.ekle` -> `not_ekle`).

1. `plugins/<ad>.py` yaz (şablon: `plugins/ornek_arac.py`, ağ kullanan gerçek örnek: `plugins/haber_ozet.py`):
   ```python
   from stack_kit import ToolError, tool

   @tool("not.ekle", "Kısa bir not kaydeder / Saves a short note", scope="read")
   def ekle(metin: str) -> dict:
       if len(metin) > 200:
           raise ToolError("not çok uzun", code="invalid_args")
       return {"kaydedildi": metin}
   ```
   Ağa çıkan araçlarda ai-bot kapsayıcısını `get_stack().app_ai_bot` ile al ve `await app.fetcher.fetch(url)` kullan (örnek: `plugins/haber_ozet.py`).
   - Açıklama (docstring ya da 2. argüman) AI'nın aracı ne zaman seçeceğini belirler: net yaz.
   - Para hareketi / geri alınamaz iş: `confirm=True` ve `scope="trade"` (veya `"admin"`). Ajan bu araçları kullanıcı onaylamadan çalıştırmaz (Telegram'da **Onayla** düğmesi, WhatsApp/Teams/Slack'te `EVET 123456` kodu; e-postada ve onay kanalı olmayan turlarda araç reddedilir); `scope` trade/admin olan araç `confirm` bayrağı olmasa bile onay ister. Bunu gevşetme.
   - Dışarıya istek atacaksan `httpx` yerine `app.fetcher.fetch(url)` kullan (özel/loopback/metadata IP engelli).
   - Kullanıcıya hata: `raise ToolError("mesaj", code="kod")`. `chat_id` adlı parametre AI'dan gizlenir ve o anki sohbetle doldurulur.
2. Yüklendi mi:
   ```bash
   .venv/bin/python -m stack_kit tools
   ```
3. Test (`tests/test_<ad>.py`), sahte LLM ile AI'nın aracı çağırmasını dene:
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
   Onaylı araçlar için `tests/test_confirm_flow.py` kalıbını kopyala (onaysız çalışmadığını da test et).
4. Hepsini koş:
   ```bash
   .venv/bin/python -m pytest
   ```
