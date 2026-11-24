[English](../TOOLS.md) · Türkçe

# Yerleşik araçlar

> EN: Built-in tools. Each is a stack_kit `@tool`: visible to the AI (dots become underscores), callable at
> `POST /bridge/call/<name>` and over MCP. `chat_id` is hidden from the AI and filled with the current chat.

Her araç kit'in `@tool` kaydıyla gelir; üç yerden çağrılır:

| Nereden | Ad | Örnek |
|---|---|---|
| AI (Telegram sohbeti) | `web_search` | "Bugün dolar kaç?" |
| HTTP köprü | `web.search` | `POST /bridge/call/web.search` gövde `{"args": {...}}` |
| MCP | `web_search` | Claude Desktop vb. `tools/call` |

`chat_id` parametresi AI'dan gizlenir ve o anki sohbetle doldurulur. Köprü/MCP çağıranı verebilir; vermezse `ALLOWED_CHAT_IDS`'in ilki kullanılır, listede olmayan kimlik reddedilir (`forbidden`). `ALLOWED_CHAT_IDS` boşsa `chat_id` alan tüm araçlar (`memory.*`, `schedule.*`, `ai.ask`) kapalıdır.

## web.search

| Argüman | Tür | Varsayılan |
|---|---|---|
| `query` | metin (1-400) | — |
| `max_results` | 1-10 | 5 |

Dönüş: `{"provider", "results": [{"title", "url", "snippet"}]}`. Sağlayıcı `SEARCH_PROVIDER` ile seçilir:

| Sağlayıcı | Ayar | Not |
|---|---|---|
| `auto` (varsayılan) | — | **Otomatik mod:** ücretsiz kanalları sırayla dener, hata vereni atlar ve 10-15 dk hatırlar. Sıra: kendi SearXNG'niz (varsa), anahtarı girilmiş Tavily/Brave/Serper, anahtarsız Parallel, DuckDuckGo, sonra searx.space'ten seçilen herkese açık SearXNG sunucuları (A/B notlu, JSON açık olanlar; en fazla 5 deneme). Herkese açık sunucular katı ağ politikasıyla (yalnız genel adresler) çağrılır. Sorgularınız üçüncü taraf sunuculara gidebilir. |
| `duckduckgo` | — | Anahtarsız, HTML kazıma; **en iyi çaba**. Bazı ağlardan otomatik istekleri bot korumasıyla reddeder (geliştirme makinesinde gözlendi); o zaman `search_unavailable` hatası döner. |
| `tavily` | `TAVILY_API_KEY` | Önerilen; ücretsiz kotası var. |
| `parallel` | — | Parallel'in barındırılan arama servisi (MCP, Streamable HTTP): **hesap ve anahtar gerekmez**, gerçek web sonuçları ve alıntılar döner. Anonim kullanımın hız sınırı düşüktür (sınır yayımlanmaz). Sorgular Parallel'e gider. |
| `brave` | `BRAVE_API_KEY` | Brave Search API. Kayıt kart isteyebilir; koşullar değişir. |
| `serper` | `SERPER_API_KEY` | Google sonuçlarını JSON döndüren servis (serper.dev); kayıtta tek seferlik ücretsiz sorgu hakkı verilir, kart gerekmez. |
| `searxng` | `SEARXNG_URL` | Kendi SearXNG sunucun (`format=json` açık olmalı). Adres operatör ayarıdır, SSRF süzgecinden geçmez. |

Yeni sağlayıcı: `.claude/skills/add-search-provider`.

## web.read

| Argüman | Tür | Varsayılan |
|---|---|---|
| `url` | http(s) URL | — |
| `max_chars` | 200-50000 | `WEB_READ_MAX_CHARS` (8000) |

Dönüş: `{"url", "title", "text", "truncated"}`. HTML okunabilir metne çevrilir (script/style/nav atılır); düz metin, JSON, XML olduğu gibi döner.

**SSRF koruması** (gevşetilemez; ayrıntı [SECURITY.md](SECURITY.md)): yalnız http/https, URL'de kimlik bilgisi yok, izinli portlar (`WEB_READ_PORTS`, varsayılan 80/443/8080/8443), DNS çözümlemesinden sonra **her** adres herkese açık olmalı (loopback, özel ağ, link-local/metadata, CGNAT, ULA, IPv4-eşlemeli IPv6 engelli), bağlantı denetlenen IP'ye sabitlenir, her yönlendirme yeniden denetlenir (en çok `WEB_READ_MAX_REDIRECTS`=5), boyut `WEB_READ_MAX_BYTES` (2 MB) ve süre `WEB_READ_TIMEOUT` (15 sn) sınırlı.

## schedule.add · schedule.list · schedule.cancel

Ayrıntı: [SCHEDULING.md](SCHEDULING.md).

| Araç | Argümanlar |
|---|---|
| `schedule.add` | `when` (cron / göreli / ISO), `text`, `kind` (`message`/`prompt`), `description` |
| `schedule.list` | — |
| `schedule.cancel` | `task_id` |

## memory.remember · memory.recall · memory.forget · memory.list

Ayrıntı: [MEMORY.md](MEMORY.md).

| Araç | Argümanlar |
|---|---|
| `memory.remember` | `content` (tek cümle, ≤500), `tags` |
| `memory.recall` | `query` (boş = en yeniler), `limit` |
| `memory.forget` | `memory_id` |
| `memory.list` | `limit`, `offset` |

## ai.ask (yalnız köprü/MCP)

AI'ya dışarıdan soru sorar; AI araçlarıyla çalışır ve cevabı döner. `notify: true` ise cevap Telegram'a da gider (yalnız `ALLOWED_CHAT_IDS` sohbetlerine). Döngü olmasın diye AI'nın kendisine **gösterilmez**; bu turda `schedule.add` da kullanılamaz. Onay gerektiren araçlar yine kullanıcı onayıyla sorulur (Telegram'da düğme, diğer kanallarda kod); onay kanalı yoksa çalışmaz.

```bash
curl -s -H "Authorization: Bearer $BRIDGE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"args":{"prompt":"zar at"}}' http://127.0.0.1:8091/bridge/call/ai.ask
```

## Plugin araçları (örnek)

`haber.basliklar` (RSS başlıkları), `ornek.zar`, `ornek.bagis` (onaylı örnek). Kendi aracın: [DEVELOPER.md](DEVELOPER.md).

## Olaylar

`GET /bridge/events` ve `WEBHOOKS_OUT` hedeflerine: `ai.reply` (`chat_id`, `prompt`, `answer`, `scheduled`), `schedule.fired`, `memory.remembered`, `memory.forgotten`.
