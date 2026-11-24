[English](../DEVELOPER.md) · Türkçe

# Geliştirici rehberi

> EN: Extend ai-bot: write plugins (`plugins/*.py` with `@tool`), use the SSRF-safe fetcher, add bridges,
> embed ai-bot into another stack with `ai_bot.setup(stack, bot)`, run the tests with fake LLM/Telegram.

## Ortam

```bash
uv venv --python 3.12
uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Testler gerçek ağa çıkmaz: `tests/fakes.py` sahte LLM (`FakeLLM`), sahte Telegram (`FakeTelegram`) ve sabit DNS çözücü içerir; `tests/conftest.py::make_harness` hepsini kurar.

## Plugin yazma

`plugins/*.py` açılışta yüklenir. Her `@tool` AI'ya, `/bridge`'e ve `/mcp`'ye açılır.

```python
from stack_kit import ToolError, tool

@tool("not.ekle", "Kısa bir not kaydeder / Saves a short note", scope="read")
def ekle(metin: str) -> dict:
    if len(metin) > 200:
        raise ToolError("not çok uzun", code="invalid_args")
    return {"kaydedildi": metin}
```

Doğrula ve çağır:

```bash
.venv/bin/python -m stack_kit tools
.venv/bin/python -m stack_kit call ornek.zar '{"sides": 6, "count": 2}'
```

Kurallar:

- **Açıklama** AI'nın aracı ne zaman seçeceğini belirler; net yaz (TR + EN iyi çalışır).
- **Para / geri alınamaz iş** → `confirm=True` ve `scope="trade"` (ya da `"admin"`). Ajan bu araçları kullanıcı onaylamadan çalıştırmaz (Telegram'da düğme, diğer kanallarda kod). `scope` trade/admin olan araç `confirm` olmasa da onay ister.
- **Dış istek** → `get_stack().app_ai_bot.fetcher.fetch(url)` (SSRF korumalı). Örnek: `plugins/haber_ozet.py`.
- **`chat_id`** adlı parametre AI'dan gizlenir, o anki sohbetle doldurulur.
- **Durum**: `app.db` (SQLite, `Database`), `app.settings`, `app.scheduler`, `app.memory`, `app.deliver(chat_id, text)`.
- Bir aracı AI'dan gizlemek için: `from ai_bot.agent import HIDDEN_FROM_AI; HIDDEN_FROM_AI.add("benim.aracim")`.
- Aracın dış/güvenilmeyen metin döndürüyorsa (web, e-posta, RSS): `from ai_bot.agent import UNTRUSTED_OUTPUT_TOOLS; UNTRUSTED_OUTPUT_TOOLS.add("benim.aracim")`. Böylece aynı turdaki hafıza kaydı ve zamanlama kullanıcı onayı ister. Kalıcı bir şey yazan aracını `PERSISTENT_TOOLS`'a ekle.

Test kalıbı: `.claude/skills/add-ai-tool`, onaylı araçlar için `tests/test_confirm_flow.py`.

## Komut ekleme (her kanalda)

Komutlar tek kayıt defterinde (`stack.commands`) durur; Telegram'ı kitin botu, diğer kanalları (WhatsApp, Teams, Slack, e-posta) `src/ai_bot/commands.py` besler. Bir kez yaz, hepsinde çalışır:

```python
# plugins/ping.py
from ai_bot.commands import command

@command("ping", "Canlılık testi / liveness", readonly=True)
async def ping(ctx):
    return "pong"
```

- `/ad` ya da `!ad` (Slack `/...` mesajını kendi komutu sayar). Kayıtlı olmayan ad normal mesajdır ve AI'ya gider.
- `ctx`: `chat_id`, `args`, `arg_text`, `text`, `reply()`, `confirm()`, `run_tool()`; kanal komutlarında ayrıca `ctx.channel`.
- `readonly=True`: hiçbir şeyi değiştirmeyen komut. E-postada gönderen doğrulanamadığından yalnız okuma komutları (`/help`, argümansız `/hafiza` `/gorevler` `/model`, `readonly` işaretliler) çalışır; diğerleri "bu kanalda kapalı" yanıtı alır.
- Değiştiren/silen komutta `await ctx.confirm("... yapılsın mı?")`: Telegram'da düğme, diğer kanallarda `EVET 123456` kodu, e-postada her zaman `False`.
- Yalnız Telegram'a özgü ihtiyaç için kitin `from stack_kit.telegram import command`'ı kullanılabilir (kayıt defterine girer; kanallarda `ctx.message` boş gelir). `/start` ve `/id` Telegram'a özeldir.
- Beceri ve test kalıbı: `.claude/skills/add-command/SKILL.md`, `tests/test_channel_commands.py`.

## Köprü ekleme

Başka bir stack'in araçlarını AI'ya açmak için iki env yeter (kit birleştirir):

```
BRIDGE_<AD>_URL=https://diger-stack.example.com
BRIDGE_<AD>_TOKEN=<o stack'in BRIDGE_TOKEN'ı>
```

Ayrıntı: [BRIDGES.md](BRIDGES.md).

## Başka bir stack'e gömme: `ai_bot.setup`

```python
import ai_bot
from stack_kit import configure, run
from stack_kit.telegram import TelegramBot

stack = configure(name="benim-stack")
bot = TelegramBot(stack=stack)
ai_bot.setup(stack, bot)            # araçlar, komutlar, serbest metin, zamanlayıcı
stack.background(bot.run)            # bot verildiyse yaşam döngüsü çağıranındır
stack.on_shutdown(bot.aclose)
run(stack)
```

- `bot=None`: ai-bot `TELEGRAM_BOT_TOKEN`'dan kendi botunu kurar ve başlatır.
- Var olan komutlar (`/help` gibi) korunur; ai-bot yalnız eksik olanları ekler.
- Aynı süreçteki araçlar (örn. `mt5.*`) AI'ya köprüsüz görünür, onay kuralı aynıdır.

## Örnek senaryo: kendi projeni köprüyle bağla

Çalışan, testli bir örnek var: [`examples/ornek-stack/kargo_stack.py`](../../examples/ornek-stack/kargo_stack.py), küçük bir kargo takip stack'i (okuma aracı, onay isteyen iade aracı, kargo firmasının webhook'u ve `kargo.gecikti` olayı). `BRIDGE_KARGO_URL` + `BRIDGE_KARGO_TOKEN` ile km-ai'ye bağlanır; araçlar `kargo_durum`, `kargo_iade_baslat` olarak modele görünür, iade yalnız kullanıcı onayıyla çalışır. Adım adım anlatım ana README'de: **Geliştirici alanı > Örnek kullanım senaryosu**. Test: `tests/test_example_stack.py` (stack'i km-ai'ye gerçek köprü istemcisiyle bağlar).

## Proje haritası

| Yol | Ne |
|---|---|
| `src/ai_bot/agent.py` | Araç döngüsü, onay kuralı, istem enjeksiyonu kilidi, sistem istemi |
| `src/ai_bot/llm.py` | OpenAI uyumlu istemci |
| `src/ai_bot/builtin_tools.py` | Yerleşik araçlar |
| `src/ai_bot/telegram_ui.py` | Komutlar ve serbest metin |
| `src/ai_bot/safefetch.py` | SSRF korumalı getirici |
| `src/ai_bot/search.py` | Arama sağlayıcıları |
| `src/ai_bot/scheduler.py`, `timeparse.py` | Zamanlayıcı, cron |
| `src/ai_bot/memory.py`, `db.py` | Hafıza, SQLite |
| `scripts/entrypoint.sh` | Konteyner girişi: yalnız `/data` yazılabilirlik uyarısı (sahipliğe dokunmaz) |
| `stack_kit/` | Vendor edilmiş kit (burada düzenleme) |
