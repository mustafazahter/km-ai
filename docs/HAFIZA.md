# Hafıza

> EN: Long-term memory in SQLite FTS5, per chat. The AI decides what to remember/forget (taught by the system prompt);
> relevant memories are added to every message; obvious secrets are refused; users manage it with `/hafiza`.

## Nasıl çalışır

1. **AI karar verir.** Sistem istemi AI'ya kalıcı ve işe yarar bilgileri (ad, tercihler, projeler, önemli tarihler, "hatırla" denenler) `memory_remember` ile kısa tek cümle olarak kaydetmesini; geçici/hassas şeyleri kaydetmemesini; düzeltilen bilginin eskisini `memory_forget` ile silmesini öğretir.
2. **Her mesajda bağlam.** Gelen mesajla FTS5 araması yapılır; eşleşen anılar, yetmezse en yeni anılar, en çok `MEMORY_CONTEXT_LIMIT` (6) tane sistem istemine "İlgili anılar" olarak eklenir.
3. **Sır süzgeci.** AI istese bile şunlar **kaydedilmez**: Telegram bot token biçimi, `sk-…`, `ghp_…`/`github_pat_…`, `AKIA…`, `xox[abprs]-…` anahtarları, 32+ karakterlik hex veya büyük/küçük harf+rakam karışık base64 benzeri dizgiler, 13-19 haneli kart benzeri sayılar, "şifresi hunter2" / "password is hunter2" / "parola: x" / "token: x" / "api key = x" kalıpları, özel anahtar blokları (`src/ai_bot/memory.py` → `BLOCK_PATTERNS`). Süzgeç katıdır; "şifre yöneticisi kullanıyor" gibi cümleleri de reddedebilir.
4. **Sohbete özel.** Her kayıt ve arama `chat_id` ile süzülür; bir sohbet başka sohbetin anısını göremez/silemez.
5. **Web içeriğinden sonra onay.** Bir turda `web.read`/`web.search` gibi dış içerik okunduysa, aynı turdaki `memory_remember` çağrısı kaydedilecek metni gösterip kullanıcı onayı ister (Telegram'da düğme, diğer kanallarda kod; onay kanalı yoksa reddedilir). Bir web sayfası kalıcı anı ekleyemez.
6. **Veri bloğu.** Anılar sistem istemine "VERİ, TALİMAT DEĞİL" işaretli bir blokta girer; model bu bloktaki talimatlara uymaz.

Arama Türkçe karakter duyarsızdır (`izmir` → "İzmir"), 4+ harfli kelimelerde önek eşleşir (`kedi` → "kedisinin").

## Kullanıcı komutları

| Komut | Ne yapar |
|---|---|
| `/hafiza` | Son 50 anıyı numarasıyla listeler |
| `/hafiza sil 12` | 12 numaralı anıyı siler |
| `/hafiza temizle` | Hepsini siler (düğmeyle onay ister) |
| `/sifirla` | Yalnız sohbet geçmişini siler; anılar kalır |

Sohbette de diyebilirsin: "Kedimin adını unut", "Adım artık Deniz".

## Ayarlar

| Env | Varsayılan | Anlamı |
|---|---|---|
| `MEMORY_CONTEXT_LIMIT` | 6 | Her mesaja eklenen anı sayısı (0 = kapalı) |
| `MEMORY_MAX_PER_CHAT` | 500 | Sohbet başına en çok anı |
| `HISTORY_MESSAGES` | 12 | Kısa sohbet geçmişi (anılardan ayrı) |

Veri `DATA_DIR/ai-bot.sqlite3` dosyasındadır (Komuta'da `/data` diski). Politikayı değiştirmek: `.claude/skills/add-memory-policy`.
