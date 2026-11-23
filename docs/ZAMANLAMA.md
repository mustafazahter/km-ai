# Zamanlanmış görevler

> EN: Persistent scheduler (SQLite) with cron, relative ("in 10 minutes", "10 dakika sonra") and ISO times in `TZ`.
> A task either sends fixed text (`message`) or runs an AI prompt with tools when due (`prompt`).

## Örnek sohbetler

- "20 dakika sonra fırını kapatmamı hatırlat" → `message`, tek sefer
- "Her sabah 9'da teknoloji haberlerini özetle" → `prompt`, cron `0 9 * * *`
- "Hafta içi 18:30'da su içmemi hatırlat" → `message`, cron `30 18 * * 1-5`
- "1 Aralık 2026 saat 10'da faturayı hatırlat" → `message`, ISO `2026-12-01T10:00`

## Zaman biçimleri (`when`)

| Biçim | Örnek | Not |
|---|---|---|
| Cron (5 alan) | `0 9 * * *`, `*/30 8-18 * * 1-5`, `0 9 * * mon`, `@daily` | dakika saat gün ay haftagünü; `*`, liste, aralık, adım, `jan..dec`, `sun..sat`; 0 ve 7 = pazar; gün ve haftagünü ikisi de kısıtlıysa biri yeter |
| Göreli | `10 dakika sonra`, `2 saat sonra`, `3 gün sonra`, `in 10 minutes`, `+30m` | sn/dakika/saat/gün/hafta |
| ISO | `2026-12-01T09:00`, `2026-12-01T06:00:00Z` | saat dilimsizse `TZ` kabul edilir; geçmiş zaman reddedilir |

"Yarın 9'da" gibi ifadeleri AI, sistem istemindeki güncel zamana bakarak ISO'ya çevirir.

## Türler

| `kind` | Zamanı gelince |
|---|---|
| `message` | Metin aynen gönderilir (başına emoji eklenmez; istersen metnin içine yaz) |
| `prompt` | Metin AI'ya istem olarak verilir; AI araçlarla çalışır, cevap gönderilir. Sohbet geçmişine yazılmaz. Onay isteyen araçlar yine düğmeyle sorulur. |

## Davranış

- Kalıcıdır: yeniden başlatmada görevler korunur.
- Kaçırılan tek seferlik görev açılışta bir kez **gecikmeli** olarak çalışır; kaçırılan cron görevi bir kez çalışır ve "şimdiden" sonraki zamana kurulur (kaçanlar tek tek tekrar edilmez).
- Görev çalıştırılmadan önce bir sonraki zamana kurulur; çökme aynı görevi iki kez çalıştırmaz.
- Sınırlar: sohbet başına `SCHEDULE_MAX_PER_CHAT` (50) etkin görev (çalışmayı bekleyen tek seferlikler dahil), tekrar aralığı en az `SCHEDULE_MIN_INTERVAL_MINUTES` (5) dakika, tek seferlik görev en az 60 sn sonra, en fazla ~5 yıl ileri, metin ≤2000 karakter.
- Yaz saati (DST) güvenli: sonraki zaman gerçek an (UTC zaman damgası) olarak hesaplanır. İleri geçişte olmayan saat atlanır; geri geçişte tekrarlanan saatte saatlik görev her gerçek saatte bir kez çalışır, aynı görev tekrar tekrar tetiklenmez. Testler: Europe/Berlin ve America/New_York.
- Zamanlanmış `prompt` görevleri (ve köprüden `ai.ask`) **yeni görev kuramaz**: `schedule_add` bu turlarda yoktur ve reddedilir; böylece görev kendini çoğaltamaz.
- Bir turda web içeriği okunduysa aynı turdaki `schedule_add` kullanıcı onayı ister (Telegram'da düğme, diğer kanallarda kod). Döngü her `SCHEDULE_TICK` (20) sn'de ve yeni görev eklenince uyanır.
- Saat dilimi `TZ` (varsayılan komuta.yaml'da `Europe/Istanbul`).

## Yönetim

| Komut | Ne yapar |
|---|---|
| `/gorevler` | Aktif görevleri listeler |
| `/gorevler iptal 3` | 3 numaralı görevi iptal eder |

Sohbette: "Sabah özetini iptal et" (AI `schedule_list` + `schedule_cancel` kullanır). Olay: her çalışmada `schedule.fired`.
