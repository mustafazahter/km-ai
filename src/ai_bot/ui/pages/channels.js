/* Kanallar: WhatsApp, Microsoft Teams, Slack, e-posta. Her kanal bir kart: açma anahtarı, durum rozeti,
   kurulum rehberi, ayar formu (gizli değerler yalnız yazılır), izin listesi, test düğmesi.
   Metin her zaman textContent (KUI.el) ile eklenir; innerHTML yok. API: /ui/api/channels (panel_channels.py). */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  var MARKS = { whatsapp: "message", teams: "users", slack: "hash", email: "mail" };
  var TAGLINES = {
    whatsapp: "WhatsApp Cloud API (Meta) üzerinden yazışın.",
    teams: "Azure Bot Service ile Teams'te birebir sohbet.",
    slack: "Slack'te bota DM atın ya da kanalda anın.",
    email: "Posta kutusunu yoklar, izinli adreslere e-postayla yanıt verir."
  };
  var STATUS = {
    off: ["Kapalı", "outline", null],
    missing: ["Eksik ayar", "accent", null],
    ready: ["Hazır", "success", "success"],
    error: ["Hata", "danger", "danger"]
  };
  var ERRORS = {
    auth: "Sağlayıcı erişimi reddetti (token/parola geçersiz).",
    not_found: "Sağlayıcı kaynağı bulamadı (kimlik yanlış olabilir).",
    rate_limited: "Sağlayıcı istek sınırına ulaştı.",
    bad_request: "Sağlayıcı isteği kabul etmedi.",
    provider_down: "Sağlayıcı yanıt vermiyor.",
    timeout: "Sağlayıcıya ulaşılamadı (zaman aşımı).",
    unreachable: "Sağlayıcıya ulaşılamadı (ağ hatası).",
    not_configured: "Ayarlar eksik.",
    scope: "Uygulamada gerekli izin eksik.",
    channel: "Kanal veya kişi bulunamadı.",
    service_url: "Teams yanıt adresi güvenli görünmüyor.",
    tls: "TLS sertifikası doğrulanamadı.",
    starttls: "SMTP sunucusu STARTTLS desteklemiyor.",
    blocked: "Sunucu adresi güvenlik politikasıyla engellendi.",
    no_target: "Bu kullanıcıya henüz ulaşılamıyor (önce o yazmalı).",
    redirect: "Sağlayıcı yönlendirme döndürdü (izlenmedi).",
    internal: "Beklenmeyen hata."
  };

  var GUIDES = {
    whatsapp: [
      "developers.facebook.com adresinde Meta for Developers hesabınızla girin, 'My Apps' > 'Create App' ile 'Business' türünde bir uygulama oluşturun.",
      "Uygulamaya 'WhatsApp' ürününü ekleyin (Add product > WhatsApp > Set up).",
      "WhatsApp > API Setup sayfasında gönderen numaranın altındaki 'Phone number ID' değerini kopyalayıp aşağıdaki 'Telefon numarası kimliği' alanına yazın.",
      "Aynı sayfadaki geçici erişim belirteci 24 saatte biter. Kalıcı belirteç için business.facebook.com > Business Settings > Users > System Users altında bir sistem kullanıcısı oluşturun, uygulamayı atayın, 'Generate token' deyin, whatsapp_business_messaging ve whatsapp_business_management izinlerini seçip belirteci 'Erişim belirteci' alanına yapıştırın.",
      "App settings > Basic sayfasındaki 'App secret' değerini ('Show' ile açın) 'Uygulama gizli anahtarı' alanına yapıştırın.",
      "'Doğrulama kodu' satırındaki 'Üret' düğmesine basın ve açılan değeri kopyalayın (yalnız bir kez gösterilir). Sonra 'Kaydet' deyin ve kartın üstündeki anahtarla kanalı açın.",
      "Meta'da WhatsApp > Configuration > Webhook bölümünde 'Edit' deyin: 'Callback URL' olarak aşağıdaki Webhook adresini, 'Verify token' olarak az önce kopyaladığınız değeri girip 'Verify and save' deyin. Adres internetten https ile erişilebilir olmalı.",
      "Aynı sayfada 'Webhook fields' içinde 'messages' alanına 'Subscribe' olun.",
      "'İzinli numaralar' listesine kendi numaranızı ülke koduyla ekleyin (ör. 905551112233), sonra WhatsApp'tan bot numarasına yazın. Test numarasıyla yalnız API Setup > 'To' alanına eklediğiniz alıcılar yazışabilir.",
      "'Test et' düğmesi yalnız belirteci ve numara kimliğini kontrol eder, kimseye mesaj göndermez. Hatırlatma gibi botun ilk yazdığı mesajlar yalnız kullanıcının son mesajından sonraki 24 saat içinde iletilir."
    ],
    teams: [
      "portal.azure.com adresinde 'Create a resource' > 'Azure Bot' ile bir bot kaynağı oluşturun (tür: Single Tenant önerilir).",
      "Bot hazır olunca kaynağın 'Configuration' sayfasındaki 'Microsoft App ID' ve 'App Tenant ID' değerlerini kopyalayıp aşağıdaki alanlara yazın.",
      "Aynı sayfada 'Manage Password' > 'Certificates & secrets' > 'New client secret' ile bir gizli anahtar üretin. 'Value' sütunundaki değeri (yalnız bir kez görünür) 'İstemci gizli anahtarı' alanına yapıştırın.",
      "Configuration > 'Messaging endpoint' alanına aşağıdaki Mesaj adresini yapıştırıp 'Apply' deyin. Adres internetten https ile erişilebilir olmalı.",
      "Kaynağın 'Channels' sayfasında 'Microsoft Teams' kanalını ekleyin, şartları kabul edip 'Apply' deyin.",
      "Botu Teams'te görmek için uygulama paketi gerekir: dev.teams.microsoft.com > Apps > New app > 'App features' > 'Bot' bölümünde Bot ID olarak Microsoft App ID'yi girin, kapsam olarak 'Personal' seçin, 'Publish' > 'Preview in Teams' ile kendi hesabınıza kurun.",
      "Kullanıcı kimliği için Azure portal > Microsoft Entra ID > Users > kullanıcı > 'Object ID' (GUID) değerini 'İzinli kullanıcılar' listesine ekleyin; Teams kimlikleri (29:…) kabul edilmez. Bilmiyorsanız bota bir kez yazın; kimlik 'Reddedilen son gönderenler' listesinde görünür, oradan 'İzin ver' deyin.",
      "'Kaydet' deyin, kanalı açın, 'Test et' ile bot belirtecinin alındığını doğrulayın ve Teams'ten bota yazın."
    ],
    slack: [
      "api.slack.com/apps adresinde 'Create New App' > 'From scratch' ile bir uygulama oluşturun ve çalışma alanınızı seçin.",
      "OAuth & Permissions > 'Bot Token Scopes' altına chat:write, im:history ve app_mentions:read ekleyin.",
      "App Home > 'Show Tabs' bölümünde 'Messages Tab' anahtarını açın ve 'Allow users to send Slash commands and messages from the messages tab' kutusunu işaretleyin.",
      "OAuth & Permissions > 'Install to Workspace' deyin. Çıkan 'Bot User OAuth Token' (xoxb-…) değerini 'Bot kullanıcı belirteci' alanına yapıştırın.",
      "Basic Information > App Credentials > 'Signing Secret' değerini 'İmza anahtarı' alanına yapıştırın, 'Kaydet' deyin ve kanalı açın.",
      "Event Subscriptions > 'Enable Events' açın, 'Request URL' alanına aşağıdaki Olay adresini yapıştırın. 'Verified' görünmelidir (kanal açık ve kayıtlı olmalı, adres internetten https ile erişilebilir olmalı).",
      "'Subscribe to bot events' altına message.im ve app_mention ekleyip 'Save Changes' deyin. Slack uygulamayı yeniden kurmanızı isterse 'reinstall' edin.",
      "Kendi kimliğiniz için Slack'te profilinize tıklayın > ⋯ > 'Copy member ID' (U… ile başlar) ve 'İzinli kullanıcı kimlikleri' listesine ekleyin. Çalışma alanı kimliğini (T…) 'Çalışma alanı kimliği' alanına yazmanız önerilir. Sonra Slack'te bota doğrudan mesaj (DM) yazın. Kanalda @bot ile anarsanız yanıt kanala değil size özel mesajla (DM) gelir."
    ],
    email: [
      "Bot için ayrı bir posta kutusu açmanız önerilir (ör. bot@alanadiniz.com). Kendi gelen kutunuzu vermeyin.",
      "Hesapta IMAP erişimini açın. İki adımlı doğrulama varsa 'uygulama parolası' üretin. Gmail: Google Hesabı > Güvenlik > 2 Adımlı Doğrulama > Uygulama şifreleri. Microsoft 365/Outlook: hesap güvenliği > Uygulama parolaları (yönetici IMAP ve 'Authenticated SMTP'ye izin vermeli). Yandex ve Zoho: hesap > Güvenlik > Uygulama parolaları.",
      "IMAP sunucusunu yazın (ör. imap.gmail.com, outlook.office365.com), port 993. SMTP sunucusunu yazın (ör. smtp.gmail.com, smtp.office365.com), port 587 (STARTTLS) veya 465 (SSL). Şifresiz bağlantı denenmez.",
      "Kullanıcı adı genelde tam e-posta adresidir. Parola alanına uygulama parolasını yazın. 'Gönderen adresi' posta kutusunun adresi olsun.",
      "'İzinli gönderenler' listesine botla yazışacağınız kendi adresinizi ekleyin (tam adres, harf büyüklüğü önemsiz).",
      "'DKIM/SPF doğrulaması iste' kutusunu açık bırakın: 'Kimden' adresi kolayca taklit edilir. Sağlayıcınız Authentication-Results başlığı eklemiyorsa postalar düşürülür; o durumda başka bir sağlayıcı seçmeniz önerilir. 'Posta sunucusu kimliği' alanına gelen bir postanın 'Authentication-Results: <kimlik>;' başlığındaki kimliği (ör. mx.google.com) yazın; böylece yalnız sizin sunucunuzun başlığı güvenilir sayılır.",
      "Sunucunun dışarıya IMAP (993) ve SMTP (587/465) bağlantısı kurabilmesi gerekir (Komuta çıkış ağı izni).",
      "'Kaydet' deyin, kanalı açın, 'Test et' ile IMAP girişini ve SMTP bağlantısını doğrulayın (e-posta gönderilmez), sonra izinli adresinizden bota e-posta yazın. Yanıt en geç yoklama aralığı kadar sonra gelir.",
      "E-posta kanalında onay gerektiren araçlar (ör. alım-satım) çalışmaz: gönderen adresi sahte olabileceği için onay e-postayla verilemez. Onay gereken işler için Telegram, WhatsApp, Slack veya Teams kullanın."
    ]
  };

  // Settings split into small cards (name pattern -> card title); one card for channels with few fields
  var GROUPS = {
    email: [
      ["Gelen posta (IMAP)", /^EMAIL_(IMAP_|PASSWORD)/, "Bot bu kutuyu yoklar."],
      ["Giden posta (SMTP)", /^EMAIL_(SMTP_|FROM)/, "Yanıtlar bu sunucudan gider."],
      ["Doğrulama ve yoklama", /^EMAIL_/, "Sahte gönderene karşı kontrol ve kontrol sıklığı."]
    ],
    whatsapp: [["Cloud API bilgileri", /./, "Gizli değerler yalnız yazılır; kaydedilince bir daha gösterilmez."]],
    teams: [["Azure Bot bilgileri", /./, "Gizli değerler yalnız yazılır; kaydedilince bir daha gösterilmez."]],
    slack: [["Slack uygulaması", /./, "Gizli değerler yalnız yazılır; kaydedilince bir daha gösterilmez."]]
  };

  var PATH_LABELS = { webhook: "Webhook adresi", messages: "Mesaj adresi", events: "Olay adresi" };

  var PROMPT_NAME = { whatsapp: "SYSTEM_PROMPT_WHATSAPP", teams: "SYSTEM_PROMPT_TEAMS", slack: "SYSTEM_PROMPT_SLACK", email: "SYSTEM_PROMPT_EMAIL" };
  var PROMPT_HINT = {
    whatsapp: "Örn. Kısa yaz; WhatsApp'ta tablo ve uzun listeler okunmaz.",
    teams: "Örn. Resmî ve net bir dil kullan; maddeleri kısa tut.",
    slack: "Örn. Kısa yanıt ver, gerekirse madde işaretleri kullan.",
    email: "Örn. Her e-postaya selamla başla, imza ile bitir; 'siz' diye hitap et."
  };
  var GUIDE_TIME = { whatsapp: "≈ 15 dakika", teams: "≈ 20 dakika", slack: "≈ 10 dakika", email: "≈ 5 dakika" };
  // guides open in the shared help modal (help.js)
  Object.keys(GUIDES).forEach(function (name) {
    A.help.topics["channel-" + name] = function () {
      var titles = { whatsapp: "WhatsApp", teams: "Microsoft Teams", slack: "Slack", email: "E-posta" };
      return { icon: MARKS[name], title: titles[name] + " kurulum rehberi", time: GUIDE_TIME[name], intro: TAGLINES[name],
        steps: GUIDES[name].map(function (t) { return [t]; }) };
    };
  });

  function section(no, title, sub, children, cls) {
    return h("section", { class: "card channel-section" + (cls ? " " + cls : ""), "aria-label": title },
      h("div", { class: "section-head" }, h("span", { class: "step-no", "aria-hidden": "true", text: String(no) }),
        h("div", { class: "grow" }, h("h2", { text: title }), sub ? h("p", { class: "card-sub", text: sub }) : null)),
      h("div", { class: "section-body stack" }, children));
  }

  function errorText(code) {
    var key = String(code || "").replace(/^[a-z]+_/, "");
    return ERRORS[key] || "Bilinmeyen hata.";
  }

  function copyButton(text, label) {
    var b = A.button("Kopyala", { size: "sm", icon: "check", aria: label + " kopyala" });
    b.addEventListener("click", function () {
      A.copyText(text).then(function (ok) { KUI.toast(ok ? "Kopyalandı" : "Kopyalanamadı; elle seçin", { kind: ok ? "success" : "danger" }); });
    });
    return b;
  }

  /** "Webhook adresi  https://…  [Kopyala]" */
  function copyRow(label, value) {
    return h("div", { class: "copy-row" },
      h("span", { class: "label", text: label }),
      h("div", { class: "copy-line" }, h("code", { class: "copy-value", text: value }), copyButton(value, label)));
  }

  // ------------------------------------------------------------ allow-list editor
  function allowList(ch, f, save) {
    var box = h("div", { class: "chat-list" });
    var input = h("input", { class: "input", id: "al-" + ch.name, name: f.name, autocomplete: "off", spellcheck: "false",
      placeholder: ch.name === "whatsapp" ? "905551112233" : ch.name === "slack" ? "U024BE7LH" : ch.name === "email" ? "ad@ornek.com" : "Entra Object ID" });
    var items = (f.items || []).slice();
    var denied = h("div", { class: "stack-sm" });

    function commit(next, btn) {
      return KUI.busy(btn, save(f.name, next).then(function (r) {
        KUI.toast("İzin listesi güncellendi; hemen geçerli.", { kind: "success" });
        return r;
      }).catch(KUI.errorToast));
    }
    function draw() {
      KUI.clear(box);
      if (!items.length) box.appendChild(A.badge("Liste boş: kimse bota yazamaz", "outline"));
      items.forEach(function (id) {
        var x = h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", "aria-label": id + " kaydını çıkar" }, A.icon("x"));
        x.addEventListener("click", function () {
          KUI.confirm({ title: "Kişi çıkarılsın mı?", body: "Bu kişi artık bota yazamaz.", confirmText: "Çıkar", cancelText: "Vazgeç", danger: true })
            .then(function (ok) { if (ok) commit(items.filter(function (i) { return i !== id; }), x); });
        });
        box.appendChild(h("span", { class: "chat-pill" }, h("span", { class: "pill-text", text: id }), x));
      });
      KUI.clear(denied);
      var rows = (ch.denied || []).filter(function (d) { return items.indexOf(d.id) < 0; });
      if (rows.length) {
        denied.appendChild(h("span", { class: "label", text: "Reddedilen son gönderenler" }));
        denied.appendChild(h("ul", { class: "list" }, rows.map(function (d) {
          var add = A.button("İzin ver", { kind: "primary", size: "sm", icon: "plus" });
          add.addEventListener("click", function () { commit(items.concat([d.id]), add); });
          return h("li", null, h("div", { class: "grow" }, h("div", { class: "mono-sm break", text: d.id }),
            h("div", { class: "small muted", text: KUI.timeAgo(d.at) })), add);
        })));
      }
    }
    draw();
    var add = A.button("Ekle", { icon: "plus" });
    add.addEventListener("click", function () {
      var v = input.value.trim();
      if (!v) return;
      commit(items.concat([v]), add).then(function () { input.value = ""; });
    });
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); add.click(); } });
    return h("div", { class: "stack-sm" },
      h("label", { for: input.id, text: f.label }), box,
      h("div", { class: "input-group" }, input, add),
      h("span", { class: "hint", text: f.hint }), denied);
  }

  // ------------------------------------------------------------ shared pieces
  function putChannel(name, body) {
    return KUI.api("/channels/" + name, { method: "PUT", body: body });
  }

  function statusBadge(c) {
    var s = STATUS[c.status] || STATUS.off;
    return A.badge(s[0], s[1], s[2]);
  }

  function metaLines(c, fieldsByName) {
    var rows = [];
    if (c.enabled && c.missing.length) {
      var labels = c.missing.map(function (n) { return fieldsByName && fieldsByName[n] ? fieldsByName[n].label : n; });
      rows.push(h("div", { class: "cluster" }, h("span", { class: "small muted", text: "Doldurulması gerekenler:" }),
        labels.map(function (l) { return A.badge(l, "outline"); })));
    }
    if (c.enabled && c.allowed.length === 0 && !c.missing.length) {
      rows.push(h("div", { class: "small muted", text: "İzin listesi boş: şu an kimse bota yazamaz." }));
    }
    if (c.last_inbound) rows.push(h("div", { class: "small muted", text: "Son gelen mesaj: " + KUI.timeAgo(c.last_inbound) }));
    if (c.last_poll) rows.push(h("div", { class: "small muted", text: "Son posta kontrolü: " + KUI.timeAgo(c.last_poll) }));
    if (c.last_error) {
      rows.push(h("div", { class: "callout callout-danger", role: "status" }, A.icon("alert"),
        h("span", null, h("strong", { text: "Son hata: " }), errorText(c.last_error.code), " ", h("span", { class: "mono-sm", text: c.last_error.code }))));
    }
    return rows;
  }

  function enableSwitch(ch, onUpdate) {
    var sw = h("input", { class: "switch", type: "checkbox", role: "switch", id: "en-" + ch.name, "aria-label": ch.title + " kanalını aç/kapat" });
    sw.checked = !!ch.enabled;
    sw.addEventListener("change", function () {
      var want = sw.checked;
      sw.disabled = true;
      putChannel(ch.name, { enabled: want }).then(function (r) {
        KUI.toast(want ? "Kanal açıldı." : "Kanal kapatıldı; gelen adresleri artık 404 döner.", { kind: "success" });
        onUpdate(r.channel);
      }).catch(function (e) { sw.checked = !want; KUI.errorToast(e); }).finally(function () { sw.disabled = false; });
    });
    return sw;
  }

  // ------------------------------------------------------------ overview: one compact tile per channel
  function tile(ch, onUpdate) {
    var fieldsByName = {};
    (ch.fields || []).forEach(function (f) { fieldsByName[f.name] = f; });
    var status = h("div", { class: "tile-status" });
    function draw(c) {
      KUI.clear(status);
      status.appendChild(statusBadge(c));
      var line = c.last_error ? errorText(c.last_error.code)
        : (c.enabled && c.missing.length) ? c.missing.length + " ayar eksik"
        : c.last_inbound ? "Son mesaj " + KUI.timeAgo(c.last_inbound)
        : c.enabled ? "Mesaj bekliyor" : "";
      if (line) status.appendChild(h("span", { class: "small muted", text: line }));
    }
    draw(ch);
    var open = h("a", { class: "btn btn-block", href: "#/kanallar/" + ch.name }, "Ayarla", A.icon("arrow-right"));
    var node = h("section", { class: "card channel-tile", "aria-label": ch.title },
      h("div", { class: "tile-top" },
        h("span", { class: "channel-mark" }, A.icon(MARKS[ch.name] || "plug")),
        h("h2", { class: "grow", text: ch.title }),
        enableSwitch(ch, onUpdate)),
      h("p", { class: "card-sub", text: TAGLINES[ch.name] }),
      status,
      h("div", { class: "card-foot" }, open));
    node._redraw = draw;
    return node;
  }

  function telegramTile(t) {
    var status = h("div", { class: "tile-status" });
    function draw(x) {
      KUI.clear(status);
      status.appendChild(!x.enabled ? A.badge("Kapalı", "outline")
        : x.running ? A.badge("Bağlı", "success", "success")
        : A.badge(x.configured ? "Bağlanıyor" : "Token yok", "outline"));
    }
    draw(t);
    var node = h("section", { class: "card channel-tile", "aria-label": "Telegram" },
      h("div", { class: "tile-top" },
        h("span", { class: "channel-mark" }, A.icon("send")),
        h("h2", { class: "grow", text: "Telegram" }),
        A.telegramSwitch(t.enabled, function (on) { t.enabled = on; setTimeout(function () { KUI.api("/telegram").then(draw).catch(function () { draw(t); }); }, 600); draw(t); })),
      h("p", { class: "card-sub", text: "Bot belirteci, izinli sohbetler ve bağlantı durumu." }),
      status,
      h("div", { class: "card-foot" }, h("a", { class: "btn btn-block", href: "#/telegram" }, "Ayarla", A.icon("arrow-right"))));
    node._redraw = draw;
    return node;
  }

  function renderOverview(main) {
    main.appendChild(A.pageHead("Bağlantılar", "Kanallar",
      "Botla Telegram'ın yanında WhatsApp, Microsoft Teams, Slack ve e-postadan da konuşun. Aynı yapay zekâ, aynı araçlar, aynı hafıza. Hepsi varsayılan olarak kapalıdır."));
    main.appendChild(h("div", { class: "callout" }, A.icon("shield"), h("div", null,
      h("strong", { text: "Güvenlik: " }),
      "Kanal kapalıyken gelen adresleri 404 döner. Açık kanalda yalnız izin listesindeki kişiler bota ulaşır; liste boşsa kimse yazamaz. Gelen istekler Komuta girişiyle değil, sağlayıcının imzasıyla korunur; bu yüzden /channels/ yolu Komuta girişinden muaf olmalıdır (belge: docs/KANALLAR.md).")));
    var host = h("div", { class: "channel-grid" }, A.skeleton(4));
    main.appendChild(host);
    main.appendChild(h("div", { class: "channel-general" }, A.promptCard("SYSTEM_PROMPT_EXTRA", {
      title: "Genel sistem istemi", sub: "Tüm kanallarda ve panel sohbetinde geçerli. Kanala özel istem, bunun ardından eklenir (panel sohbetinde yalnız bu geçerli).",
      placeholder: "Örn. Her zaman Türkçe yanıt ver. Bilmediğin şeyi uydurma.", hint: "Bu alan Ayarlar > Ajan bölümündeki aynı ayardır." })));
    var tiles = {};
    var tgTile = null;
    function onUpdate(c) { if (c) { var fresh = tile(c, onUpdate); if (tiles[c.name]) tiles[c.name].replaceWith(fresh); tiles[c.name] = fresh; } else load(); }
    function load() {
      return Promise.all([KUI.api("/channels"), KUI.api("/telegram")]).then(function (res) {
        var d = res[0];
        KUI.clear(host);
        tgTile = telegramTile(res[1]);
        host.appendChild(tgTile);
        d.channels.forEach(function (c) { tiles[c.name] = tile(c, onUpdate); host.appendChild(tiles[c.name]); });
      }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
    }
    load();
    var stop = KUI.poll(function () {
      return Promise.all([KUI.api("/channels"), KUI.api("/telegram")]).then(function (res) {
        res[0].channels.forEach(function (c) { if (tiles[c.name]) tiles[c.name]._redraw(c); });
        if (tgTile) tgTile._redraw(res[1]);
      });
    }, 10000);
    A.onLeave(stop);
  }

  // ------------------------------------------------------------ detail: one channel, its own page of small cards
  function detail(ch, onUpdate, opts) {
    opts = opts || {};
    var fieldsByName = {};
    ch.fields.forEach(function (f) { fieldsByName[f.name] = f; });
    var inputs = {};   // plain settings: name -> {get, initial}
    var secrets = {};  // secret inputs: name -> input
    var result = h("div", { class: "result-slot", "aria-live": "polite" });
    var statusSlot = h("span");
    var metaSlot = h("div", { class: "stack-sm" });

    function drawStatus(c) {
      KUI.clear(statusSlot);
      statusSlot.appendChild(statusBadge(c));
      KUI.clear(metaSlot);
      metaLines(c, fieldsByName).forEach(function (n) { metaSlot.appendChild(n); });
    }

    // addresses
    var urls = [];
    var keys = Object.keys(ch.paths || {});
    if (keys.length) {
      keys.forEach(function (k) {
        var url = (ch.urls && ch.urls[k]) || (location.origin + ch.paths[k]);
        urls.push(copyRow(PATH_LABELS[k] || "Adres", url));
      });
      if (!ch.base_verified) {
        urls.push(h("span", { class: "hint", text: "Adres tarayıcıdaki konumdan türetildi. Botun internetteki adresi farklıysa (UI_PUBLIC_URL tanımlı değilse) adresin başını kendi alan adınızla değiştirin." }));
      }
    } else {
      urls.push(h("div", { class: "callout" }, A.icon("info"),
        h("span", { text: "Bu kanalın dışarıdan gelen bir adresi yoktur: bot posta kutusunu belirli aralıkla yoklar." })));
    }

    // settings form (plain + secret fields) and the allow-list (its own card)
    var form = h("form", { class: "form channel-form channel-flow", novalidate: true });
    var people = h("div", { class: "stack" });
    var buckets = [];
    function bucketFor(name) {
      var def = GROUPS[ch.name] || [["Ayarlar", /./, "Gizli değerler yalnız yazılır; kaydedilince bir daha gösterilmez."]];
      var g = def.filter(function (x) { return x[1].test(name); })[0] || def[def.length - 1];
      var b = buckets.filter(function (x) { return x.key === g[0]; })[0];
      if (!b) { b = { key: g[0], sub: g[2] || null, body: h("div", { class: "stack channel-group-body" }) }; buckets.push(b); }
      return b.body;
    }
    // "Varsayılan değer" / "Ayarlı değil" add height and say nothing useful: only env/panel sources are shown
    function src(f) { return f.source === "env" || f.source === "ui" ? A.sourceLine(f, function () { onUpdate(null); }) : null; }
    ch.fields.forEach(function (f) {
      var id = "f-" + f.name;
      if (f.kind === "list") {
        people.appendChild(allowList(ch, f, function (name, next) {
          var values = {}; values[name] = next;
          return putChannel(ch.name, { values: values }).then(function (r) { onUpdate(r.channel); return r; });
        }));
        return;
      }
      if (f.kind === "bool") {
        var cb = h("input", { class: "switch", type: "checkbox", role: "switch", id: id, name: f.name });
        cb.checked = !!f.value;
        inputs[f.name] = { get: function () { return cb.checked; }, initial: !!f.value };
        bucketFor(f.name).appendChild(h("div", { class: "field" },
          h("label", { class: "check", for: id }, cb, h("span", { text: f.label })),
          h("span", { class: "hint", text: f.hint }), src(f)));
        return;
      }
      if (f.kind === "secret") {
        var sec = A.secretInput(f.name, f.set ? "Değiştirmek için yeni değer girin" : "Yapıştırın (yalnız yazılır)");
        sec.input.id = id;
        secrets[f.name] = sec.input;
        var gen = null;
        if (f.generate) {
          gen = A.button("Üret", { icon: "key", aria: f.label + " üret" });
          gen.addEventListener("click", function () {
            var go = function () {
              KUI.busy(gen, KUI.api("/channels/" + ch.name + "/secret", { method: "POST", body: { name: f.name } }).then(function (r) {
                A.showOnce("Yeni doğrulama kodu", r.value, "Bunu Meta'daki 'Verify token' alanına yapıştırın. Bir daha gösterilmez.");
                return KUI.api("/channels").then(function (d) { d.channels.forEach(function (c) { if (c.name === ch.name) onUpdate(c); }); });
              }).catch(KUI.errorToast));
            };
            if (!f.set) return go();
            KUI.confirm({ title: "Yeni kod üretilsin mi?", body: "Eski kod geçersiz olur; Meta'daki 'Verify token' alanını yenilemeniz gerekir.",
              confirmText: "Üret", cancelText: "Vazgeç", danger: true }).then(function (ok) { if (ok) go(); });
          });
        }
        bucketFor(f.name).appendChild(h("div", { class: "field" }, h("label", { for: id, text: f.label }), sec.group,
          gen ? h("div", { class: "cluster" }, gen) : null,
          src(f),
          h("span", { class: "hint", text: f.hint })));
        return;
      }
      var num = f.kind === "int";
      var input = h("input", { class: "input", name: f.name, type: num ? "number" : "text", autocomplete: "off", spellcheck: "false",
        inputmode: num ? "numeric" : null });
      var cur = f.value === null || f.value === undefined ? "" : String(f.value);
      input.value = cur;
      inputs[f.name] = { get: function () { return num ? (input.value === "" ? null : Number(input.value)) : input.value.trim(); }, initial: num ? (cur === "" ? null : Number(cur)) : cur.trim() };
      bucketFor(f.name).appendChild(A.field(id, f.label, input, f.hint));
      var sl = src(f);
      if (sl) bucketFor(f.name).appendChild(sl);
    });

    var save = A.button("Kaydet", { kind: "primary", cls: "btn-block" });
    var test = A.button("Test et", { icon: "check", cls: "btn-block" });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var values = {}, sec = {};
      Object.keys(inputs).forEach(function (n) {
        var now = inputs[n].get();
        if (now !== inputs[n].initial) values[n] = now;
      });
      Object.keys(secrets).forEach(function (n) { if (secrets[n].value.trim()) sec[n] = secrets[n].value.trim(); });
      if (!Object.keys(values).length && !Object.keys(sec).length) { KUI.toast("Değişiklik yok.", { kind: "info" }); return; }
      KUI.busy(save, putChannel(ch.name, { values: values, secrets: sec }).then(function (r) {
        KUI.toast(A.applyNote(r.applied), { kind: "success" });
        onUpdate(r.channel);
      }).catch(function (err) { A.errorsFrom(err, form); }));
    });
    save.addEventListener("click", function () { form.dispatchEvent(new Event("submit", { cancelable: true })); });
    test.addEventListener("click", function () {
      KUI.clear(result);
      KUI.busy(test, KUI.api("/channels/" + ch.name + "/test", { method: "POST", body: {} }).then(function (r) {
        result.appendChild(A.callout("success", r.message));
        onUpdate(r.channel, true);
      }).catch(function (e) {
        result.appendChild(A.callout("danger", e.message));
        KUI.api("/channels").then(function (d) { d.channels.forEach(function (c) { if (c.name === ch.name) drawStatus(c); }); }).catch(function () {});
      }));
    });

    drawStatus(ch);
    var head = h("section", { class: "card channel-hero", "aria-label": ch.title },
      h("div", { class: "tile-top" },
        h("span", { class: "channel-mark" }, A.icon(MARKS[ch.name] || "plug")),
        h("div", { class: "grow" }, h("h2", { text: ch.title }), h("p", { class: "card-sub", text: TAGLINES[ch.name] })),
        h("div", { class: "channel-switch" }, statusSlot, enableSwitch(ch, onUpdate))),
      metaSlot,
      h("div", { class: "cluster" }, A.button("Kurulum rehberi", { icon: "info", onclick: function () { A.help.open("channel-" + ch.name); } })));

    var actions = h("div", { class: "channel-actions" }, save, test);
    var no = 0;
    form.appendChild(section(++no, "Bağlantı adresi", "Sağlayıcının panelinde bu adresi kullanın.", urls));
    buckets.forEach(function (b) { form.appendChild(section(++no, b.key, b.sub, b.body)); });
    if (people.childNodes.length) form.appendChild(section(++no, "İzinli kişiler", "Yalnız bu listedekiler bota yazabilir.", people));
    form.appendChild(section(++no, "Sistem istemi", "Bu kanaldaki her konuşmada, genel istemden sonra eklenir.",
      A.promptCard(PROMPT_NAME[ch.name], { title: "", sub: null, placeholder: PROMPT_HINT[ch.name] })));
    form.appendChild(h("div", { class: "channel-savebar" }, h("div", { class: "grow" }, result), actions));
    var cols = form;

    var node = h("div", { class: "channel-detail stack", id: "kanal-" + ch.name },
      opts.embedded ? null : h("div", null, h("a", { class: "btn btn-ghost btn-sm", href: "#/kanallar" }, A.icon("chev-left"), "Kanallar")),
      head, cols);
    node._redraw = drawStatus;
    return node;
  }

  function renderDetail(main, name) {
    var host = h("div", { class: "stack" }, A.skeleton(3));
    main.appendChild(host);
    var node = null;
    function show(c, soft) {
      if (soft && node && node._redraw) { node._redraw(c); return; }
      var titleNode = KUI.$("[data-page-title]");
      if (titleNode) titleNode.textContent = c.title;
      document.title = c.title + " · " + (A.me ? A.me.title : "km-ai");
      var fresh = detail(c, show);
      var guideOpen = node && node.querySelector("details.guide") && node.querySelector("details.guide").open;
      if (guideOpen) fresh.querySelector("details.guide").open = true;
      KUI.clear(host);
      host.appendChild(fresh);
      node = fresh;
    }
    function load() {
      return KUI.api("/channels").then(function (d) {
        var c = d.channels.filter(function (x) { return x.name === name; })[0];
        if (!c) { location.hash = "#/kanallar"; return; }
        show(c);
      }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
    }
    // onUpdate(null) = "reload the page data"
    var origShow = show;
    show = function (c, soft) { if (!c) return load(); return origShow(c, soft); };
    load();
    // status line only (never the forms: typed values must survive)
    var stop = KUI.poll(function () {
      return KUI.api("/channels").then(function (d) {
        d.channels.forEach(function (c) { if (c.name === name && node && node._redraw) node._redraw(c); });
      });
    }, 10000);
    A.onLeave(stop);
  }

  function render(main, route) {
    var name = route && route.sub && route.sub[0];
    if (name && GUIDES[name]) return renderDetail(main, name);
    renderOverview(main);
  }

  A.channelDetail = detail;   // the setup wizard embeds the same form
  A.pages.channels = { title: "Kanallar", render: render };
})();
