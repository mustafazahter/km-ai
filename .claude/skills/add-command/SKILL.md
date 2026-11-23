---
name: add-command
description: Add a slash command (/ping, /rapor ...) that works on EVERY channel of km-ai - Telegram, WhatsApp, Microsoft Teams, Slack and e-mail - from one handler, with the right read-only/confirmation behaviour and a test. Use when the user wants a new command people can type in chat.
---

# Komut ekle (her kanalda çalışır)

Komut = kullanıcının sohbete `/ad` (ya da `!ad`) yazınca AI'ya gitmeden çalışan işlem. Tek kayıt defteri (`stack.commands`) hem Telegram'ı hem diğer kanalları (WhatsApp, Teams, Slack, e-posta) besler; bir kez yazarsın, hepsinde çalışır. Hazır komutlar: `/help`, `/hafiza`, `/gorevler`, `/model`, `/sifirla` (`src/ai_bot/telegram_ui.py`). `/start` ve `/id` yalnız Telegram'dadır.

1. `plugins/<ad>.py` yaz (açılışta yüklenir):
   ```python
   from ai_bot.commands import command

   @command("ping", "Canlılık testi / liveness", readonly=True)
   async def ping(ctx):
       kanal = ctx.channel.name if getattr(ctx, "channel", None) else "telegram"
       return f"pong ({kanal}) {ctx.arg_text}".strip()
   ```
   - `ctx.reply`/`confirm`/`run_tool` kullanan komut `async def` olmalı; düz `def` komut ayrı iş parçacığında çalışır (diğer kanalları durdurmasın diye) ve yalnız metin hesaplayıp döndürebilir. Köprü araçlarında onay istemi argümanları gösterir, uzaktaki aracın kendi önizlemesini değil.
   - Dönüş değeri yanıt metnidir (`None` = yanıt yok). Metin `"Türkçe / English"` biçiminde olabilir.
   - `ctx`: `chat_id` (kanal kullanıcısı için kararlı negatif sayı), `args` (liste), `arg_text` (komuttan sonrası), `text`, `reply(metin)`, `confirm(metin)` (onay kodu ya da düğme; kanalda değilse `False`), `run_tool(ad, args)` (AI'nın araç döngüsüyle aynı onay kuralı: `confirm=True` ya da `trade`/`admin` kapsamlı araç ve köprü araçları, aynı gönderen kodla onaylamadan çalışmaz; e-postada reddedilir). Kanal komutlarında ek olarak `ctx.channel` vardır; Telegram'da yoktur.
   - Ad: küçük harf, rakam, `_`, en çok 32 karakter. Aynı ad ikinci kez kayıtlanırsa hata verir.
2. **`readonly`** kuralı (güvenlik): `readonly=True` yalnız hiçbir şeyi değiştirmeyen komut için. E-postada gönderen doğrulanamadığından (`can_confirm` yok) yalnız okuma komutları çalışır; `readonly` işaretlenmeyen komut orada "bu kanalda kapalı" yanıtı alır. `readonly` yalnız komutun kendi davranışı içindir; `ctx.run_tool` onay kuralını yine ayrıca uygular. Kayıt komutun yazıldığı stack'e aittir. Silen, değiştiren, para/iş tetikleyen komutta `readonly` koyma ve kullanıcıya sormak için `await ctx.confirm("... yapılsın mı?")` kullan (Telegram'da düğme, diğer kanallarda `EVET 123456` kodu, e-postada her zaman `False`).
3. Giriş kuralları (değiştirme): komut yalnız izin listesindeki gönderenlerden gelir (kanal hattı önce denetler); `/ad` ve `!ad` ikisi de çalışır (Slack `/...` mesajını kendi komutu sayar, orada `!ad` yaz); kayıtlı olmayan ad normal mesajdır ve AI'ya gider; komut `ToolError` fırlatırsa mesajı (sırlar maskelenmiş) kullanıcıya gider; başka her hata kullanıcıya genel "Komut çalışırken hata oluştu" olarak gider, ayrıntı yalnız günlüğe yazılır (sır ve gövde yazılmaz).
4. Test (`tests/test_<ad>.py`), kanal düzeneğiyle (`tests/test_channel_commands.py` kalıbı):
   ```python
   import asyncio
   from stack_kit import use_stack
   from ai_bot import commands
   from ai_bot.channels.base import Inbound
   from tests.test_channels_env import ALICE, chenv  # noqa: F401

   def test_ping_on_whatsapp(chenv):
       chenv.whatsapp()
       with use_stack(chenv.stack):
           @commands.command("ping", "test", readonly=True)
           def ping(ctx):
               return "pong"
       ch = chenv.mgr.channels["whatsapp"]
       sent = []
       async def send(ctx, text): sent.append(text)
       ch.send = send
       asyncio.run(ch.process(Inbound(sender=ALICE, text="/ping", msg_id=None, ctx={})))
       assert sent == ["pong"] and chenv.llm.requests == []   # AI'ya gitmedi
   ```
   E-posta davranışı için `chenv.set(EMAIL_ENABLED=True)` ve `chenv.mgr.channels["email"]`.
   ```bash
   .venv/bin/python -m pytest -q tests/test_channel_commands.py tests/test_<ad>.py
   ```
5. Belgele: `README.md` ve `README.en.md` "Komutlar" tablosu, `docs/GELISTIRICI.md`. Telegram komut menüsü (`setMyCommands`) bot başlarken kayıt defterinden yayınlanır; yeni komut menüde yeniden başlatmadan sonra görünür.

Yalnız Telegram'a özgü bir şey gerekiyorsa (düğme, `message` alanı) kitin `@command`'ini (`from stack_kit.telegram import command`) kullan; o komut da kayıt defterine girer ve diğer kanallarda `ctx.message`/`ctx.user_id` boş gelir, buna göre yaz.
