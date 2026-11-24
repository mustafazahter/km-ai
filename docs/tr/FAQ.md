[English](../FAQ.md) · Türkçe

# SSS

> EN: FAQ — costs, models, privacy, search reliability, groups, unified service with mt5-trader.

**Ne kadar tutar?** Komuta: `dz-dev` aylık ~9 USD + disk `Small`. LLM: sağlayıcına ve modeline göre; `deepseek/deepseek-v4.1-flash-1017` gibi küçük modeller günlük kişisel kullanımda düşük maliyetlidir (23 Kasım 2026'da OpenRouter'da milyon token başına yaklaşık 0,03 USD girdi / 0,20 USD çıktı; güncel fiyat için OpenRouter'a bak). Kesin fiyatı Komuta planı ve sağlayıcının paneli gösterir.

**Hangi model?** Araç çağırmayı destekleyen herhangi bir model. Önerilen `deepseek/deepseek-v4.1-flash-1017` (OpenRouter; DeepSeek V4.1 flash): sihirbaz bunu önerir. Yerleşik varsayılan `openai/gpt-4o-mini` çalışan kurulumlar değişmesin diye aynı kaldı. Hep en yeni flash için `~deepseek/deepseek-flash-latest`. Bkz. [LLM-PROVIDERS.md](LLM-PROVIDERS.md).

**Yerel model kullanabilir miyim?** Evet: `LLM_BASE_URL=http://localhost:11434/v1` (Ollama) gibi; anahtar boş kalabilir. Komuta'da yerel model yoktur; erişilebilir bir sunucu gerekir.

**Web araması bazen sonuç vermiyor.** Varsayılan DuckDuckGo anahtarsızdır ve otomatik istekleri bazı ağlardan reddeder. `SEARCH_PROVIDER=tavily` (veya `brave`, `searxng`) kullan: `examples/komuta-arama-saglayicili.yaml`.

**Verilerim nerede?** `DATA_DIR/ai-bot.sqlite3` (Komuta'da servisin `/data` diski): sohbet geçmişi (kısa), anılar, görevler. Mesajlar LLM sağlayıcına gider; gizlilik politikasını oradan kontrol et.

**Geçmişi nasıl silerim?** `/sifirla` sohbet geçmişini, `/hafiza temizle` anıları siler.

**Grupta çalışır mı?** Evet; grubun (eksi işaretli) kimliğini `ALLOWED_CHAT_IDS`'e ekle ve BotFather'da gizlilik modunu kapat. Gruptaki herkes botu kullanabilir ve onay düğmesine basabilir; para araçları bağlıysa grubu dikkatli seç.

**AI benim yerime işlem açabilir mi?** Yalnız sen Telegram'da **Onayla**'ya basarsan. AI onayı kendisi veremez. Bkz. [SECURITY.md](SECURITY.md#3-onay-kuralı-para--işlem).

**mt5-trader ile tek serviste çalışır mı?** Evet, mt5-trader `ai_bot.setup(stack, bot)` ile ai-bot'u kendi sürecine ekleyebilir; ayrıntı mt5-trader reposunda. Ayrı Stack olarak köprü: [BRIDGES.md](BRIDGES.md).

**`/start` farklı bir metin göstersin istiyorum.** `/start` ve `/id` kit'in yerleşikleridir (izin listesi boşken de çalışmaları gerektiği için). Karşılama metni `/help`'tedir: `src/ai_bot/telegram_ui.py` → `HELP`.

**Aynı token ile iki örnek çalıştırdım, 409 hatası.** Telegram aynı bot için tek polling bağlantısına izin verir; bir örneği kapat.

**Docker imajı test edildi mi?** Bu depoda henüz build edilerek denenmedi; Python testleri ve gerçek uvicorn ile köprü/MCP doğrulaması yapıldı.
