/* Mini yardımcılar: "Nasıl yapılır?" düğmesi bir rehber penceresi (modal) açar.
   Her konu: başlık, kısa giriş, numaralı adımlar (kopyalanabilir değerler, bağlantılar), notlar.
   Metin yalnız textContent ile eklenir; bağlantılar yalnız https. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  // provider id -> [where to create the key, extra note]
  var KEYS = {
    openrouter: ["https://openrouter.ai/keys", "Tek anahtarla yüzlerce modele erişirsiniz. Bazı modeller ücretsizdir; ücretli olanlar için hesaba bakiye yüklenir."],
    openai: ["https://platform.openai.com/api-keys", "API kullanımı ChatGPT aboneliğinden ayrıdır; Billing bölümünden bakiye yüklemeniz gerekir."],
    anthropic: ["https://console.anthropic.com/settings/keys", "API kullanımı Claude aboneliğinden ayrıdır; Plans & Billing bölümünden kredi yüklemeniz gerekir."],
    gemini: ["https://aistudio.google.com/apikey", "Google AI Studio'da ücretsiz bir kota vardır; kota ve modeller zamanla değişir."],
    xai: ["https://console.x.ai", "Console > API Keys. Kullanım için hesaba kredi gerekir."],
    groq: ["https://console.groq.com/keys", "Ücretsiz bir kullanım katmanı vardır; hız sınırları sağlayıcı sayfasında yazar."],
    deepseek: ["https://platform.deepseek.com/api_keys", "Kullanım için hesaba bakiye yüklenir."],
    mistral: ["https://console.mistral.ai/api-keys", "Ücretsiz deneme katmanı vardır; sınırlar sağlayıcı sayfasında yazar."],
    together: ["https://api.together.ai/settings/api-keys", "Hesabınıza başlangıç kredisi verilebilir; güncel durum sağlayıcı sayfasında."],
    perplexity: ["https://www.perplexity.ai/settings/api", "API kullanımı için kredi yüklenir. Bu sağlayıcı model listesi sunmaz; model adını elle yazın."]
  };

  function link(url, label) {
    if (!/^https:\/\//.test(url)) return h("span", { text: label || url });
    return h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: label || url });
  }
  function code(text) { return h("code", { text: text }); }

  function copyChip(text) {
    var b = h("button", { class: "help-copy", type: "button", "aria-label": text + " kopyala" }, code(text), A.icon("copy"));
    b.addEventListener("click", function () {
      A.copyText(text).then(function (ok) { KUI.toast(ok ? "Kopyalandı" : "Kopyalanamadı; elle seçin", { kind: ok ? "success" : "danger" }); });
    });
    return b;
  }

  // ------------------------------------------------------------ topics (functions so they can use context)
  var TOPICS = {
    "telegram-token": function () {
      return { icon: "send", title: "Telegram bot token'ı nasıl alınır?", time: "≈ 2 dakika",
        intro: "Bot, Telegram'da kendi hesabıdır. Hesabı Telegram'ın resmi botu BotFather açar ve size bir token verir.",
        steps: [
          [link("https://t.me/BotFather", "@BotFather"), " sohbetini Telegram'da açın (mavi tikli resmi hesap)."],
          ["Mesaj kutusuna ", copyChip("/newbot"), " yazıp gönderin."],
          ["Botunuza bir ", h("strong", { text: "ad" }), " verin (görünen isim, istediğiniz gibi)."],
          ["Bir ", h("strong", { text: "kullanıcı adı" }), " seçin; ", code("bot"), " ile bitmeli, örn. ", code("kmt_asistan_bot"), "."],
          ["BotFather'ın yanıtındaki ", code("123456789:AA…"), " biçimindeki uzun metin token'ınızdır. Kopyalayın."],
          ["Bu sayfada token'ı alana yapıştırıp ", h("strong", { text: "Kaydet ve bağlan" }), " düğmesine basın."]
        ],
        notes: ["Token bir şifre gibidir: kimseyle paylaşmayın, bir yere yapıştırıp bırakmayın.",
                "Sızdırdıysanız BotFather'da /revoke ile yenileyin; sonra buraya yeni token'ı girin."] };
    },
    "telegram-chat": function () {
      return { icon: "message", title: "Sohbet kimliği nasıl bulunur?", time: "≈ 1 dakika",
        intro: "Bot yalnız izin listesindeki sohbetlere yanıt verir. Kimliği elle aramanız gerekmez: bota yazın, burada belirir.",
        steps: [
          ["Telegram'da botunuzu kullanıcı adıyla arayıp açın ve ", h("strong", { text: "Başlat" }), " düğmesine basın."],
          ["Sohbete ", copyChip("/id"), " yazıp gönderin."],
          ["Bu sayfada ", h("strong", { text: "Bota yazan sohbetler" }), " altında sohbet birkaç saniye içinde görünür."],
          [h("strong", { text: "İzin ver" }), " düğmesine basın. Liste hemen geçerli olur."]
        ],
        notes: ["Grup için botu gruba ekleyip grupta /id yazın. Grup kimlikleri eksiyle başlar (örn. -100123…).",
                "Kimliği zaten biliyorsanız \"Elle ekle\" alanına yazabilirsiniz.",
                "Liste boşken hafıza, görev ve AI araçları kapalıdır; bunu güvenlik için bilerek böyle yaptık."] };
    },
    "panel-password": function () {
      return { icon: "lock", title: "Panel parolası", time: "≈ 1 dakika",
        intro: "Panel ayarları, anahtarları ve botu yönetir; yalnız siz girebilmelisiniz.",
        steps: [
          ["İlk açılışta servis günlüğündeki ", code("Kurulum kodu"), " ile parolayı belirlersiniz (Komuta konsolu > servis > Loglar)."],
          ["Parola en az 10 karakter olmalı; yaygın parolalar kabul edilmez."],
          ["Parolayı sonradan Ayarlar > Güvenlik'ten değiştirebilirsiniz; değişince diğer oturumlar kapanır."]
        ],
        notes: ["Komuta girişiyle koruma kullanıyorsanız ayrıca parola gerekmez.", "UI_PASSWORD ortam değişkeni verirseniz parola yalnız orada değişir."] };
    },
    "llm-key": function (ctx) {
      var p = (ctx && ctx.provider) || {}, k = KEYS[p.id];
      if (p.local) {
        return { icon: "spark", title: (p.name || "Yerel model") + " nasıl bağlanır?", time: "≈ 3 dakika",
          intro: "Yerel sunucular anahtar istemez; model kendi bilgisayarınızda ya da sunucunuzda çalışır.",
          steps: [
            ["Sunucuyu başlatın ve bir model indirin (Ollama: ", code("ollama pull llama3.1"), ")."],
            ["Adresin bu servisten erişilebilir olduğundan emin olun. Docker içinde ", code("localhost"), " yerine ", code("host.docker.internal"), " kullanın."],
            ["Seçici kartından yerel sağlayıcıyı seçin; adres kendiliğinden gelir. Gerekirse düzeltin."],
            ["Model listesi otomatik gelir; modeli seçip ", h("strong", { text: "Kaydet ve test et" }), " düğmesine basın."]
          ],
          notes: ["Yerel adresler için gerekirse \"yerel/özel ağda\" kutusunu işaretleyin (yalnız Özel sağlayıcıda).",
                  "Küçük yerel modeller araç çağırmayı hep desteklemez; listede \"araç\" rozetli olanları seçin."] };
      }
      var steps = k ? [
        [link(k[0], "Anahtar sayfasını açın"), " ve hesabınızla giriş yapın."],
        ["Yeni bir API anahtarı oluşturun (\"Create key\" benzeri bir düğme)."],
        ["Anahtarı hemen kopyalayın; çoğu sağlayıcı bir daha göstermez."],
        ["Bu sayfada anahtar alanına yapıştırın. Model listesi kendiliğinden gelir."],
        ["Bir model seçip ", h("strong", { text: "Kaydet ve test et" }), " düğmesine basın."]
      ] : [
        ["Sağlayıcınızın panelinden bir API anahtarı oluşturun."],
        ["Adres alanına OpenAI uyumlu ", code("/v1"), " adresini yazın."],
        ["Anahtarı yapıştırın, modeli seçin veya yazın, ", h("strong", { text: "Kaydet ve test et" }), " düğmesine basın."]
      ];
      return { icon: "key", title: (p.name || "Sağlayıcı") + " anahtarı nasıl alınır?", time: "≈ 2 dakika",
        intro: "Anahtar, bu botun yapay zekâ sağlayıcısına kimliğidir. Yalnız sunucuda saklanır; panele bir daha gönderilmez.",
        steps: steps,
        notes: [k ? k[1] : (p.hint || "Sağlayıcınızın belgelerine bakın."), "Anahtarı kimseyle paylaşmayın; sızarsa sağlayıcı panelinden silip yenisini oluşturun."] };
    },
    "search-auto": function () {
      return { icon: "search", title: "Otomatik mod nasıl çalışır?", time: "≈ 1 dakika",
        intro: "Anahtar girmeden çalışır: ücretsiz kanalları sırayla dener, hata vereni atlar ve bir süre hatırlar.",
        steps: [
          ["Kendi SearXNG adresiniz varsa önce o denenir."],
          ["Sonra anahtarını girdiğiniz sağlayıcılar: Tavily, Brave, Serper."],
          ["Sonra anahtarsız Parallel, DuckDuckGo ve herkese açık SearXNG sunucuları."],
          ["Hepsi başarısız olursa net bir hata görürsünüz."]
        ],
        notes: ["Sorgular üçüncü taraf servislere gidebilir. Gizlilik için kendi Tavily/Brave/Serper anahtarınızı ya da kendi SearXNG sunucunuzu seçin.",
                "En güvenilir sonuç için bir anahtar eklemenizi öneririz."] };
    },
    "search-parallel": function () {
      return { icon: "globe", title: "Parallel (anahtarsız) nedir?", time: "≈ 1 dakika",
        intro: "Parallel'in barındırılan arama servisi (MCP). Hesap, anahtar ve kart gerekmez; gerçek web sonuçları ve alıntılar döner.",
        steps: [["Kartlardan ", h("strong", { text: "Parallel" }), "'i seçin."], [h("strong", { text: "Kaydet ve dene" }), " düğmesine basın; birkaç sonuç görmelisiniz."]],
        notes: ["Anonim kullanımın hız sınırı düşüktür ve sınır değerini sağlayıcı yayımlamaz. Çok sorgu yapıyorsanız bir anahtar sağlayıcı ekleyin.",
                "Sorgularınız Parallel'e gider; gizlilik ve kullanım koşulları için parallel.ai'ye bakın."] };
    },
    "search-tavily": function () {
      return { icon: "key", title: "Tavily anahtarı nasıl alınır?", time: "≈ 2 dakika",
        intro: "Yapay zekâ ajanları için tasarlanmış bir arama API'si; temiz özetli sonuçlar döner.",
        steps: [[link("https://tavily.com", "tavily.com"), "'a gidip hesap açın."], ["Dashboard > API Keys bölümünden anahtarınızı kopyalayın (", code("tvly-…"), " ile başlar)."], ["Bu sayfada anahtar alanına yapıştırıp ", h("strong", { text: "Kaydet ve dene" }), " düğmesine basın."]],
        notes: ["Ücretsiz kota ve koşullar zamanla değişir; güncel durum için sağlayıcının fiyat sayfasına bakın."] };
    },
    "search-brave": function () {
      return { icon: "key", title: "Brave Search anahtarı nasıl alınır?", time: "≈ 3 dakika",
        intro: "Brave'in bağımsız arama dizinine API erişimi; hızlı ve yüksek kalite.",
        steps: [[link("https://brave.com/search/api", "brave.com/search/api"), " sayfasından başlayın ve hesap açın."], ["Bir plan seçin (aylık ücretsiz kredi sunulabilir) ve API anahtarı oluşturun."], ["Anahtarı bu sayfadaki alana yapıştırıp ", h("strong", { text: "Kaydet ve dene" }), " düğmesine basın."]],
        notes: ["Brave kayıt sırasında kart isteyebilir ve sonuçları gösterirken atıf şartı koyabilir. Koşullar değişir; güncel hâli sağlayıcı sayfasında."] };
    },
    "search-serper": function () {
      return { icon: "key", title: "Serper anahtarı nasıl alınır?", time: "≈ 2 dakika",
        intro: "Google arama sonuçlarını JSON olarak veren bir servis.",
        steps: [[link("https://serper.dev", "serper.dev"), "'e gidip hesap açın (Google ile girilebilir)."], ["Kayıt sonrası ücretsiz sorgu hakkınız hesabınıza tanımlanır; Dashboard'da API key'i bulun."], ["Anahtarı bu sayfadaki alana yapıştırıp ", h("strong", { text: "Kaydet ve dene" }), " düğmesine basın."]],
        notes: ["Ücretsiz sorgu hakkı tek seferliktir; bitince ücretli paket gerekir. Güncel koşullar sağlayıcının sayfasında."] };
    },
    "search-searxng": function () {
      return { icon: "globe", title: "Kendi SearXNG sunucunuzu bağlama", time: "≈ 5 dakika",
        intro: "SearXNG birçok arama motorunu tek yerde toplayan açık kaynak bir meta arama motorudur. Kendi sunucunuzda çalıştırırsanız bot koruması sizi engellemez.",
        steps: [
          ["SearXNG'yi kendi sunucunuzda kurun (resmi belgeler: ", link("https://docs.searxng.org", "docs.searxng.org"), ")."],
          ["settings.yml içinde ", code("search.formats"), " listesine ", code("json"), " ekleyin."],
          ["Yalnız botunuzun eriştiği özel bir sunucuysa ", code("server.limiter: false"), " yapın."],
          ["Sunucuyu yeniden başlatın ve tarayıcıda ", code("https://adresiniz/search?q=test&format=json"), " açıp JSON gelip gelmediğine bakın."],
          ["Adresi bu sayfadaki alana yazıp ", h("strong", { text: "Kaydet ve dene" }), " düğmesine basın."]
        ],
        notes: ["Herkese açık SearXNG sunucularının çoğu botları engeller (429/403); bu yüzden kendi sunucunuz önerilir.", "Özel ağdaki (10.x, 192.168.x) adresler desteklenir; bulut metadata ve link-local adresler her zaman engelli."] };
    }
  };

  // ------------------------------------------------------------ the dialog
  var dialog = null, lastFocus = null;
  function close() { if (dialog && dialog.open) dialog.close(); }

  function open(topic, ctx) {
    var make = TOPICS[topic];
    if (!make) return;
    var d = make(ctx || {});
    if (!dialog) {
      dialog = h("dialog", { class: "modal help-modal", "aria-labelledby": "help-title" });
      dialog.addEventListener("click", function (e) { if (e.target === dialog) close(); });
      dialog.addEventListener("close", function () { if (lastFocus && lastFocus.focus) lastFocus.focus(); });
      document.body.appendChild(dialog);
    }
    lastFocus = document.activeElement;
    KUI.clear(dialog);
    var closeBtn = h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", "aria-label": "Kapat", onclick: close }, A.icon("x"));
    var ok = h("button", { class: "btn btn-primary", type: "button", onclick: close }, "Anladım");
    dialog.appendChild(h("div", { class: "help-card" },
      h("header", { class: "help-head" },
        h("span", { class: "help-badge" }, A.icon(d.icon || "info")),
        h("div", { class: "grow" },
          h("h2", { id: "help-title", text: d.title }),
          d.time ? h("span", { class: "help-time", text: d.time }) : null),
        closeBtn),
      h("div", { class: "help-body" },
        d.intro ? h("p", { class: "help-intro", text: d.intro }) : null,
        h("ol", { class: "help-steps" }, d.steps.map(function (s) { return h("li", null, h("div", { class: "help-step" }, s)); })),
        d.notes && d.notes.length ? h("div", { class: "help-notes" }, A.icon("info"), h("ul", null, d.notes.map(function (n) { return h("li", { text: n }); }))) : null),
      h("footer", { class: "help-foot" }, ok)));
    dialog.showModal();
    ok.focus();
  }

  /** Small "Nasıl yapılır?" button for next to a label. */
  function helpButton(topic, ctx, label) {
    var b = h("button", { class: "help-trigger", type: "button", "aria-haspopup": "dialog" }, A.icon("info"), h("span", { text: label || "Nasıl yapılır?" }));
    b.addEventListener("click", function () { open(topic, typeof ctx === "function" ? ctx() : ctx); });
    return b;
  }

  /** Label row with the help button on the right. */
  function labelWithHelp(forId, text, topic, ctx) {
    return h("div", { class: "label-row" }, h("label", { for: forId, text: text }), helpButton(topic, ctx));
  }

  A.help = { open: open, close: close, topics: TOPICS };
  A.helpButton = helpButton;
  A.labelWithHelp = labelWithHelp;
})();
