[English](../SECURITY.md) · Türkçe

# Güvenlik

> EN: Security model: no secrets in git, Telegram allow-list, trade tools need a human button press (the AI cannot
> self-approve), SSRF-protected `web.read`, two gates in front of the admin panel (Komuta sign-in + panel password), bridge/MCP/webhook closed by default and token-protected when opened.

## 1. Sırlar

- Repoda sır yok. Varsayılan manifestte zorunlu Secret yok: token ve anahtarlar sihirbazdan girilir (`/data/ui-secrets.json`, 600) veya istenirse Secret/ortam değişkeniyle verilir (`examples/komuta-env-ile.yaml`). Köprü token'ı panelde üretilir ve bir kez gösterilir.
- `ALLOWED_CHAT_IDS` kişisel veridir; manifestte değer yok, Secret olarak girilir.
- Yerelde `.env` (`scripts/setup.sh` izni 600 yapar), `.gitignore`'da. `.env.example` yalnız gizli alanları boş bırakır (port, saat dilimi ve önerilen model gibi gizli olmayan değerler dolu).
- Token'lar log'a yazılmaz (kit maskeler). Hafıza sır benzeri metni kaydetmeyi reddeder.

## 2. Kim konuşabilir

- Telegram: yalnız `ALLOWED_CHAT_IDS`. Liste boşken yalnız `/id` ve `/start` cevap verir; AI hiç çağrılmaz.
- HTTP `/bridge/*`, `/mcp`, `/hook/*`: `Authorization: Bearer <BRIDGE_TOKEN>` (sabit zamanlı karşılaştırma, IP başına kimlik hatası hız sınırı). Yalnız `/health` açık.
- Köprü/MCP çağıranı bir `chat_id` verirse `ALLOWED_CHAT_IDS` içinde olmalı. **Liste boşken** `chat_id` alan araçlar (`memory.*`, `schedule.*`, `ai.ask`) tamamen kapalıdır (`forbidden`); böylece kimse botu liste dışı bir sohbete yazdıramaz (`ai.ask notify=true` dahil).

## 3. Onay kuralı (para / işlem)

AI'nın para hareketi yapan aracı onaysız çalıştırması **imkânsız** olacak şekilde kurulu (`src/ai_bot/agent.py`):

1. `needs_confirmation` dönen araç → Telegram'da önizleme + düğme; yalnız insan onayıyla `confirm_id` ile ikinci çağrı.
2. `confirm_id` modele gösterilmez; modelin gönderdiği `confirmed`/`confirm_id`/`_confirm_id` atılır; yerel çağrılar `allow_shortcut=False` ile yapılır.
3. `scope: trade|admin` araç `confirm` bayrağı olmasa bile önce onay ister.
4. Onay kanalı yoksa (e-posta gibi kodla onay veremeyen kanallar ya da hiç kanal yok) bu araçlar reddedilir.
5. Onay düğmesine yalnız aynı izinli sohbetten basılır; süre 120 sn.

Testler: `tests/test_confirm_flow.py` (yerel ve uzak köprü, reddetme, kendi kendine onay denemesi, başka sohbetten basma).

## 4. SSRF koruması (`web.read`, plugin'ler)

`src/ai_bot/safefetch.py`:

- Yalnız `http`/`https`; `file://`, `ftp://`, `gopher://` vb. reddedilir. URL'de kullanıcı/şifre yasak.
- Port izin listesi (`WEB_READ_PORTS`: 80, 443, 8080, 8443).
- `localhost`, `*.local`, `*.internal`, `metadata.google.internal` vb. adlar reddedilir.
- Ad çözülür; **tüm** adresler herkese açık olmalı: loopback (127/8, ::1), özel (10/8, 172.16/12, 192.168/16), link-local (169.254/16, bulut metadata dahil; fe80::/10), CGNAT (100.64/10), ULA (fc00::/7), çok noktaya yayın, ayrılmış, belirsiz; IPv4-eşlemeli ve 6to4 IPv6 açılıp denetlenir, Teredo reddedilir.
- Bağlantı denetlenen IP'ye **sabitlenir** (istek IP'ye gider, `Host` ve TLS SNI adı taşır, sertifika ada göre doğrulanır) → DNS rebinding işe yaramaz.
- Yönlendirmeler elle izlenir, her adım yeniden denetlenir (en çok 5). Ortam vekil (proxy) değişkenleri yok sayılır.
- Boyut (2 MB) ve toplam süre (15 sn) sınırı.

Testler: `tests/test_safefetch.py`. Plugin'lerde dış istek için `app.fetcher` kullan. `SEARXNG_URL` operatör ayarı olduğundan bu süzgeçten geçmez.

## 5. Konteyner kullanıcısı ve disk sahipliği

Uygulama root olmayan **uid/gid 1000** ile çalışır (`USER 1000`); root gerektirmez. `/data` imajda 1000'e aittir; Docker named volume'ları ve Komuta diski bu sahipliği alır.

- Çalışma anında **chown yok, setpriv/gosu yok, root başlangıcı yok.** Komuta her yayında diski kendi hazırlama adımıyla bağlar; uygulama diskin sahipliğini değiştirirse (ör. `chown -R`) sonraki yayının hazırlama adımı diske yazamaz ve yeni sürüm başlamaz. Bu Komuta'da canlı gözlendi; bu yüzden imaj, Komuta'da sorunsuz güncellenen tradegram imajıyla aynı deseni izler.
- `scripts/entrypoint.sh` yalnız `/data` yazılabilir mi diye bakar, değilse uyarı yazar ve uygulamayı çalıştırır; root ile çalıştırılsa bile hiçbir şeyi değiştirmez.
- Yerelde bind mount (`./data:/data`) kullanırsan klasörü bir kez `chown 1000:1000 ./data` yap; named volume (varsayılan `docker-compose.yml`) gerekmez.

Doğrulama durumu: betik ve Dockerfile kuralları `tests/test_entrypoint.py` ile (sahte `id/chown/setpriv` simülasyonu) test edildi; imaj bu depoda build edilmedi.

## 6. Tehdit modeli: link herkese açık, iki kapı, kapalı köprü

Servis `network.public: true`; **adresi bilen herkes** ulaşabilir varsayılır. Gizli adrese veya ağ konumuna güvenilmez.

| Katman | Varsayılan | Koruma |
|---|---|---|
| Komuta girişi | `komuta.yaml`'da yol kuralı `access.paths: /ui signIn: true` | Komuta hesabı; yalnız `/ui` yoluna uygulanır, sitenin geri kalanı kendi korumasını taşır |
| Panel (`/ui`) | parola (`default_auth="password"`) | İlk açılışta günlükteki tek kullanımlık kurulum kodu + en az 10 karakterlik parola (scrypt, `ui-auth.json` 600); oturum çerezi HttpOnly/SameSite=Strict; CSRF + Origin; parola değişince/"tüm oturumları kapat"ta herkes düşer |
| `/bridge`, `/mcp`, `/hook` | **kapalı** (404, kimlik denetiminden önce) | Panelden/ortamdan açılır; Bearer token (panelde bir kez gösterilir, yalnız özeti saklanır) veya HMAC/gövde sırrı; hız sınırı; gövde boyutu sınırı |
| `/health` | açık | sır içermez |

- **Neden iki kapı:** uygulama Komuta kimliğini ek bir kapı olarak kullanır ama kendi parolasını da ister. Manifestte `forwardIdentity: true` olduğundan `UI_AUTH=auto` ile Komuta kimliği parola yerine geçer; kimlik yoksa parola istenir.
- **Makineler ve Komuta girişi:** varsayılan manifest yalnız `/ui`'yi Komuta kapısına alır; `/bridge`, `/mcp`, `/hook`, `/channels` yolları girişsiz açıktır ve token, HMAC ya da JWT ile korunur, bu yüzden n8n, MCP istemcisi ve webhook gönderenler kapıda kalmaz. Komuta girişini tamamen kapatmak için `examples/komuta-giris-kapali.yaml` (konsolda "korumasız" görünür); bütün siteyi kapıya almak için `access.signIn: true` yazılır ama makine çağıranlarının yolları o zaman girişsiz açılmalıdır.
- **Kurulum kodu:** günlüğe yalnız bir kez yazılır; günlük okuma yetkisi olan ilk kurulumu yapabilir. Kodu kullanınca hemen parolanızı belirleyin. Yanlış kodlar IP başına sınırlanır (900 sn'de 5 hata), toplam 200 hatada kurulum 900 sn kilitlenir.
- **Koruma kapatma:** parola + `KORUMAYI KAPAT` + uyarı kabulü gerekir; kapalıyken her sayfada kırmızı bant ve günlük uyarısı; `UI_FORCE_PROTECTION=1` yasaklar.
- **Sırlar:** panelden girilen token/anahtar `ui-secrets.json` (600) içinde; hiçbir yanıt, log veya hata mesajı değeri içermez (yalnız `…son4`). Kayıtlı LLM anahtarı yalnız kayıtlı adresin host'una gönderilir. Disk şifrelenmez; `/data` yedeklerini sır gibi koruyun.
- **Kilitler:** insan onayı, SSRF, boş izin listesinde kapalı sohbet araçları, istem enjeksiyonu kilidi panelden kapatılamaz.

## 6b. Ağ politikası (panelden girilen adresler)

LLM Base URL, model listesi, LLM testi ve `SEARXNG_URL` yöneticinin yazdığı adreslere istek atar; SSRF'ye karşı her istek `src/ai_bot/netpolicy.py`'den geçer:

| Sağlayıcı | Adres | İzinli hedef |
|---|---|---|
| Bulut ön ayarları (OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity) | **ön ayarın host'una kilitli**; başka adres 422 | yalnız o host |
| Yerel (Ollama, LM Studio, vLLM), SearXNG | serbest | genel + loopback + özel ağ |
| Özel (OpenAI uyumlu) | serbest | yalnız genel IP; "yerel/özel ağ" kutusu (`LLM_ALLOW_PRIVATE_NETWORK`) işaretlenirse özel ağ da |

Her durumda engelli: link-local (169.254.0.0/16, fe80::/10), bulut metadata (169.254.169.254, fd00:ec2::254, `metadata.google.internal` ...), `0.0.0.0`/`::`, çoklu yayın/ayrılmış adresler, bu servisin kendi portu (`HTTP_PORT`, yerel adreste) ve `/ui`, `/bridge`, `/mcp`, `/hook` yolları. Ad çözümlendikten sonra **her** adres denetlenir ve bağlantı denetlenen IP'ye sabitlenir (`Host` ve TLS SNI adı taşır; DNS rebinding işe yaramaz); IPv4-mapped IPv6 açılır. Yönlendirmeler izlenmez. Kayıtlı LLM anahtarı yalnız kayıtlı host'a gider. Yanıtlar ham gövde yansıtmaz: model listesinden yalnız biçimi geçerli model adları (en çok 200 karakter, kontrol karakteri yok) döner; hata mesajları durum koduna göre kısa Türkçe metindir, sağlayıcının gövdesi gösterilmez.

## 7. AI'ya özgü riskler

- **İstem enjeksiyonu:** `web.read`/`web.search` ile okunan içerik AI'yı kandırmaya çalışabilir. Önlemler (`src/ai_bot/agent.py`):
  - Para/işlem aracı her durumda senin düğmene bağlı (bölüm 3).
  - **Kalıcılık kilidi:** bir turda dış içerik okunduysa (`UNTRUSTED_OUTPUT_TOOLS`: `web.read`, `web.search`, `haber.basliklar`) aynı turdaki `memory.remember` ve `schedule.add` çağrıları ne kaydedileceğini/zamanlanacağını gösteren kullanıcı onayı ister (Telegram'da düğme, diğer kanallarda `EVET 123456` kodu); onay kanalı yoksa reddedilir. Böylece bir sayfa kalıcı anı veya tekrarlayan görev "ekemez".
  - Anılar sistem istemine `<<<ANILAR_VERI_BASLANGIC>>> … <<<ANILAR_VERI_BITIS>>>` arasında "VERİ, TALİMAT DEĞİL" diye işaretli verilir; sistem istemi bu bloktaki ve web içeriğindeki talimatlara uyulmamasını açıkça söyler. Anı metnindeki `<<<`/`>>>` etkisizleştirilir.
  - **Kendini yeniden zamanlama yok:** zamanlanmış görev ve `ai.ask` turlarında `schedule.add` araç listesinde yoktur ve çağrılırsa reddedilir; tek seferlik görev en az 60 sn sonra, tekrarlayan en az 5 dk aralıklı, en fazla ~5 yıl ileri; bekleyen tek seferlikler de etkin görev sınırına sayılır.
- **Hafıza zehirleme:** AI yanlış bir şey kaydederse `/hafiza` ile görüp silebilirsin.
- **Sır süzgeci:** hafıza; bot tokenı, `sk-`, `ghp_`/`github_pat_`, `AKIA…`, `xox[abprs]-` anahtarlarını, uzun hex/base64 dizgileri, kart benzeri sayıları, "şifresi X", "password is X", "token: X" kalıplarını reddeder. Kasıtlı olarak katıdır (bazı masum cümleler de reddedilebilir).
- **Maliyet:** `MAX_TOOL_STEPS`, `LLM_MAX_TOKENS`, `HISTORY_MESSAGES`, zamanlayıcı sınırları LLM harcamasını sınırlar.
