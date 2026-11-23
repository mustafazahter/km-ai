# Mesajlaşma kanalları (WhatsApp, Microsoft Teams, Slack, e-posta)

Botla Telegram'ın yanında WhatsApp, Microsoft Teams, Slack ve e-postadan da konuşabilirsiniz. Aynı yapay zekâ, aynı araçlar, aynı hafıza ve görev zamanlayıcı çalışır. **Her kanal varsayılan olarak kapalıdır**; kapalıyken gelen adresleri `404` döner.

Panelde `Kanallar` sayfası (`/ui/#/kanallar`) her kanal için kurulum rehberini, ayar formunu, izin listesini ve test düğmesini içerir. Bu belge aynı bilgileri komut satırı/ortam değişkeni bakışıyla verir.

## Özet

| Kanal | Gelen yol | Nasıl gelir | Onay gerektiren araçlar |
|---|---|---|---|
| WhatsApp | `POST/GET /channels/whatsapp/webhook` | Meta webhook'u (imzalı) | Evet, kodla (`EVET 123456`) |
| Microsoft Teams | `POST /channels/teams/messages` | Azure Bot Service (JWT) | Evet, kodla |
| Slack | `POST /channels/slack/events` | Events API (imzalı) | Evet, kodla (DM'den) |
| E-posta | yok (bot yoklar) | IMAP, her `EMAIL_POLL_SECONDS` sn | **Hayır** (reddedilir) |

Bir mesajın yolu: sağlayıcı imzası doğrulanır → izin listesi, tekrar (dedupe) ve hız sınırı denetlenir → `ai.run_ai(chat_id, metin)` (aynı sohbet kilidi, aynı araç döngüsü) → yanıt sağlayıcının sınırına göre parçalanıp aynı yoldan gönderilir. Webhook'lar hemen `200` döner, iş arka planda yürür.

## Güvenlik modeli

- **Kapalı varsayılan.** `*_ENABLED` değeri `false` iken ilgili gelen adresler `404` verir; varlıkları bile görünmez.
- **Kendi imzaları.** Bu adresler Komuta girişiyle değil sağlayıcının imzasıyla korunur: WhatsApp `X-Hub-Signature-256` (HMAC-SHA256, ham gövde), Slack `X-Slack-Signature` (HMAC-SHA256 + 5 dk tazelik), Teams Bot Framework JWT (RS256). İmza, gövde ayrıştırılmadan önce ve sabit zamanlı karşılaştırmayla doğrulanır; eksik/yanlış imza `403` (Teams: `401`).
- **İzin listesi.** Her kanalın kendi listesi vardır. **Boş liste = kimse yazamaz**; "herkese izin" diye bir değer yoktur. Listede olmayan gönderenin mesajı sessizce düşer (panelde "Reddedilen son gönderenler" altında kimliği görünür, oradan tek tıkla izin verebilirsiniz).
- **Sohbet kimliği.** Kanal gönderenleri için `sha256(kanal + ":" + dış_kimlik)` özetinden kararlı, **negatif** bir tam sayı üretilir: `-(2**52 + (özetin ilk 8 baytı mod 2**52 - 1))`. Büyüklük 2**52 ile 2**53-2 arasındadır: JavaScript'te tam temsil edilir (panelin Hafıza/Görevler sayfaları JSON sayısını okur) ve Telegram kimlikleriyle çakışmaz (kullanıcılar pozitif ve 2**52'nin altında, gruplar yaklaşık -1e12..-1e13). Hafıza, geçmiş ve görevler gönderen başınadır ve Telegram sohbetleriyle karışmaz. `resolve_chat` bu kimlikleri yalnız **o kullanıcının kendi başlattığı turun içinde** (iç bağlam değişkeni) ve gönderen **açık bir kanalın izin listesinde** olduğu sürece kabul eder; köprü (`/bridge`), MCP, `/hook/notify` ve `ai.ask` çağıranları bir kanal kullanıcısının sohbetine hafıza/görev/mesaj yoluyla ulaşamaz. Liste değişince/kanal kapanınca araçlar o kullanıcı için hemen kapanır.
- **Tekrar saldırısı (replay).** İmzalı bir WhatsApp gövdesi ele geçirilirse yeniden gönderilebilir; bu yüzden mesaj `timestamp` değeri 10 dakikadan eskiyse işlenmez (Teams etkinlikleri için aynı kural `timestamp` alanına uygulanır; Slack zaten 5 dakika sınırlıdır). Alan yoksa kontrol atlanır. Tekrar eleme belleği süreç içindedir: yeniden başlatmadan sonraki 10 dakikalık pencerede aynı mesaj bir kez daha işlenebilir.
- **Sınırlar.** En çok 4000 karakter girdi (fazlası reddedilir; e-postada kesilir), gönderen başına dakikada 20 mesaj, sağlayıcı mesaj kimliğine göre tekrar eleme, grup/bot/metin dışı olaylar yok sayılır, yanıtlar sağlayıcı sınırında parçalanır (WhatsApp 4000, Slack 3000, Teams 4000, e-posta tek mesaj).
- **Gizlilik.** Mesaj gövdeleri ve gizli değerler günlüğe yazılmaz; hata olarak yalnız kısa güvenli kodlar (`whatsapp_auth` gibi) saklanır. Panel API'si hiçbir gizli değeri geri vermez (yalnız `…son4`). Sağlayıcı hata gövdeleri panele yansıtılmaz.
- **İnsan onayı.** Onay gerektiren araçlar (`confirm=True` veya `trade`/`admin` kapsamı) için WhatsApp, Teams ve Slack'te bot, önizleme + tek kullanımlık 6 haneli kod gönderir. Onay için **aynı gönderenin** `EVET 123456` yazması gerekir (`CONFIRM_TIMEOUT`, varsayılan 120 sn içinde). Yanlış kod, `HAYIR`, başka herhangi bir metin ya da süre aşımı = **hayır**; deneme hakkı tek. Başka bir kullanıcının yazdığı kod bu onayı vermez. Slack'te onay istemi, mesaj kanaldan gelmiş olsa bile kullanıcının DM'ine gider. Onay önizlemesi tam gösterilemeyecek kadar uzunsa (4000 karakter; Telegram'da 3000) **onay istenmez ve araç çağrısı reddedilir**: kimse kesilmiş bir önizlemeyi onaylamaz.
- **E-posta neden onaysız?** `Kimden` başlığı kolayca taklit edilir; e-postayla verilen bir onayı da taklit eden herkes verebilir. Bu yüzden e-posta turlarında onaycı yoktur: onay gerektiren araçlar **reddedilir**, çalışmaz. Ayrıca e-postadan başlayan turlar "dış içerik" (tainted) sayılır: hafızaya yazma ve görev ekleme de onay ister, yani e-postadan yapılamaz. E-posta turlarında `web.read` modele hiç gösterilmez (komut enjekte edilmiş bir posta, hafıza içeriğini bir adrese gömüp sızdıramasın); `web.search` kalır.
- **SSRF.** WhatsApp, Slack, Microsoft yanıt adresleri sabittir. Teams yanıt adresi (`serviceUrl`) https, port 443, IP olmayan, Bot Framework son ekli (`.botframework.com`, `.trafficmanager.net`, `.azure.com`, `smba.*.microsoft.com`) olmalı ve imzalı JWT içindeki `serviceurl` değeriyle aynı olmalıdır; aksi halde istek reddedilir ve bot belirteci hiçbir yere gönderilmez. E-posta sunucusu adı çözümlenir; metadata, link-local, `0.0.0.0` ve çoklu yayın adresleri engellenir (yerel ağdaki posta sunucusuna izin verilir).

## Komutlar

Her kanalda `/hafiza`, `/gorevler`, `/model`, `/sifirla`, `/help` ve eklediğin komutlar çalışır (Telegram dahil). `/komut` ya da `!komut` yazılır; Slack `/...` mesajını kendi komutu saydığı için orada `!hafiza` kullan. Onay isteyen komutlar kodla onaylanır (`EVET 123456`). **E-postada** gönderen doğrulanamadığından yalnız okuma komutları çalışır; değiştiren komutlar "bu kanalda kapalı" yanıtı alır. Kendi komutunu ekleme: [GELISTIRICI.md](GELISTIRICI.md#komut-ekleme-her-kanalda). Uygulama: `src/ai_bot/commands.py`, testler: `tests/test_channel_commands.py`.

Öncelik her yerde aynıdır: **panelde kaydedilen > ortam değişkeni > varsayılan**. Panelden kaydedilen değerler yeniden başlatma gerektirmez. Sırlar yalnız yazılır; kaydedildikten sonra panel yalnız `…son4` gösterir.

| Değişken | Tür | Varsayılan | Anlam |
|---|---|---|---|
| `WHATSAPP_ENABLED` | bool | `false` | WhatsApp kanalı açık |
| `WHATSAPP_PHONE_NUMBER_ID` | metin | — | Cloud API telefon numarası kimliği |
| `WHATSAPP_ALLOWED_NUMBERS` | liste | boş | İzinli numaralar (ülke koduyla, virgüllü; `+`, boşluk, `-` yok sayılır) |
| `WHATSAPP_TOKEN` | **sır** | — | Erişim belirteci (kalıcı sistem kullanıcısı belirteci önerilir) |
| `WHATSAPP_APP_SECRET` | **sır** | — | Uygulama gizli anahtarı (imza doğrulama) |
| `WHATSAPP_VERIFY_TOKEN` | **sır** | — | Webhook doğrulama kodu (panelden "Üret") |
| `TEAMS_ENABLED` | bool | `false` | Teams kanalı açık |
| `TEAMS_APP_ID` | metin | — | Azure Bot "Microsoft App ID" |
| `TEAMS_TENANT_ID` | metin | boş | Tek kiracılı bot için kiracı kimliği (boşsa `botframework.com`) |
| `TEAMS_ALLOWED_USERS` | liste | boş | Yalnız Entra Object ID (`aadObjectId`, GUID); `from.id` eşleştirilmez |
| `TEAMS_APP_PASSWORD` | **sır** | — | İstemci gizli anahtarı |
| `SLACK_ENABLED` | bool | `false` | Slack kanalı açık |
| `SLACK_TEAM_ID` | metin | boş | Çalışma alanı kimliği (`T…`). Doluysa olayın `team_id`/`authorizations` değeri eşleşmelidir (önerilir) |
| `SLACK_ALLOWED_USERS` | liste | boş | Slack üye kimlikleri (`U…`) |
| `SLACK_BOT_TOKEN` | **sır** | — | Bot User OAuth Token (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | **sır** | — | İmza anahtarı |
| `EMAIL_ENABLED` | bool | `false` | E-posta kanalı açık |
| `EMAIL_IMAP_HOST` / `EMAIL_IMAP_PORT` / `EMAIL_IMAP_USER` | metin / sayı / metin | — / `993` / — | IMAP (yalnız TLS) |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | metin / sayı | — / `587` | SMTP; `465` = SSL, diğerleri STARTTLS (STARTTLS yoksa gönderim yapılmaz) |
| `EMAIL_FROM` | metin | — | Yanıtların gönderen adresi |
| `EMAIL_ALLOWED_SENDERS` | liste | boş | Tam e-posta adresleri (büyük/küçük harf önemsiz) |
| `EMAIL_POLL_SECONDS` | sayı | `60` | Yoklama aralığı (en az 30) |
| `EMAIL_AUTHSERV_ID` | metin | boş | Posta sunucunuzun `Authentication-Results` kimliği (önerilir; aşağıya bakın) |
| `EMAIL_REQUIRE_AUTH_RESULTS` | bool | `true` | Hizalı DKIM/SPF/DMARC geçmeyen postayı düşür |
| `EMAIL_PASSWORD` | **sır** | — | Uygulama parolası (SMTP girişi de bunu kullanır) |

Listeler virgülle ayrılır. Bu ayarlar `runtime_config.SCHEMA`/`SECRET_NAMES` içindedir ("kanallar" grubu); güvenlik kilitleri (onay kuralı, SSRF sınırları, istem enjeksiyonu kilidi) bu ayarlarla gevşetilemez.

## Komuta tarafında yapılacaklar

- `/channels/*` yolları **Komuta girişinden muaf** olmalıdır (yol kuralı); aksi halde Meta/Slack/Microsoft sunucuları giriş duvarına çarpar. Güvenlik bu yollarda sağlayıcı imzasından gelir.
- Servisin genel adresi **https** olmalıdır (üçü de http kabul etmez). Panelde gösterilen adresler `UI_PUBLIC_URL` (varsa) veya tarayıcının `Host` değerinden üretilir; `X-Forwarded-Host` güvenilmez. Genel adres farklıysa `UI_PUBLIC_URL` tanımlayın.
- E-posta için servisin dışarıya IMAP (993) ve SMTP (587/465) bağlantısı kurabilmesi gerekir (çıkış ağı izni).

## 1) WhatsApp (Meta Cloud API)

1. developers.facebook.com'da bir **Business** uygulaması oluşturun, ürün olarak **WhatsApp**'ı ekleyin.
2. **WhatsApp > API Setup**: gönderen numaranın **Phone number ID** değerini `WHATSAPP_PHONE_NUMBER_ID` olarak girin.
3. Kalıcı erişim belirteci: business.facebook.com > Business Settings > Users > **System Users** > sistem kullanıcısı oluşturun, uygulamayı atayın, **Generate token**, izinler `whatsapp_business_messaging` ve `whatsapp_business_management` → `WHATSAPP_TOKEN`. (API Setup'taki geçici belirteç 24 saatte biter.)
4. **App settings > Basic > App secret** → `WHATSAPP_APP_SECRET`.
5. Panelde **Doğrulama kodu > Üret**: `WHATSAPP_VERIFY_TOKEN` oluşur ve **bir kez** gösterilir; kopyalayın. (Ortamdan vermek isterseniz kendiniz rastgele bir değer seçin.)
6. Kanalı açın (`WHATSAPP_ENABLED=true`).
7. **WhatsApp > Configuration > Webhook > Edit**: Callback URL `https://<alan-adınız>/channels/whatsapp/webhook`, Verify token = 5. adımdaki değer → **Verify and save**. Sonra **Webhook fields** içinde `messages` alanına **Subscribe** olun.
8. `WHATSAPP_ALLOWED_NUMBERS` listesine numaranızı ekleyin (`905551112233`). Test numarasıyla yalnız API Setup'ta "To" alanına eklediğiniz alıcılar yazışabilir.
9. Panelde **Test et**: `GET /v21.0/{phone_number_id}` ile belirteci ve numara kimliğini denetler; kimseye mesaj göndermez.

Notlar: serbest metin yalnız kullanıcının son mesajından sonraki **24 saat** içinde gönderilebilir (hatırlatmalar da); şablon mesaj desteği yoktur.

## 2) Microsoft Teams (Azure Bot Service)

1. portal.azure.com > **Create a resource > Azure Bot** (tür: Single Tenant önerilir).
2. **Configuration**: `Microsoft App ID` → `TEAMS_APP_ID`, `App Tenant ID` → `TEAMS_TENANT_ID`.
3. **Manage Password > Certificates & secrets > New client secret**: **Value** → `TEAMS_APP_PASSWORD` (yalnız bir kez görünür).
4. **Messaging endpoint**: `https://<alan-adınız>/channels/teams/messages` → Apply.
5. **Channels > Microsoft Teams** kanalını ekleyin.
6. Teams'te görünmesi için uygulama paketi: dev.teams.microsoft.com > Apps > New app > App features > **Bot** (Bot ID = Microsoft App ID, kapsam **Personal**) > Publish > Preview in Teams.
7. Kullanıcının **Object ID**'sini (GUID; Entra ID > Users) `TEAMS_ALLOWED_USERS` listesine ekleyin (`29:…` kimlikleri kabul edilmez). Bilmiyorsanız bota yazın; kimlik panelde "Reddedilen son gönderenler" altında çıkar.
8. `TEAMS_ENABLED=true`, panelde **Test et** (Microsoft'tan bot belirteci alır), sonra Teams'ten yazın.

Etkinlik denetimi: `channelId` `msteams` olmalı; `TEAMS_TENANT_ID` doluysa `channelData.tenant.id` ona eşit olmalı (başka kiracının botunuza yazması engellenir); yalnız `personal` konuşma türü; kullanıcı yalnız `from.aadObjectId` ile eşleşir; 10 dakikadan eski `timestamp` yok sayılır.

JWT denetimi: `Authorization: Bearer`, RS256, anahtar `https://login.botframework.com/v1/.well-known/openidconfiguration` → `jwks_uri` (önbellekli; bilinmeyen `kid` en çok 30 sn'de bir yenileme tetikler), `iss = https://api.botframework.com`, `aud = TEAMS_APP_ID`, `exp`/`nbf` (60 sn tolerans), `serviceurl` talebi = etkinliğin `serviceUrl` değeri. v1: yalnız **birebir (personal)** sohbet; kanallar ve grup sohbetleri yok sayılır. Botun ilk yazdığı mesajlar (hatırlatma) için kullanıcının daha önce yazmış olması gerekir (konuşma referansı `DATA_DIR/channel-refs.json` içinde saklanır; yalnız kimlikler ve servis adresi, sır yok).

## 3) Slack

1. api.slack.com/apps > **Create New App > From scratch**.
2. **OAuth & Permissions > Bot Token Scopes**: `chat:write`, `im:history`, `app_mentions:read`.
3. **App Home > Messages Tab** açık ve "Allow users to send … messages from the messages tab" işaretli.
4. **Install to Workspace** → **Bot User OAuth Token** → `SLACK_BOT_TOKEN`.
5. **Basic Information > Signing Secret** → `SLACK_SIGNING_SECRET`.
6. `SLACK_ENABLED=true` yapıp kaydedin, sonra **Event Subscriptions > Enable Events**: Request URL `https://<alan-adınız>/channels/slack/events` (imza geçince `Verified` görünür).
7. **Subscribe to bot events**: `message.im` ve `app_mention` → Save Changes (gerekirse uygulamayı yeniden kurun).
8. Profilinizden **Copy member ID** (`U…`) → `SLACK_ALLOWED_USERS`. Çalışma alanı kimliğini (`T…`) `SLACK_TEAM_ID` olarak girmeniz önerilir. Bota DM atın; kanalda `@bot` ile de anabilirsiniz: yanıt (hafıza veya araç çıktısı içerebileceği için) **kanala değil sizin DM'inize** gider, kanala yalnız "yanıtı DM'den gönderdim" notu düşer.

Slack yeniden denemeleri (`X-Slack-Retry-Num`) işlenmeden `200` ile karşılanır; bot/alt tür mesajları yok sayılır.

## 4) E-posta

1. Bot için **ayrı bir posta kutusu** açın. IMAP erişimini açın; iki adımlı doğrulama varsa **uygulama parolası** üretin (Gmail: Google Hesabı > Güvenlik > 2 Adımlı Doğrulama > Uygulama şifreleri; Microsoft 365: hesap güvenliği > Uygulama parolaları ve yönetici tarafında IMAP + Authenticated SMTP; Yandex/Zoho: Güvenlik > Uygulama parolaları).
2. `EMAIL_IMAP_HOST` (ör. `imap.gmail.com`), `EMAIL_IMAP_PORT=993`, `EMAIL_IMAP_USER` (genelde tam adres), `EMAIL_PASSWORD`.
3. `EMAIL_SMTP_HOST` (ör. `smtp.gmail.com`), `EMAIL_SMTP_PORT` (`587` STARTTLS veya `465` SSL), `EMAIL_FROM`.
4. `EMAIL_ALLOWED_SENDERS` = botla yazışacağınız tam adresler.
5. `EMAIL_REQUIRE_AUTH_RESULTS=true` kalsın (aşağı bakın), `EMAIL_ENABLED=true`.
6. Panelde **Test et**: IMAP girişi + SMTP `EHLO/STARTTLS/giriş/NOOP`; e-posta göndermez.

Davranış: `INBOX`'taki **okunmamış** postaları `EMAIL_POLL_SECONDS` aralığında okur (en çok 10 posta/yoklama, 1 MB üstü atlanır). Yalnız izinli göndericilerin postası okunur ve "görüldü" işaretlenir; diğer postalara dokunulmaz. Gövde `text/plain` bölümüdür (yoksa HTML'den metin); alıntı satırları (`>`), "On … wrote:" bölümü, imza ve eklentiler atılır; en çok 4000 karakter. Yanıt `Re:` konusu, `In-Reply-To`/`References` ve `Auto-Submitted: auto-replied` başlıklarıyla `EMAIL_FROM` adresinden gider.

**Sahtecilik savunması.** `Kimden` başlığı tek olmalı ve tek adres içermeli (iki `From` başlığı veya iki adres = sahte). `EMAIL_REQUIRE_AUTH_RESULTS` açıkken, **sizin posta sunucunuzun** yazdığı `Authentication-Results` başlığında **hizalı** bir geçiş aranır; yalnız "pass" yetmez, tanımlayıcı özellik şarttır:

- `spf=pass` yalnız `smtp.mailfrom` alanı varsa ve alanı `Kimden` alanıyla **aynıysa** sayılır (`smtp.helo` hiç sayılmaz; `smtp.mailfrom` yoksa başarısız);
- `dkim=pass` yalnız `header.d` varsa ve `Kimden` alanına eşitse ya da onun üst alanıysa (nokta sınırında; `com`, `co.uk` gibi herkese açık sonekler asla) sayılır;
- `dmarc=pass` yalnız `header.from` `Kimden` alanıyla aynıysa sayılır.

`EMAIL_AUTHSERV_ID` (önerilir) boşken **en üstteki** başlık güvenilir sayılır: sunucunuz her postaya kendi başlığını en üste yazıyorsa güvenlidir, yazmıyorsa gönderen kendi sahte başlığını üstte taşıyabilir. Doluysa yalnız bu kimliği taşıyan **ve en üstte olan** başlık sayılır; aynı kimliği taşıyan ikinci bir başlık (sahte konum) postayı düşürür. Kimliği bir postanın ham başlığından okuyun (`Authentication-Results: mx.google.com; …` ise `mx.google.com`). Başlık yoksa/başarısızsa posta düşer ve (gizlilik için) okunmamış kalır. Sağlayıcınız bu başlığı eklemiyorsa seçeneği kapatmak yerine sağlayıcı değiştirmeniz önerilir: kapatırsanız izinli adres taklit edilerek bota komut verilebilir.

Maliyet: gönderen ve kimlik denetimi **yalnız başlık indirilerek** yapılır; gövde ancak denetimden geçen postalar için indirilir. Yoklama başına en çok 30 başlık ve 10 gövde işlenir (kalanı sonraki yoklamaya kalır); atlanan postalar `(UIDVALIDITY, UID)` ile hatırlanır.

Atlananlar: `Auto-Submitted` (değeri `no` değilse), `Precedence: bulk/junk/list`, `List-Id`/`List-Unsubscribe`, `no-reply`/`mailer-daemon` göndericileri, kendi adresiniz.

## Sorun giderme

| Belirti | Olası neden | Çözüm |
|---|---|---|
| Sağlayıcı webhook'u doğrulayamıyor (Meta/Slack) | Kanal kapalı (404), adres http veya Komuta girişi arkada | `*_ENABLED` açın, https adresi kullanın, `/channels/*` yolunu Komuta girişinden muaf tutun |
| WhatsApp doğrulama `403` | Verify token farklı | Panelde yeniden "Üret", yeni değeri Meta'ya girin |
| WhatsApp mesajlarına `403` | `WHATSAPP_APP_SECRET` yanlış | App settings > Basic'ten tekrar kopyalayın |
| Slack `403` | İmza anahtarı yanlış veya sunucu saati 5 dk'dan fazla kaymış | Signing Secret'ı kontrol edin, NTP |
| Teams `401` | `TEAMS_APP_ID` Azure ile aynı değil, süre/saat sorunu | App ID'yi kontrol edin; sunucu saati |
| Teams `403 service url` | Etkinliğin `serviceUrl` değeri Bot Framework son eklerinde değil | Yalnız Azure Bot Service kullanın |
| Panelde durum "Hata" | Son gönderim başarısız | Rozetteki kod: `*_auth` token/parola, `*_not_found` kimlik, `*_timeout`/`*_unreachable` ağ, `email_tls` sertifika |
| Bot kimseye yanıt vermiyor | İzin listesi boş veya kimlik yanlış | "Reddedilen son gönderenler" listesinden kimliği ekleyin |
| WhatsApp hatırlatması gitmiyor | 24 saatlik pencere doldu | Kullanıcı bota bir mesaj yazsın |
| Teams hatırlatması gitmiyor | Konuşma referansı yok | Kullanıcı bota bir kez yazsın |
| E-posta hiç gelmiyor | Gönderen listede değil, DKIM/SPF geçmiyor, IMAP kapalı | Test düğmesi; sunucu günlüğü "message dropped" satırları; `EMAIL_ALLOWED_SENDERS` |
| `email_auth` | Normal parola kullanılıyor | Uygulama parolası üretin |
| `email_tls` | Sertifika adı sunucuyla eşleşmiyor | Doğru sunucu adını yazın; kendi imzalı sertifika kabul edilmez |
| `email_starttls` | SMTP portu düz metin | `587` (STARTTLS) veya `465` (SSL) kullanın |
| Onay istendi ama işlem çalışmadı | Kod yanlış/geç, ya da e-posta kanalı | `EVET 123456` aynen, 120 sn içinde, aynı hesaptan; e-postada onay yoktur |
| Panel API `424` | Sağlayıcı reddetti/ulaşılamadı | Türkçe mesaj ve kod gösterilir; ayrıntı sağlayıcı panelinde |

## v1'de desteklenmeyenler

- WhatsApp: şablon (template) mesajlar, medya (ses/görsel/belge), gruplar, okundu bilgisi.
- Teams: kanal ve grup sohbetleri, Adaptive Card/düğmeli onay (onay metinle kodla yapılır), dosya paylaşımı, emulator/yerel test belirteçleri (yalnız Bot Framework Service belirteçleri).
- Slack: ileti dizisi dışı kanal dinleme (yalnız DM ve `@bot`), slash komutları, Block Kit, dosyalar, Socket Mode.
- E-posta: onay gerektiren araçlar, ekler, çoklu posta kutusu, OAuth2 girişi (yalnız parola/uygulama parolası), IMAP IDLE (yoklama kullanılır), yanıtta alıntı.
- Hepsi: komutlar her kanalda çalışır (`/hafiza`, `/gorevler`…) ama e-postada yalnız okuma komutları; `/start` ve `/id` Telegram'a özeldir. Kanal başına tek bot hesabı. Gelen medya sessizce yok sayılır. Kanal ayarlarında "herkese izin" yoktur ve eklenmeyecektir.

## Bilinen sınırlar ve gizlilik notları (düzeltilmeyenler)

- **Mesajlar yapay zekâ sağlayıcısına gider.** Kanaldan gelen metin, hafıza ve araç çıktıları Telegram'dakiyle aynı şekilde yapılandırdığınız LLM sağlayıcısına (ve web aramasında arama sağlayıcısına) iletilir; kanalı açmak bu veri akışını o kanaldaki kullanıcılar için de açar.
- **Geçmiş ve hafıza saklanır.** Kanal kullanıcılarının sohbet geçmişi ve anıları SQLite veritabanında (`DATA_DIR`) şifresiz, gönderen başına (türetilmiş negatif sohbet kimliğiyle) tutulur; `/ui/#/hafiza` sayfasında "izinli değil" etiketiyle görünebilir (etiket yalnız Telegram izin listesine bakar). Silmek için o sayfayı kullanın.
- **Panelde son reddedilen gönderenler** (en çok 5 kimlik, telefon numarası/e-posta dahil) bellekte tutulur, yeniden başlatmada silinir ve yalnız giriş yapmış yöneticiye gösterilir.
- **Teams konuşma referansı** (`DATA_DIR/channel-refs.json`): kimlikler ve servis adresi; sır yok, dosya izni 600.
- **E-postada `web.search` açık kalır.** `web.read` kapalı, ama arama sorgusu arama sağlayıcısına gider; enjekte edilmiş bir posta sorguya hafıza metni koydurabilir (sağlayıcı dışında kimse görmez). Tamamen kapatmak için e-posta kanalını kullanmayın.
- **`EMAIL_AUTHSERV_ID` boşken** en üstteki `Authentication-Results` başlığına güvenilir (yukarıya bakın).
- **Tekrar eleme belleği süreç içidir** (yeniden başlatmada sıfırlanır); replay penceresi 10 dakikadır.
- **Slack:** `SLACK_TEAM_ID` boşsa çalışma alanı denetimi yapılmaz; DM'e yanıt için botun `chat:write` izni yeterlidir.
- Kanal kullanıcıları `/ui` panelinde değil yalnız kendi kanallarında konuşur; panel yetkisi vermez.
