# Yönetim arayüzü (`/ui`)

> EN: Admin panel at `/ui`, protected by a password by default (first start: a one-time setup code in the service
> log, then `/ui/setup`). The default manifest also keeps Komuta sign-in on, so the panel has two gates. Wizard:
> panel password, channel choice (setup continues with the chosen channel: Telegram token and chat ids, or the WhatsApp/Teams/Slack/e-mail form), LLM provider + automatic model list, search. Values
> from env vars show up filled (panel value > env > default). `/bridge`, `/mcp`, `/hook` start closed and are
> opened from the panel, with tokens shown once. Secrets are write-only; security locks cannot be switched off.

İçindekiler

1. [Ekranlar](#1-ekranlar)
2. [İlk açılış: iki kapı](#2-ilk-açılış-iki-kapı)
3. [Kurulum sihirbazı](#3-kurulum-sihirbazı)
4. [Ortam değişkenleri ve sihirbaz](#4-ortam-değişkenleri-ve-sihirbaz)
5. [Köprü, MCP ve webhook](#5-köprü-mcp-ve-webhook)
6. [Panel koruması](#6-panel-koruması)
7. [Komuta girişi ve makineler](#7-komuta-girişi-ve-makineler)
8. [Kendi sunucunda](#8-kendi-sunucunda)
9. [Canlı uygulama](#9-canlı-uygulama)
10. [Güvenlik modeli](#10-güvenlik-modeli)
11. [Ortam değişkenleri](#11-ortam-değişkenleri)
12. [Geliştirici notları](#12-geliştirici-notları)
13. [Dil, yardımcılar ve güncelleme ekranı](#13-dil-yardımcılar-ve-güncelleme-ekranı)

## 1. Ekranlar

| Ekran | Adres | Ne yapar |
|---|---|---|
| Genel bakış | `/ui/#/` | Telegram, model, arama, anı/görev sayıları, köprü yüzeyleri (açık/kapalı), panel koruması, son hatalar, kurulum ilerlemesi. |
| Sohbet | `/ui/#/sohbet` | Panelden bota yazın: model seçin (listeden veya elle), araçlar çalışır, onay gerektirenler tarayıcıda sorulur. Konuşmalar sunucuda (`DATA_DIR` içindeki SQLite) saklanır: yalnız düz kullanıcı/AI metni, sırlar maskeli, yönetici başına; **Geçmiş** listesinden açılıp devam edilir, tek tek ya da toplu silinir (en çok 200 sohbet, sohbet başına 400 mesaj). Uçlar: `GET /ui/api/chat/conversations`, `GET/DELETE /ui/api/chat/conversations/{id}`, `DELETE /ui/api/chat/conversations`; kanal (Telegram, WhatsApp, ...) geçmişlerine yazılmaz. Başarısız ya da iptal edilen turlar kaydedilmez. Panel koruması kapalıysa (herkes aynı "kullanıcı" olur) hiçbir sohbet saklanmaz ve listelenmez. |
| Kurulum | `/ui/#/kurulum` | Kurulum durumu: her adımın durumu (Tamam / Doğrulanmadı / Eksik), eksikler kırmızı, her satırda *Düzenle / Doğrula / Tamamla*. Adıma tıklayınca yalnız o adım açılır. |
| Hoş geldin sihirbazı | `/ui/#/hos-geldin` | Tam ekran, adım adım ilk kurulum (aşağıda). Yalnız ilk girişte kendiliğinden açılır; *Sonra yap* (sunucuda saklanır) veya kurulumu bitirmek sonrasında bir daha açılmaz. Kurulum sayfasındaki *Sihirbazı yeniden başlat* ile elle açılır. |
| Ayarlar | `/ui/#/ayarlar` | Sol menüde bölümler: Model, Yanıt ve araçlar, Arama, Hafıza ve görevler, Telegram, **Güvenlik** (panel koruması, kilitler; `#/ayarlar/guvenlik`), Gizli değerler (yalnız yazılır), Değişiklik geçmişi. Kaynak rozeti: arayüz / ortam / varsayılan. |
| Telegram | `/ui/#/telegram` | Bot token'ı (yaz, `getMe` testi), bağlantı durumu ve yeniden bağlanma, izinli sohbetler + canlı sohbet kimliği yakalama, komut menüsü senkronu (`setMyCommands`), son Telegram hataları. |
| Kanallar | `/ui/#/kanallar` | Telegram, WhatsApp, Microsoft Teams, Slack ve e-posta (Telegram kartı kendi `#/telegram` sayfasına açılır, aç/kapat anahtarı kartta): kanal başına aç/kapat, kurulum rehberi, ayar formu (sırlar yalnız yazılır), izin listesi, Test et, bir kez gösterilen doğrulama kodu. Ayrıntı: [KANALLAR.md](KANALLAR.md). |
| Entegrasyonlar | `/ui/#/kopru` | `/bridge`, `/mcp`, `/hook` aç/kapat, token ve webhook sırrı üret/yenile/iptal (bir kez gösterilir, kopyala düğmesi), kopyala-yapıştır örnekler, denetim kaydı. |
| Hafıza, Görevler | `/ui/#/hafiza`, `/ui/#/gorevler` | Sohbet bazlı anılar (ara, sil), zamanlanmış görevler (iptal). |
| Araçlar, Günlük | `/ui/#/araclar`, `/ui/#/gunluk` | Yerleşik/plugin/köprü araçları (onay gerekenler işaretli), redakteli log ve olaylar. |

## 2. İlk açılış: iki kapı

Varsayılan `komuta.yaml` iki kapı kurar:

1. **Komuta girişi** (yol kuralı `access.paths: [{path: /ui, signIn: true}]`, `forwardIdentity: true`): yalnız `/ui` önce Komuta hesabıyla girilir; sitenin geri kalanı (`/health`, `/bridge`, `/mcp`, `/hook`, `/channels`) girişsiz açık kalır ve kendi korumasını taşır.
2. **Panel parolası** (`mount_ui(default_auth="password")`): ilk açılışta parola yoktur. Servis başlarken günlüğe bir **kurulum kodu** yazar:
   ```
   admin UI first-time setup required. Kurulum kodu / setup code (one-time, changes on restart): XXXX-XXXX-XXXX
   ```
   Komuta konsolu → servis → **Loglar** sekmesinden kodu alın, `https://<servis-adresi>/ui/` açın: `/ui/setup` kodu ve yeni parolayı (en az 10 karakter) ister. Kod tek kullanımlıktır, yeniden başlatınca değişir. `UI_PASSWORD` verilirse bu adım atlanır; `UI_SETUP_CODE` ile kendi kodunuzu verebilirsiniz.

Neden iki kapı: uygulama Komuta kimliğine (JWT) varsayılan olarak yalnız bir ek kapı olarak bakar; panel kendi parolasıyla da korunur. Manifestte `forwardIdentity: true` olduğu için `UI_AUTH=auto` ile Komuta kimliği varken parola sorulmaz, yoksa parola istenir (`UI_AUTH=komuta`: yalnız Komuta kimliği). Yol kuralı ve kimlik iletme manifestte yazılabilir; dış çağıranlar (n8n, MCP, webhook) `/ui` dışında kaldıkları için kapıda takılmaz.

Parolayı unuttunuz: giriş sayfasında **Parolamı unuttum**'a basın (`/ui/reset`). Servis günlüğüne (Komuta konsolu → Loglar, `sıfırlama` diye aratın) tek kullanımlık, 15 dakika geçerli bir kod yazılır; kodu ve yeni parolayı girin, kaydedince tüm eski oturumlar kapanır. Parola `UI_PASSWORD` ortam değişkeninden geliyorsa orada değiştirin.

## 3. Kurulum sihirbazı

1. **Panel parolası.** Kurulduysa "Tamam" görünür (`UI_PASSWORD` ortamdan, panelde belirlenmiş veya Komuta kimliği). Koruma kapalıysa burada uyarı ve "Korumayı aç" düğmesi çıkar.
2. **Kanal.** Hangi kanaldan konuşacağınızı seçersiniz: Telegram, WhatsApp, Microsoft Teams, Slack, e-posta ya da *Şimdilik yok* (yalnız panel sohbeti). Seçim sunucuda saklanır; sihirbaz o kanalın kurulumuyla devam eder, diğer kanalları sonra **Kanallar** sayfasından eklersiniz. Daha önce Telegram veya başka bir kanalı ayarlamış kurulumlarda seçim buna göre kendiliğinden belirlenir.
3. **Seçilen kanalın kurulumu.**
   - **Telegram:** iki adım. *Telegram botu* (BotFather token'ı; *Test et* `getMe` ile doğrular, *Kaydet ve bağlan* saklar) ve *Sohbet kimlikleri* (bota mesaj atın, sohbet birkaç saniyede listede belirir, *İzin ver*; elle ekleme de var; liste boşken sohbet araçları kapalıdır).
   - **WhatsApp, Teams, Slack, e-posta:** tek adım, *<Kanal> kurulumu*. Kanallar sayfasındaki aynı form gömülüdür: kurulum rehberi, bağlantı adresi, ayarlar ve gizli değerler (yalnız yazılır), izin listesi, kanala özel sistem istemi, açma anahtarı ve *Test et*. Kanal **açık, ayarları tam ve izin listesinde en az bir kişi** olunca adım tamam olur; *Şimdilik atla* yalnız panel sohbetine döner.
4. **Yapay zekâ modeli.** Sağlayıcı kartları: OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity, Ollama, LM Studio, vLLM, özel. Anahtarı girip **Bağlan**: model listesi gelir (aranabilir, son kullanılan üstte, araç rozeti). Sihirbazın önerdiği model `deepseek/deepseek-v4.1-flash-1017` (yerleşik varsayılan `openai/gpt-4o-mini` çalışan kurulumlar değişmesin diye aynı kaldı). Ayrıntı: [LLM-SAGLAYICILAR.md](LLM-SAGLAYICILAR.md).
5. **Web arama.** Otomatik mod (önerilen), Parallel, DuckDuckGo, Tavily, Brave, Serper, SearXNG. Anahtarı eksik sağlayıcı kaydedilmez.
6. **Özet ve uygula.** *Uygula ve bitir*; yeniden başlatma gerekmez.

Adım listesi seçime göre değişir (ör. Telegram seçilince iki, WhatsApp seçilince bir kanal adımı olur); ilerleme çubuğu ve *Geri*/*Devam* buna uyar. Sunucu tarafı: `POST /ui/api/setup/channel {"channel": "telegram|whatsapp|teams|slack|email|none"}`; `GET /ui/api/setup` yanıtında `channel` ve adım kimlikleri (`channel`, `channel_setup`, `telegram`, `chats`, ...) bulunur.

Akış tek kart ve tek eylemdir: adım başarılı olunca bir sonrakine geçilir, *Devam* adım doğrulanmadan açılmaz; arama adımı atlanabilir.

Adım durumları: **Tamam** (doğrulandı: gerçek test veya onay), **Doğrulanmadı** (değer ortam değişkeninden/varsayılandan geldi; *Doğrula* düğmesi sunulur, tamam sayılmaz), **Bekliyor**. Ortamdan gelen değer asla "tamam" sayılmaz; sohbet listesi ve arama için *onayla*, Telegram ve model için gerçek istekle doğrulama gerekir. `0` sohbet kimliği yer tutucudur, liste boş sayılır.

## 4. Ortam değişkenleri ve sihirbaz

Her ayar hem ortam değişkeni/Komuta Secret olarak hem panelden girilebilir. Öncelik:

```
panelde kaydedilen değer  >  ortam değişkeni  >  varsayılan
```

- Sihirbaz ortamdan gelen değerleri dolu gösterir: "Ortam değişkeninden: `TELEGRAM_BOT_TOKEN` …abcd" (sırlar yalnız son 4 karakter).
- Panelde yeni bir değer yazarsanız ortam değerini ezer; alan "Panelde kayıtlı · ortam değerini eziyor" der ve **Ortam değişkenine dön** düğmesi panel değerini siler.
- Ortamdan gelen değerler dolu görünür ama doğrulanana kadar adım açık kalır (yanlış anahtar "hazır" görünmez). Örnek manifest: [`examples/komuta-env-ile.yaml`](../examples/komuta-env-ile.yaml).

## 5. Köprü, MCP ve webhook

`/bridge/*`, `/mcp`, `/hook/*` **varsayılan kapalıdır**: kapalı yüzey kimlik denetiminden önce **404** döner. Açmak:

- Panel → **Entegrasyonlar**: yüzey başına anahtar; köprü/MCP açılırken token yoksa üretilip **bir kez** gösterilir (kopyala düğmesi). Token ve webhook sırrı yenilenebilir/iptal edilebilir; her değişiklik denetim kaydına girer. Sunucuda token'ın yalnız özeti saklanır.
- Ortam: `BRIDGE_ENABLED=true`, `MCP_ENABLED=true`, `HOOKS_ENABLED=true` (başlangıç değeri; örnek [`examples/komuta-n8n-ile.yaml`](../examples/komuta-n8n-ile.yaml)). `BRIDGE_TOKEN` / `HOOK_SECRET` env'leri hâlâ geçerlidir ama manifestte yoktur.
- Komut satırı: `python -m stack_kit gate status | enable bridge | rotate`.

Sayfadaki hazır örnekler: curl, n8n HTTP Request, Claude Code (`claude mcp add --transport http ...`), Claude Desktop (`mcp-remote` ile), TradingView webhook (`/hook/notify`, gövdede `"secret"` alanı). `/hook/notify` gelen metni izinli ilk sohbete Telegram mesajı olarak iletir (AI çalıştırmaz).

Sayfanın üstündeki **Hızlı bağlantı** kartı (stack_ui `KUI.quickConnect`, `/ui/api/bridge/quickstart`): istemci seçin (Claude Code, Claude Desktop, genel/curl, n8n), prompt'u kopyalayıp ajana yapıştırın; ajan MCP bağlantısını kurar. MCP kapalıyken kart pasiftir ("önce açın"); yeni token üretildiği anda bir kez "token dahil kopyala" seçeneği çıkar (token sunucuda kısa süre oturuma bağlı tutulur, sayfada saklanmaz).

## 6. Panel koruması

Ayarlar → **Panel koruması**:

- **Parolayı değiştir** (mevcut + yeni). Değişince diğer tüm oturumlar düşer. `UI_PASSWORD` ortamdan geliyorsa yalnız orada değişir.
- **Tüm oturumları kapat**: siz dahil herkes yeniden giriş yapar.
- **Korumayı kapat (önerilmez)**: mevcut parola + `KORUMAYI KAPAT` yazmak + risk uyarılarını okuyup kabul kutusunu işaretlemek gerekir. Kapalıyken her sayfada kalıcı kırmızı bant görünür, tek tıkla yeniden açılır; her açılışta günlüğe uyarı yazılır; kimlik/parola ayarları anonimce değiştirilemez, yazma isteklerinde CSRF sürer. `UI_FORCE_PROTECTION=1` kapatmayı tamamen yasaklar.

## 7. Komuta girişi ve makineler

Varsayılan manifest yalnız `/ui` yolunu Komuta girişine bağlar (`access.paths`). Köprü, MCP, webhook ve kanal adresleri girişsiz açıktır ve kendi korumalarını taşır: Bearer token, HMAC imzası, JWT. Bu yüzden n8n, MCP istemcisi, TradingView ve WhatsApp/Teams/Slack webhook'ları kapıda kalmaz. Komuta girişini tamamen kapatmak istersen `examples/komuta-giris-kapali.yaml`; bütün siteyi Komuta girişine almak için `access.signIn: true` yazılır (bu durumda makine çağıranları için ilgili yolları girişsiz açman gerekir).

| Manifest | Komuta girişi | Panel | Makineler |
|---|---|---|---|
| `komuta.yaml` (varsayılan) | yalnız `/ui` | parola | doğrudan, kendi token/imzasıyla |
| `examples/komuta-giris-kapali.yaml` | yok (konsolda "korumasız") | parola | doğrudan, token'la |
| `examples/komuta-env-ile.yaml` | açık | `UI_PASSWORD` Secret | — |
| `examples/komuta-n8n-ile.yaml` | yok | parola | köprü manifestten açık |

## 8. Kendi sunucunda

```bash
scripts/setup.sh            # .env: UI_PASSWORD ve UI_SESSION_SECRET üretir
docker compose up --build   # http://localhost:8080/ui/
```

`UI_PASSWORD` vermezseniz günlükteki kurulum koduyla `/ui/setup`. Yerelde sahte LLM ile:

```bash
.venv/bin/python scripts/fake_llm.py 9999 &
LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:9999/v1 PYTHONPATH=src:. .venv/bin/python -m ai_bot
# günlükteki "Kurulum kodu" ile http://127.0.0.1:8080/ui/setup
```

## 9. Canlı uygulama

Ayarlar kaydedildiği anda çalışan bota uygulanır: model/sağlayıcı/anahtar değişince yeni LLM istemcisi kurulur (Anthropic seçilirse yerel adaptör), arama, izin listesi, saat dilimi, sınırlar ve onay süresi hemen geçerli olur, Telegram token'ı değişince bağlantı yeniden kurulur. Tek istisna: bot başka bir modüle aitse (birleşik mt5-trader) token için servis yeniden başlatılmalı; arayüz bunu söyler.

### Ağ politikası (panelden girilen adresler)

LLM Base URL, model listesi, LLM testi ve `SEARXNG_URL` yöneticinin yazdığı adreslere istek atar; SSRF'ye karşı her istek `src/ai_bot/netpolicy.py`'den geçer:

| Sağlayıcı | Adres | İzinli hedef |
|---|---|---|
| Bulut ön ayarları (OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity) | **ön ayarın host'una kilitli**; başka adres 422 | yalnız o host |
| Yerel (Ollama, LM Studio, vLLM), SearXNG | serbest | genel + loopback + özel ağ |
| Özel (OpenAI uyumlu) | serbest | yalnız genel IP; "yerel/özel ağ" kutusu (`LLM_ALLOW_PRIVATE_NETWORK`) işaretlenirse özel ağ da |

Her durumda engelli: link-local (169.254.0.0/16, fe80::/10), bulut metadata (169.254.169.254, fd00:ec2::254, `metadata.google.internal` ...), `0.0.0.0`/`::`, çoklu yayın/ayrılmış adresler, bu servisin kendi portu (`HTTP_PORT`, yerel adreste) ve `/ui`, `/bridge`, `/mcp`, `/hook` yolları. Ad çözümlendikten sonra **her** adres denetlenir ve bağlantı denetlenen IP'ye sabitlenir (`Host` ve TLS SNI adı taşır; DNS rebinding işe yaramaz); IPv4-mapped IPv6 açılır. Yönlendirmeler izlenmez. Kayıtlı LLM anahtarı yalnız kayıtlı host'a gider. Yanıtlar ham gövde yansıtmaz: model listesinden yalnız biçimi geçerli model adları (en çok 200 karakter, kontrol karakteri yok) döner; hata mesajları durum koduna göre kısa Türkçe metindir, sağlayıcının gövdesi gösterilmez.

## 10. Güvenlik modeli

- **Link herkese açık varsayılır.** Panel parolalı (scrypt, `ui-auth.json` izin 600), ilk kurulum tek kullanımlık kodla; varsayılan manifestte önünde Komuta girişi de var.
- **Makine yolları kapalı başlar** (404); açıldığında token (yalnız özeti saklanır) veya HMAC ister.
- **Sırlar geri gösterilmez**: yanıtlar yalnız "kayıtlı" + son 4 karakter; test uçları verilen değeri geri yazmaz; kurulum kodu yalnız başlangıç günlüğüne bir kez yazılır.
- **Kayıtlı LLM anahtarı yalnız kayıtlı host'a gider**: arayüzde başka adres yazılırsa kayıtlı anahtar gönderilmez.
- **Kilitler kapatılamaz**: insan onayı, SSRF koruması, boş izin listesinde kapalı sohbet araçları, istem enjeksiyonu kilidi, `BRIDGE_TOKEN`/`UI_*` ayar şemasında yoktur; alt sınırlar korunur.
- **CSRF + Origin** her yazma isteğinde; sıkı CSP; denetim kayıtları (ayarlar, parola/koruma, köprü kapısı).

Ayrıntı: [GUVENLIK.md](GUVENLIK.md).

## 11. Ortam değişkenleri

| Değişken | Varsayılan | Anlam |
|---|---|---|
| `UI_AUTH` | (yok → parola) | `password`, `auto` (Komuta kimliği varsa o, yoksa parola), `komuta`, `off` (yalnız `UI_ALLOW_INSECURE=1`) |
| `UI_PASSWORD` | — | Panel parolası ortamdan (kurulum kodu adımı atlanır; yalnız buradan değişir) |
| `UI_SETUP_CODE` | rastgele | İlk kurulum kodu (verilmezse günlüğe yazılır) |
| `UI_SESSION_SECRET` | rastgele | Oturumlar yeniden başlatmada düşmesin |
| `UI_FORCE_PROTECTION` | `false` | Korumanın kapatılmasını yasaklar |
| `UI_ALLOWED_HOSTS`, `UI_ALLOWED_EMAILS` | — | Yalnız Komuta kimliği modlarında |
| `BRIDGE_ENABLED`, `MCP_ENABLED`, `HOOKS_ENABLED` | kapalı | Yüzeylerin başlangıç değeri |
| `BRIDGE_TOKEN`, `HOOK_SECRET` | — | Panel token'ına ek olarak geçerli |
| `LLM_PROVIDER` | `auto` | Sağlayıcı |
| `TELEGRAM_API_BASE` | Telegram | Yalnız ortamdan |

## 12. Geliştirici notları

- Sayfalar: `src/ai_bot/ui/` (tek sayfa, hash yönlendirme: `index.html`, `app.js`, `widgets.js`, `pages/*.js`). Giriş, ilk kurulum ve erişim-yok sayfaları kitindir (`mount_ui(login_card_width=460, setup_card_width=460, setup_hint=...)`).
- API: `src/ai_bot/panel.py`; sağlayıcılar `providers.py`; ağ politikası `netpolicy.py`; Anthropic adaptörü `llm.py`; canlı ayar `runtime_config.py`. Kimlik, ilk kurulum, koruma ve köprü kapısı uçları vendor `stack_ui`'den (`/ui/api/auth*`, `/ui/api/bridge`).
- Testler: `tests/test_ui.py`, `tests/test_ui_v2.py`.


## 13. Dil, yardımcılar ve güncelleme ekranı

- **Dil (TR / EN):** İlk açılışta sihirbazın ilk ekranı dil seçtirir; seçim sunucuda saklanır, yeni bir tarayıcı aynı dille açılır. Üst çubuktaki TR / EN düğmesiyle istediğiniz zaman değişir. Çeviri sözlüğü `src/ai_bot/ui/i18n-en.js` (anahtar Türkçe metin); eksikleri `python scripts/i18n_extract.py --missing` listeler.
- **Mini yardımcılar:** Telegram token'ı, sohbet kimliği, her LLM sağlayıcısının anahtarı, arama sağlayıcıları ve panel parolası için "Nasıl yapılır?" düğmesi adım adım rehber penceresi açar (kopyalanabilir değerler, bağlantılar, notlar). Kanallar sayfasında her kanalın kendi kurulum rehberi vardır.
- **Güncelleniyor ekranı:** Sunucu yeni sürüme geçerken istekler birkaç saniye başarısız olabilir. Panel bunu algılar, "Güncelleniyor" ekranını gösterir, `/health` yanıt verince sayfayı kendiliğinden yeniler. Oturumlar düşmez: oturum anahtarı `DATA_DIR/ui-session.key` dosyasında saklanır.
- **Veri tabanı:** Eski ve yeni örnek sürüm geçişinde birkaç saniye aynı SQLite dosyasını açabilir; bağlantı 30 sn beklemeyi (busy timeout) ve kilitli kalan açılışta yeniden denemeyi bilir, kapanırken WAL'ı temizler.
- **Arama:** Varsayılan `auto`: kendi SearXNG'niz, anahtarlı sağlayıcılar (Tavily, Brave, Serper), anahtarsız Parallel, DuckDuckGo, herkese açık SearXNG sırasıyla denenir; hata veren atlanır ve 10-15 dk hatırlanır.
