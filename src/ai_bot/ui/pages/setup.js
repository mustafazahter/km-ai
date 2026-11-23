/* Kurulum sihirbazı — tam ekran, tek kart, adım adım (n8n tarzı ilk kurulum):
   panel parolası → kanal seçimi → seçilen kanalın kurulumu (Telegram: token + sohbet; diğerleri: kanal formu)
   → yapay zekâ → arama → bitti. Adım listesi sunucudan gelir (seçime göre değişir).
   Kural: ortam değişkeninden gelen değer dolu görünür ama GÜVENİLMEZ; adım ancak doğrulanınca
   (test / onay) tamam olur. Her adım kendi işini yapar, başarılınca bir sonrakine geçer.
   Text is always inserted with textContent (KUI.el); no innerHTML, no inline script/style (CSP). */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;
  var ORDER = ["panel", "channel", "telegram", "chats", "llm", "search", "summary"];  // replaced by the server's list on every load
  function syncOrder(state) { ORDER = state.steps.map(function (s) { return s.id; }); }
  var BASE = "#/hos-geldin/";  // first-run wizard; the "Kurulum" page itself is a checklist
  var SEARCH_INFO = {
    auto: { name: "Otomatik mod", sub: "Ücretsiz kanallar sırayla · önerilen" },
    parallel: { name: "Parallel", sub: "Anahtarsız · gerçek web sonuçları" },
    duckduckgo: { name: "DuckDuckGo", sub: "Anahtarsız · en iyi çaba" },
    tavily: { name: "Tavily", sub: "Anahtarlı · ücretsiz kota" },
    brave: { name: "Brave Search", sub: "Anahtarlı · hızlı" },
    serper: { name: "Serper (Google)", sub: "Anahtarlı · 2.500 ücretsiz sorgu" },
    searxng: { name: "SearXNG", sub: "Kendi sunucun" }
  };

  var CHANNEL_INFO = {
    telegram: ["Telegram", "Bot token'ı · en kolayı, ≈ 3 dk", "send"],
    whatsapp: ["WhatsApp", "Meta Cloud API · ≈ 15 dk", "message"],
    teams: ["Microsoft Teams", "Azure Bot · ≈ 20 dk", "users"],
    slack: ["Slack", "Slack uygulaması · ≈ 10 dk", "hash"],
    email: ["E-posta", "IMAP ve SMTP · ≈ 5 dk", "mail"],
    none: ["Şimdilik yok", "Yalnız panel sohbeti", "home"]
  };

  var lastIdx = -1, lastDone = null;  // wizard memory between redraws: travel direction, steps that just became done
  function go(step) { location.hash = BASE + step; }
  function stepOf(state, id) { return state.steps.filter(function (s) { return s.id === id; })[0]; }
  function fieldOf(step, name) { return (step.fields || []).filter(function (f) { return f.name === name; })[0] || null; }
  function firstOpen(state) {
    for (var i = 0; i < state.steps.length - 1; i++) if (!state.steps[i].done) return state.steps[i].id;
    return "summary";
  }

  // ------------------------------------------------------------ small blocks
  /** Value found in the environment: show it masked, ask to verify. One primary action. */
  function found(title, detail, verifyLabel, onVerify, onChange) {
    var result = h("div");
    var verify = A.button(verifyLabel, { kind: "primary", icon: "check" });
    verify.addEventListener("click", function () {
      KUI.busy(verify, onVerify().then(function (msg) {
        KUI.clear(result); result.appendChild(A.callout("success", msg));
      }).catch(function (e) { KUI.clear(result); result.appendChild(A.callout("danger", e.message)); }));
    });
    var change = A.button("Değiştir", { kind: "ghost", onclick: onChange });
    return h("div", { class: "stack" },
      h("div", { class: "found" }, h("span", { class: "found-icon" }, A.icon("info")),
        h("div", { class: "grow" }, h("div", { class: "ink small", text: title }), h("div", { class: "small muted break", text: detail }))),
      h("div", { class: "cluster" }, verify, change), result);
  }

  function okBlock(text, changeLabel, onChange) {
    return h("div", { class: "stack" }, A.callout("success", text),
      changeLabel ? h("div", { class: "cluster" }, A.button(changeLabel, { kind: "ghost", onclick: onChange })) : null);
  }

  // ------------------------------------------------------------ steps
  // Each step: function (ctx) -> { title, text, body: Node, optional?: bool, skip?: {label, run} }
  var STEPS = {};

  STEPS.panel = function (ctx) {
    var step = stepOf(ctx.state, "panel");
    var auth = ctx.state.auth || {};
    var body = [];
    if (!auth.protection) {
      var on = A.button("Korumayı aç", { kind: "primary", icon: "shield", onclick: function () {
        KUI.busy(on, KUI.api("/auth/protection", { method: "POST", body: { enabled: true } }).then(function () {
          KUI.toast("Panel koruması açıldı.", { kind: "success" }); location.reload();
        }).catch(KUI.errorToast));
      } });
      body.push(A.callout("danger", "Panel koruması KAPALI: bu adresi bilen herkes paneli açabilir."), h("div", { class: "cluster" }, on));
    } else if (auth.mode === "komuta") {
      body.push(A.callout("success", "Panel Komuta girişiyle korunuyor. Ayrıca parola gerekmez."));
    } else if (auth.password === "env") {
      body.push(A.callout("success", "Parola UI_PASSWORD ortam değişkeninden geliyor."));
    } else if (auth.password === "stored") {
      body.push(A.callout("success", "Parolanız belirlendi. Panel her açılışta bu parolayı ister."),
        h("div", { class: "cluster" }, h("a", { class: "btn btn-ghost", href: "#/ayarlar/koruma" }, "Parolayı değiştir")));
    } else {
      var pw = h("input", { class: "input", type: "password", id: "pw-new", autocomplete: "new-password", minlength: "10" });
      var pw2 = h("input", { class: "input", type: "password", id: "pw-new2", autocomplete: "new-password" });
      var save = A.button("Parolayı belirle", { kind: "primary", trail: "arrow-right", onclick: function () {
        if (pw.value !== pw2.value) { KUI.toast("Parolalar aynı değil.", { kind: "danger" }); return; }
        KUI.busy(save, KUI.api("/auth/password", { method: "POST", body: { "new": pw.value } }).then(function () {
          KUI.toast("Parola belirlendi.", { kind: "success" }); return ctx.done();
        }).catch(KUI.errorToast));
      } });
      body.push(A.field("pw-new", "Yeni parola", pw, "En az 10 karakter; yaygın parolalar kabul edilmez."),
        A.field("pw-new2", "Parola tekrar", pw2), h("div", { class: "cluster" }, save));
    }
    body.unshift(h("div", { class: "help-line" }, h("span", { class: "muted small", text: "Parola ve kurulum kodu hakkında." }), A.helpButton("panel-password")));
    return { title: "Panel parolası", text: "Bu panel ayarları, anahtarları ve botu yönetir; yalnız siz girebilmelisiniz.", body: h("div", { class: "stack" }, body) };
  };

  STEPS.channel = function (ctx) {
    var step = stepOf(ctx.state, "channel");
    var chosen = step.choice;
    var result = h("div");
    var tiles = [];
    function mark(id) { tiles.forEach(function (t) { t.setAttribute("aria-pressed", t.getAttribute("data-channel") === id ? "true" : "false"); }); }
    (step.choices || []).forEach(function (c) {
      var info = CHANNEL_INFO[c.id] || [c.title, "", "plug"];
      var t = h("button", { type: "button", class: "tile tile-channel", "data-channel": c.id, "aria-pressed": c.id === chosen ? "true" : "false" },
        h("span", { class: "tile-name" }, A.icon(info[2]), info[0]), h("small", { text: info[1] }));
      t.addEventListener("click", function () {
        KUI.busy(t, KUI.api("/setup/channel", { method: "POST", body: { channel: c.id } }).then(function () {
          mark(c.id);
          return ctx.refresh().then(function () { ctx.advanceSoon(); });
        }).catch(function (e) { KUI.clear(result); result.appendChild(A.callout("danger", e.message)); }));
      });
      tiles.push(t);
    });
    var body = h("div", { class: "stack" },
      h("div", { class: "tiles tiles-channel", role: "group", "aria-label": "Kanal" }, tiles),
      h("p", { class: "small muted", text: "Seçimden sonra o kanalın kurulumu açılır. Diğer kanalları istediğiniz zaman Kanallar sayfasından ekleyebilirsiniz." }),
      result);
    return { title: "Hangi kanaldan konuşacaksınız?",
      text: "Bot Telegram, WhatsApp, Microsoft Teams, Slack veya e-postadan yazışabilir; panelde Sohbet sayfasıyla ise kanal olmadan da konuşursunuz. Birini seçin, kurulumuna birlikte devam edelim.",
      body: body };
  };

  STEPS.channel_setup = function (ctx) {
    var step = stepOf(ctx.state, "channel_setup");
    var name = step.channel;
    var host = h("div", { class: "stack" }, A.skeleton(3));
    var node = null;
    function show(c, soft) {
      if (!c) return load();
      if (soft && node && node._redraw) { node._redraw(c); ctx.refresh(); return; }
      var fresh = A.channelDetail(c, show, { embedded: true });
      KUI.clear(host); host.appendChild(fresh); node = fresh;
      ctx.refresh();   // the step turns done once the channel is on, configured and has someone allowed
    }
    function load() {
      return KUI.api("/channels").then(function (d) {
        var c = d.channels.filter(function (x) { return x.name === name; })[0];
        if (!c) { KUI.clear(host); host.appendChild(A.callout("danger", "Bu kanal bu kurulumda yok.")); return; }
        show(c);
      }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
    }
    load();
    return {
      title: step.title,
      text: "Rehberi izleyin, ayarları kaydedin ve kanalın anahtarını açın. Kanal hazır olunca Devam açılır; Test et bağlantıyı doğrular.",
      body: host,
      skip: step.done ? null : { label: "Şimdilik atla (yalnız panel sohbeti)", run: function () {
        return KUI.api("/setup/channel", { method: "POST", body: { channel: "none" } });
      } }
    };
  };

  STEPS.telegram = function (ctx) {
    var step = stepOf(ctx.state, "telegram");
    var field = fieldOf(step, "TELEGRAM_BOT_TOKEN");
    var box = h("div");
    function form() {
      KUI.clear(box);
      box.appendChild(h("div", { class: "stack" },
        A.telegramToken(field, { onSaved: function () { return ctx.done(); } })));
    }
    if (step.state === "done") {
      box.appendChild(okBlock(step.detail + " olarak bağlandı.", "Token'ı değiştir", form));
    } else if (step.state === "ready" && field && field.source === "env") {
      box.appendChild(found("TELEGRAM_BOT_TOKEN ortam değişkeninde bulundu", "Değer " + (field.preview || "…") + " ile bitiyor. Doğrulanmadan kullanılmaz.",
        "Doğrula ve bağlan", function () {
          return KUI.api("/telegram/test", { method: "POST", body: {} }).then(function (r) {
            ctx.done(); return "@" + (r.bot.username || "?") + " doğrulandı.";
          });
        }, form));
    } else {
      form();
    }
    return { title: "Telegram botunu bağlayın", text: "Bot, Telegram'daki hesabınız. Token yalnız sunucuda saklanır; bu sayfaya bir daha gönderilmez.", body: box };
  };

  STEPS.chats = function (ctx) {
    var step = stepOf(ctx.state, "chats");
    var body = h("div", { class: "stack" });
    body.appendChild(h("div", { class: "help-line" }, h("span", { class: "muted small", text: "Kimliği bota yazarak bulursunuz." }), A.helpButton("telegram-chat")));
    if (step.state === "ready") {
      var confirm = A.button("Bu listeyi onayla", { kind: "primary", icon: "check" });
      confirm.addEventListener("click", function () {
        KUI.busy(confirm, KUI.api("/setup/confirm", { method: "POST", body: { step: "chats", fp: step.fp } }).then(function () { return ctx.done(); }).catch(KUI.errorToast));
      });
      body.appendChild(h("div", { class: "found" }, h("span", { class: "found-icon" }, A.icon("info")),
        h("div", { class: "grow" }, h("div", { class: "ink small", text: "ALLOWED_CHAT_IDS ortamdan geldi" }),
          h("div", { class: "small muted", text: step.detail + ". Kimler botu kullanabilir, aşağıdan kontrol edin." }))));
      body.appendChild(h("div", { class: "cluster" }, confirm));
      body.appendChild(A.chatManager(function () { ctx.refresh(); }).node);
    } else {
      body.appendChild(A.chatManager(function () { ctx.refresh().then(function (s) { if (stepOf(s, "chats").done) ctx.advanceSoon(); }); }).node);
    }
    return { title: "Botu kimler kullanabilir?", text: "Bot yalnız izin listesindeki sohbetlere yanıt verir. Liste boşken hafıza, görev ve AI araçları kapalıdır.", body: body };
  };

  STEPS.llm = function (ctx) {
    var step = stepOf(ctx.state, "llm");
    ctx.state.llm_fields = step.fields;
    var picker = h("div");
    function form() {
      KUI.clear(picker);
      picker.appendChild(A.llmPicker(ctx.state, { onSaved: function () { return ctx.done(); } }));
    }
    var box = h("div");
    var provider = (ctx.state.llm_providers.filter(function (p) { return p.id === ctx.state.llm_provider; })[0] || {}).name || "Sağlayıcı";
    if (step.state === "done") {
      box.appendChild(okBlock(step.detail, "Sağlayıcıyı veya modeli değiştir", function () { KUI.clear(box); box.appendChild(picker); form(); }));
    } else if (step.state === "ready") {
      box.appendChild(found(provider + " · " + ctx.state.values.LLM_MODEL, "Ayarlar ortamdan geldi; anahtarla gerçek bir istek göndererek doğrulayın.",
        "Doğrula", function () {
          return KUI.api("/llm/test", { method: "POST", body: {} }).then(function (r) {
            ctx.done(); return r.model + " yanıt verdi (" + r.latency_ms + " ms).";
          });
        }, function () { KUI.clear(box); box.appendChild(picker); form(); }));
    } else {
      box.appendChild(picker); form();
    }
    return { title: "Yapay zekâ sağlayıcısını seçin", text: "Sağlayıcıyı seçin, anahtarı girin, modeli listeden seçin. Anthropic yerel adaptörle, diğerleri OpenAI uyumlu uçla çalışır.", body: box };
  };

  STEPS.search = function (ctx) {
    var step = stepOf(ctx.state, "search");
    var chosen = ctx.state.values.SEARCH_PROVIDER;
    var sec = A.secretInput("api_key", "Anahtar");
    var url = h("input", { class: "input", name: "searxng_url", value: ctx.state.values.SEARXNG_URL || "", placeholder: "https://searx.example.org", inputmode: "url", autocomplete: "off" });
    url.id = "searx-url"; sec.input.id = "search-key";
    var extra = h("div", { class: "stack" }), result = h("div"), tiles = [];
    function show(kind, text) { KUI.clear(result); result.appendChild(A.callout(kind, text)); }
    /** The provider refuses this server: say it plainly and move the choice to a keyed provider. */
    function failed(e, saved) {
      if (e && e.code === "search_blocked") {
        show("danger", e.message);
        var t = tiles.filter(function (x) { return x.getAttribute("data-provider") === "tavily"; })[0];
        if (t && chosen === "duckduckgo") t.click();
        return;
      }
      show("danger", (saved ? "Kaydedildi, ama deneme araması başarısız: " : "") + (e && e.message));
    }
    function drawExtra() {
      KUI.clear(extra);
      var topic = "search-" + chosen;
      if (A.help.topics[topic]) extra.appendChild(h("div", { class: "help-line" }, h("span", { class: "muted small", text: "Seçili sağlayıcı hakkında." }), A.helpButton(topic)));
      if (chosen === "auto") {
        extra.appendChild(A.callout("info", "Ücretsiz kanalları sırayla dener, hata vereni atlar ve bir süre hatırlar: anahtarınız varsa Tavily/Brave, sonra DuckDuckGo, sonra herkese açık SearXNG sunucuları (searx.space). Sorgular bu üçüncü taraf sunuculara gidebilir; gizlilik ve güvenilirlik için kendi Tavily veya Brave anahtarınızı seçin."));
      } else if (chosen === "parallel") {
        extra.appendChild(A.callout("info", "Parallel'in barındırılan arama servisi (MCP): hesap ve anahtar gerekmez, gerçek web sonuçları döner. Anonim kullanımın hız sınırı düşüktür ve sorgularınız Parallel'e gider (parallel.ai gizlilik ve kullanım koşulları geçerlidir)."));
      } else if (chosen === "duckduckgo") {
        extra.appendChild(A.callout("info", "Anahtarsız DuckDuckGo bulut sunucularında bot korumasına takılabilir. Güvenilir arama için Tavily, Brave veya kendi SearXNG'niz."));
      } else if (chosen === "searxng") {
        extra.appendChild(h("div", { class: "field" }, h("label", { for: "searx-url", text: "SearXNG adresi" }), url,
          h("span", { class: "hint", text: "JSON çıktısı açık olmalı (settings.yml: formats: [html, json])." })));
      } else {
        var name = { tavily: "TAVILY_API_KEY", brave: "BRAVE_API_KEY", serper: "SERPER_API_KEY" }[chosen];
        var f = fieldOf(step, name);
        extra.appendChild(h("div", { class: "field" },
          h("label", { for: "search-key", text: { tavily: "Tavily anahtarı", brave: "Brave anahtarı", serper: "Serper anahtarı" }[chosen] }), sec.group,
          f && f.set ? h("span", { class: "hint", text: "Kayıtlı anahtar var (" + (f.preview || "…") + "); yeni değer yazmazsanız o kullanılır." }) : null,
          h("span", { class: "hint", text: { tavily: "tavily.com > API keys", brave: "brave.com/search/api", serper: "serper.dev > API key (kart gerekmez)" }[chosen] })));
      }
    }
    var tileBox = h("div", { class: "tiles tiles-search", role: "group", "aria-label": "Arama sağlayıcısı" }, ctx.state.providers.map(function (p) {
      var t = h("button", { type: "button", class: "tile", "data-provider": p, "aria-pressed": p === chosen ? "true" : "false" }, SEARCH_INFO[p].name, h("small", { text: SEARCH_INFO[p].sub }));
      t.addEventListener("click", function () {
        chosen = p; tiles.forEach(function (x) { x.setAttribute("aria-pressed", x === t ? "true" : "false"); }); drawExtra();
      });
      tiles.push(t);
      return t;
    }));
    drawExtra();
    function body() {
      var b = { provider: chosen };
      if (chosen === "searxng") b.searxng_url = url.value.trim();
      if (sec.input.value.trim()) b.api_key = sec.input.value.trim();
      return b;
    }
    var save = A.button("Kaydet ve dene", { kind: "primary", trail: "arrow-right", onclick: function () {
      KUI.busy(save, KUI.api("/setup/search", { method: "POST", body: body() }).then(function () {
        sec.input.value = "";
        return KUI.api("/search/test", { method: "POST" }).then(function (r) {
          show("success", r.count + " sonuç · " + r.latency_ms + " ms (" + r.provider + (r.via ? " · " + r.via : "") + ")");
          return ctx.done();
        }, function (e) { failed(e, true); return ctx.refresh(); });
      }).catch(function (e) { show("danger", e.message); }));
    } });
    var box = h("div", { class: "stack" });
    if (step.state === "ready") {
      var okBtn = A.button("Doğrula", { kind: "primary", icon: "check" });
      okBtn.addEventListener("click", function () {
        KUI.busy(okBtn, KUI.api("/search/test", { method: "POST" }).then(function (r) {
          show("success", r.count + " sonuç · " + r.latency_ms + " ms (" + r.provider + (r.via ? " · " + r.via : "") + ")"); return ctx.done();
        }).catch(function (e) { failed(e, false); }));
      });
      box.appendChild(h("div", { class: "found" }, h("span", { class: "found-icon" }, A.icon("info")),
        h("div", { class: "grow" }, h("div", { class: "ink small", text: SEARCH_INFO[ctx.state.values.SEARCH_PROVIDER].name + " seçili" }),
          h("div", { class: "small muted", text: "Ortamdan veya varsayılan olarak geldi; deneme araması yapıp onaylayın ya da başka sağlayıcı seçin." }))));
      box.appendChild(h("div", { class: "cluster" }, okBtn));
    }
    box.appendChild(tileBox); box.appendChild(extra); box.appendChild(h("div", { class: "cluster" }, save)); box.appendChild(result);
    if (step.state === "done") box.insertBefore(A.callout("success", "Arama hazır: " + step.detail), box.firstChild);
    return {
      title: "Web aramayı ayarlayın", text: "Bot güncel bilgi için web'de arayabilir. İsterseniz bu adımı atlayıp sonra Ayarlar'dan değiştirin.", body: box,
      skip: step.done ? null : { label: "Atla (DuckDuckGo ile devam)", run: function () {
        return KUI.api("/setup/search", { method: "POST", body: { provider: "duckduckgo" } });
      } }
    };
  };

  STEPS.summary = function (ctx) {
    var state = ctx.state;
    var required = state.steps.slice(0, -1);
    var all = required.every(function (s) { return s.done; });
    var list = h("ul", { class: "list summary-list" }, required.map(function (s, i) {
      return h("li", { class: s.done ? "is-done" : null },
        h("span", { class: "num-badge" }, s.done ? A.icon("check") : String(i + 1)),
        h("div", { class: "grow" }, h("div", { class: "ink small", text: s.title }), h("div", { class: "small muted break", text: s.detail })),
        s.done ? null : h("a", { class: "btn btn-sm", href: BASE + s.id }, "Tamamla"));
    }));
    var finish = A.button(all ? "Kurulumu bitir" : "Önce eksik adımları tamamlayın", { kind: "primary", trail: "check", disabled: !all });
    finish.addEventListener("click", function () {
      KUI.busy(finish, KUI.api("/setup/complete", { method: "POST" }).then(function () {
        KUI.toast("Kurulum tamam. Bot çalışıyor; ayarlar anında uygulanır.", { kind: "success", timeout: 6000 });
        location.hash = "#/sohbet";
      }).catch(KUI.errorToast));
    });
    return {
      title: all ? "Her şey hazır" : "Neredeyse bitti", final: true,
      text: all ? (state.channel && state.channel !== "none"
        ? (CHANNEL_INFO[state.channel] || [state.channel])[0] + " üzerinden botunuza yazın ya da panelde Sohbet sayfasından modeli ve araçları deneyin."
        : "Panelde Sohbet sayfasından modeli ve araçları deneyin; kanalları Kanallar sayfasından ekleyebilirsiniz.") : "Aşağıdaki adımlar henüz doğrulanmadı.",
      body: h("div", { class: "stack" }, list, h("div", { class: "cluster" }, finish))
    };
  };

  // ------------------------------------------------------------ shell
  function progress(state, active, onGo) {
    var open = ORDER.indexOf(firstOpen(state));
    var prev = lastDone;
    lastDone = {}; state.steps.forEach(function (s) { lastDone[s.id] = s.done; });
    return h("ol", { class: "wz-steps", "aria-label": "Kurulum adımları" }, state.steps.map(function (s, i) {
      var cls = (s.done ? "is-done " : "") + (s.id === active ? "is-active " : "") + (s.done && prev && !prev[s.id] ? "is-new " : "");
      var reachable = s.done || i <= open || s.id === active;
      var b = h("button", { type: "button", "aria-current": s.id === active ? "step" : null, disabled: reachable ? null : true,
        title: s.title }, h("span", { class: "wz-dot" }, s.done ? A.icon("check") : String(i + 1)), h("span", { class: "wz-label", text: s.title }));
      b.addEventListener("click", function () { onGo(s.id); });
      return h("li", { class: cls }, b);
    }));
  }

  function langSwitch() {
    var box = h("div", { class: "lang-switch", role: "group", "aria-label": "Dil / Language" });
    ["tr", "en"].forEach(function (l) {
      var b = h("button", { type: "button", "aria-pressed": KUI.lang() === l ? "true" : "false", text: l.toUpperCase() });
      b.addEventListener("click", function () {
        if (KUI.lang() === l) return;
        KUI.api("/setup/lang", { method: "POST", body: { lang: l } }).catch(function () { /* ignore */ }).then(function () { KUI.setLang(l); });
      });
      box.appendChild(b);
    });
    return box;
  }

  function render(main, route) {
    if (!document.body.classList.contains("wizard-mode")) { lastIdx = -1; lastDone = null; }  // fresh visit: no stale direction
    document.body.classList.add("wizard-mode");
    var host = h("div", { class: "wz" }, A.skeleton(4));
    main.appendChild(host);
    var active = route.sub[0];
    var advanceTimer = null;
    function leaveHooks() {
      A.onLeave(function () { clearTimeout(advanceTimer); document.body.classList.remove("wizard-mode"); });
    }
    leaveHooks();

    function chooseLanguage() {
      function pick(l) {
        KUI.api("/setup/lang", { method: "POST", body: { lang: l } }).catch(function () { /* local choice still applies */ })
          .then(function () { KUI.setLang(l); });
      }
      var cards = [["tr", "Türkçe", "Arayüz Türkçe olsun"], ["en", "English", "Use the interface in English"]].map(function (c) {
        var b = h("button", { type: "button", class: "lang-card" }, h("strong", { text: c[1] }), h("span", { class: "muted small", text: c[2] }));
        b.addEventListener("click", function () { pick(c[0]); });
        return b;
      });
      KUI.clear(host);
      host.appendChild(h("section", { class: "card wz-card go-none lang-pick", "aria-labelledby": "wz-title" },
        h("span", { class: "lang-pick-mark" }, h("img", { src: "logo.svg", alt: "", width: "56", height: "56" })),
        h("h1", { id: "wz-title", text: "Dilinizi seçin · Choose your language" }),
        h("p", { class: "muted", text: "Daha sonra üst çubuktaki TR / EN düğmesiyle değiştirebilirsiniz. · You can change it later with the TR / EN switch." }),
        h("div", { class: "lang-cards" }, cards)));
    }

    function load() {
      return KUI.api("/setup").then(function (state) {
        A.refreshSetupCount();
        syncOrder(state);
        if (!state.lang && !KUI.langChosen() && !route.sub[0]) { chooseLanguage(); return state; }  // very first screen
        if (!STEPS[active] || ORDER.indexOf(active) < 0) {
          active = firstOpen(state);
          if (route.sub[0]) { location.replace(BASE + active); return state; }   // the address must show the step that is drawn
        }
        draw(state);
        return state;
      }).catch(function (e) { KUI.clear(host); host.appendChild(h("div", { class: "wz-card card" }, A.errorBox(e))); });
    }

    function draw(state) {
      A.onLeaveNow();
      leaveHooks();
      document.body.classList.add("wizard-mode");
      var idx = ORDER.indexOf(active);
      var ctx = { state: state };
      ctx.refresh = function () {
        return KUI.api("/setup").then(function (s) { ctx.state = s; state = s; syncOrder(s); chrome(s); A.refreshSetupCount(); return s; });
      };
      ctx.done = function () { return ctx.refresh().then(function (s) { if (stepOf(s, active).done) ctx.advanceSoon(); }); };
      ctx.advanceSoon = function () {
        clearTimeout(advanceTimer);
        advanceTimer = setTimeout(function () {
          var i = ORDER.indexOf(active);   // -1: the step vanished (e.g. "skip the channel"): continue at the first open one
          var n = i < 0 ? firstOpen(ctx.state) : ORDER[i + 1];
          if (n) go(n);
        }, 800);
      };
      var def = STEPS[active](ctx);
      var next = A.button(def.final ? "Panele git" : "Devam", { kind: "primary", trail: "arrow-right" });
      var back = idx > 0 ? A.button("Geri", { kind: "ghost", icon: "chev-left", onclick: function () {
        var i = ORDER.indexOf(active);   // -1: the step vanished meanwhile (e.g. the channel step was skipped)
        go(i > 0 ? ORDER[i - 1] : firstOpen(ctx.state));
      } }) : h("span");
      var skip = def.skip ? A.button(def.skip.label, { kind: "ghost" }) : null;
      var tools = h("div", { class: "wz-nav-tools" }, skip, def.final ? null : next);
      if (def.final) next = null;
      var chip = h("span", { "data-step-chip": "" });
      var prog = h("div", { class: "wz-progress" });
      var navbar = h("div", { class: "wz-nav" }, back, tools);

      function chrome(s) {
        var st = stepOf(s, active);
        KUI.clear(prog); prog.appendChild(progress(s, active, go));
        KUI.clear(chip);
        if (!def.final && st) chip.appendChild(st.done ? A.badge("Tamam", "success", "success") : st.state === "ready" ? A.badge("Doğrulanmadı", "accent") : A.badge("Bekliyor", "outline"));
        if (next && st) {
          next.disabled = !st.done;
          next.title = st.done ? "" : "Önce bu adımı tamamlayın";
        }
      }
      if (next) next.addEventListener("click", function () { var n = ORDER[ORDER.indexOf(active) + 1]; if (n) go(n); });
      if (skip) skip.addEventListener("click", function () {
        KUI.busy(skip, def.skip.run().then(function () { return ctx.refresh(); }).then(function () { ctx.advanceSoon(); }).catch(KUI.errorToast));
      });

      var skipLater = h("a", { class: "btn btn-ghost btn-sm", href: "#/" }, "Sonra yap");
      skipLater.addEventListener("click", function (e) {
        e.preventDefault();
        KUI.busy(skipLater, KUI.api("/setup/dismiss", { method: "POST" }).then(function () {
          KUI.toast("Kurulumu istediğiniz zaman Kurulum sayfasından tamamlayabilirsiniz.", { kind: "info", timeout: 5000 });
          location.hash = "#/kurulum";
        }).catch(KUI.errorToast));
      });

      KUI.clear(host);
      host.appendChild(h("header", { class: "wz-top" },
        h("span", { class: "brand" }, h("span", { class: "brand-mark is-logo" }, h("img", { src: "logo.svg", alt: "", width: "36", height: "36" })), h("span", { class: "brand-name", text: "km-ai kurulumu" })),
        h("div", { class: "row" }, langSwitch(), skipLater)));
      host.appendChild(prog);
      var dir = lastIdx < 0 ? "none" : idx > lastIdx ? "fwd" : idx < lastIdx ? "back" : "none";
      lastIdx = idx;
      host.appendChild(h("section", { class: "card wz-card go-" + dir, "aria-labelledby": "wz-title" },
        h("header", { class: "wz-head" },
          h("div", { class: "row-between" }, h("span", { class: "eyebrow", text: def.final ? "Son adım" : "Adım " + (idx + 1) + " / " + (ORDER.length - 1) }), chip),
          h("h1", { id: "wz-title", text: def.title }), def.text ? h("p", { text: def.text }) : null),
        h("div", { class: "wz-body" }, def.body),
        navbar));
      chrome(state);
      window.scrollTo(0, 0);
    }

    load();
  }

  // ------------------------------------------------------------ "Kurulum" page: checklist + single-step editor
  var STATE_LABEL = { done: ["Tamam", "success", "success"], ready: ["Doğrulanmadı", "accent", null], todo: ["Eksik", "danger", "danger"] };
  var ACTION = { done: "Düzenle", ready: "Doğrula", todo: "Tamamla" };
  var HINT = {
    panel: "Panel şifresi olmadan bu adresi bilen herkes paneli açar.",
    channel: "Hangi kanaldan konuşacağınızı seçin; kurulumu ona göre ilerler.",
    channel_setup: "Kanal kapalı ya da ayarları eksik; bot o kanaldan yazışamaz.",
    telegram: "Bot Telegram'a bağlanmadan mesajlara yanıt veremez.",
    chats: "Liste boşken hafıza, görev ve AI araçları kapalıdır.",
    llm: "Model olmadan bot yanıt üretemez.",
    search: "İsteğe bağlı: web araması için."
  };

  function renderList(main) {
    var host = h("div", { class: "stack gap-lg" }, A.skeleton(5));
    main.appendChild(A.pageHead("Yapılandırma", "Kurulum durumu", "Botun çalışması için gerekenler. Eksik veya doğrulanmamış olanları buradan tamamlayın ya da değiştirin.",
      h("a", { class: "btn btn-ghost btn-sm", href: BASE + "panel" }, A.icon("refresh"), "Sihirbazı yeniden başlat")));
    main.appendChild(host);
    KUI.api("/setup").then(function (state) {
      A.refreshSetupCount();
      var steps = state.steps.slice(0, -1);
      var required = steps.filter(function (x) { return !x.optional; });
      var missing = required.filter(function (x) { return !x.done; });
      var banner = missing.length
        ? A.callout("danger", h("span", null, h("strong", { text: missing.length + " gerekli adım eksik: " }), missing.map(function (x) { return x.title; }).join(", ") + "."), "alert")
        : A.callout("success", state.completed ? "Kurulum tamam. Bot kullanıma hazır." : "Gerekli her şey doğrulandı. Aşağıdan kurulumu bitirebilirsiniz.");
      var rows = h("ul", { class: "list check-list" }, steps.map(function (x) {
        var st = STATE_LABEL[x.state] || STATE_LABEL.todo;
        var btn = h("a", { class: "btn btn-sm" + (x.done ? "" : " btn-primary"), href: "#/kurulum/" + x.id }, ACTION[x.state] || "Düzenle");
        return h("li", { class: "check-row is-" + x.state },
          h("span", { class: "check-mark", "aria-hidden": "true" }, x.done ? A.icon("check") : A.icon(x.state === "ready" ? "info" : "alert")),
          h("div", { class: "grow check-main" },
            h("div", { class: "row" }, h("strong", { class: "ink", text: x.title }), x.optional ? A.badge("isteğe bağlı", "outline") : null, A.badge(st[0], st[1], st[2])),
            h("div", { class: "small muted break", text: x.detail }),
            x.done ? null : h("div", { class: "small muted", text: HINT[x.id] || "" })),
          btn);
      }));
      var finish = null;
      if (!state.completed && !missing.length) {
        finish = A.button("Kurulumu bitir", { kind: "primary", trail: "check" });
        finish.addEventListener("click", function () {
          KUI.busy(finish, KUI.api("/setup/complete", { method: "POST" }).then(function () {
            KUI.toast("Kurulum tamam. Ayarlar anında uygulanıyor.", { kind: "success" }); location.reload();
          }).catch(KUI.errorToast));
        });
      }
      KUI.clear(host);
      host.appendChild(banner);
      host.appendChild(A.card(null, null, [rows, finish ? h("div", { class: "cluster" }, finish) : null]));
      host.appendChild(h("p", { class: "small muted", text: "Değerler ortam değişkeninden gelebilir; doğrulanana kadar \"Doğrulanmadı\" kalır. Panelde kaydettiğiniz değer ortam değişkenini ezer." }));
    }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
  }

  function renderEdit(main, step) {
    var host = h("div", { class: "stack" }, A.skeleton(4));
    var back = h("a", { class: "btn btn-ghost btn-sm", href: "#/kurulum" }, A.icon("chev-left"), "Kurulum durumu");
    main.appendChild(h("div", { class: "edit-wrap stack gap-lg" }, h("div", null, back), host));
    function load() {
      return KUI.api("/setup").then(function (state) {
        A.onLeaveNow();
        if (!stepOf(state, step)) { location.replace("#/kurulum"); return; }   // a step that is not part of this setup (other channel picked)
        var ctx = { state: state };
        var timer = null;
        A.onLeave(function () { clearTimeout(timer); });
        ctx.refresh = function () { return KUI.api("/setup").then(function (s) { ctx.state = s; A.refreshSetupCount(); return s; }); };
        ctx.advanceSoon = function () { clearTimeout(timer); timer = setTimeout(function () { location.hash = "#/kurulum"; }, 900); };
        ctx.done = function () {
          return ctx.refresh().then(function (s) {
            var st = stepOf(s, step);
            if (st && st.done) { KUI.toast(st.title + " kaydedildi.", { kind: "success" }); ctx.advanceSoon(); }
          });
        };
        var def = STEPS[step](ctx);
        var st = stepOf(state, step);
        KUI.clear(host);
        host.appendChild(h("section", { class: "card wz-card", "aria-labelledby": "wz-title" },
          h("header", { class: "wz-head" },
            h("div", { class: "row-between" }, h("span", { class: "eyebrow", text: "Kurulum" }),
              st.done ? A.badge("Tamam", "success", "success") : st.state === "ready" ? A.badge("Doğrulanmadı", "accent") : A.badge("Eksik", "danger", "danger")),
            h("h1", { id: "wz-title", text: def.title }), def.text ? h("p", { text: def.text }) : null),
          h("div", { class: "wz-body" }, def.body)));
      }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
    }
    load();
  }

  A.pages.setup = {
    title: "Kurulum",
    render: function (main, route) {
      var step = route.sub[0];
      if (step && STEPS[step] && step !== "summary") renderEdit(main, step);
      else renderList(main);
    }
  };
  A.pages.welcome = { title: "Kurulum", render: render };
})();
