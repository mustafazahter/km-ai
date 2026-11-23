/* ai-bot admin UI — shell: hash router, helpers shared by the pages (window.AIB).
   Text is always inserted with textContent (KUI.el); no innerHTML, no inline script/style (CSP). */
(function () {
  "use strict";

  var h = KUI.el;
  var KIT_ICONS = { mark: 1, home: 1, grid: 1, sliders: 1, key: 1, tool: 1, activity: 1, plug: 1, list: 1, clock: 1,
    shield: 1, user: 1, logout: 1, sun: 1, moon: 1, check: 1, x: 1, alert: 1, info: 1, refresh: 1, "arrow-right": 1,
    lock: 1, trash: 1, message: 1 };

  /** icon("brain") — kit sprite or the ai-bot sprite (icons.svg) */
  function icon(name, cls) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", KUI.iconHref(name, KIT_ICONS[name] ? "/ui/_kit/icons.svg" : "/ui/icons.svg"));
    svg.appendChild(use);
    return svg;
  }

  var pages = {};
  var routes = {
    "": "overview", "kurulum": "setup", "ayarlar": "settings", "hafiza": "memory",
    "gorevler": "tasks", "araclar": "tools", "gunluk": "logs", "telegram": "telegram", "kopru": "bridge", "sohbet": "chat", "hos-geldin": "welcome", "kanallar": "channels"
  };
  var current = null;
  var cleanup = [];

  function onLeave(fn) { cleanup.push(fn); }
  function onLeaveNow() { cleanup.splice(0).forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } }); }

  function parseHash() {
    var raw = (location.hash || "#/").replace(/^#\/?/, "");
    var parts = raw.split("?");
    var query = {};
    (parts[1] || "").split("&").forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf("=");
      query[decodeURIComponent(i < 0 ? kv : kv.slice(0, i))] = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1));
    });
    var path = parts[0].split("/");
    return { name: routes[path[0]] || null, sub: path.slice(1), query: query };
  }

  function markNav(name) {
    KUI.$all("[data-nav] a").forEach(function (a) {
      if (a.getAttribute("data-route") === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var more = KUI.$("[data-more]");
    if (more) more.classList.toggle("is-current", ["tasks", "tools", "logs", "bridge", "memory"].indexOf(name) >= 0);
  }

  function render() {
    cleanup.splice(0).forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } });
    var route = parseHash();
    var main = KUI.$("#main");
    KUI.clear(main);
    if (!route.name || !pages[route.name]) {
      main.appendChild(empty("alert", "Sayfa bulunamadı", "Soldaki menüden bir sayfa seçin."));
      return;
    }
    var page = pages[route.name];
    current = route.name;
    markNav(route.name === "telegram" ? "channels" : route.name);
    document.body.classList.toggle("page-chat", route.name === "chat");
    if (route.name !== "welcome") document.body.classList.remove("wizard-mode");
    KUI.$("[data-page-title]").textContent = KUI.t(page.title);
    document.title = KUI.t(page.title) + " · " + (AIB.me ? AIB.me.title : "km-ai");
    try {
      page.render(main, route);
    } catch (e) {
      main.appendChild(errorBox(e));
    }
    var sheet = KUI.$("[data-sheet]");
    if (sheet && sheet.open) closeSheet();
    if (AIB.help) AIB.help.close();  // a guide never outlives the page it was opened on
    window.scrollTo(0, 0);
  }

  // ------------------------------------------------------------ building blocks
  function pageHead(eyebrow, title, text, actions) {
    return h("div", { class: "page-head" },
      h("div", null, h("span", { class: "eyebrow", text: eyebrow }), h("h1", { text: title }),
        text ? h("p", { text: text }) : null),
      actions ? h("div", { class: "cluster" }, actions) : null);
  }

  function card(title, sub, children, opts) {
    opts = opts || {};
    return h("section", { class: "card" + (opts.flush ? " card-flush" : "") + (opts.cls ? " " + opts.cls : ""),
      "aria-label": title || null },
      title ? h("div", { class: "card-head" },
        h("div", { class: "grow" }, h("h2", { text: title }), sub ? h("p", { class: "card-sub", text: sub }) : null),
        opts.action || null) : null,
      children);
  }

  function empty(iconName, title, text, action) {
    return h("div", { class: "empty" }, icon(iconName), h("strong", { text: title }),
      text ? h("span", { class: "small", text: text }) : null, action || null);
  }

  /** morph(live, fresh): make the children of `live` match `fresh` by editing nodes in place (text, attributes,
   *  added or removed nodes). Untouched nodes stay, so a periodic refresh causes no flicker and keeps focus,
   *  hover and scroll. `fresh` is consumed. Only for markup without per-node listeners (links, text). */
  function morph(live, fresh) {
    var o = live.firstChild, n = fresh.firstChild;
    while (n) {
      var nextFresh = n.nextSibling;
      if (!o) {
        live.appendChild(n);
      } else if (o.nodeType === 3 && n.nodeType === 3) {
        if (o.nodeValue !== n.nodeValue) o.nodeValue = n.nodeValue;
        o = o.nextSibling;
      } else if (o.nodeType === 1 && n.nodeType === 1 && o.nodeName === n.nodeName) {
        Array.prototype.slice.call(o.attributes).forEach(function (at) { if (!n.hasAttribute(at.name)) o.removeAttribute(at.name); });
        Array.prototype.forEach.call(n.attributes, function (at) { if (o.getAttribute(at.name) !== at.value) o.setAttribute(at.name, at.value); });
        morph(o, n);
        o = o.nextSibling;
      } else {
        var after = o.nextSibling;
        live.replaceChild(n, o);
        o = after;
      }
      n = nextFresh;
    }
    while (o) { var gone = o; o = o.nextSibling; live.removeChild(gone); }
  }

  function skeleton(rows) {
    var out = [];
    for (var i = 0; i < (rows || 3); i++) out.push(h("div", { class: "skeleton" }));
    return h("div", { class: "stack-sm", "aria-busy": "true" }, out);
  }

  function errorBox(err) {
    return h("div", { class: "callout callout-danger", role: "alert" }, icon("alert"),
      h("span", { text: (err && err.message) || "Bir hata oluştu" }));
  }

  function callout(kind, text, iconName) {
    return h("div", { class: "callout result" + (kind === "danger" ? " callout-danger" : kind === "success" ? " callout-success" : ""),
      role: kind === "danger" ? "alert" : "status" },
      icon(iconName || (kind === "success" ? "check" : kind === "danger" ? "alert" : "info")),
      typeof text === "string" ? h("span", { text: text }) : text);
  }

  function badge(text, kind, dot) {
    return h("span", { class: "badge" + (kind ? " badge-" + kind : "") },
      dot ? h("span", { class: "dot" + (dot === true ? "" : " dot-" + dot) }) : null, text);
  }

  function sourceBadge(source) {
    var map = { ui: ["arayüz", "accent"], env: ["ortam", "outline"], "default": ["varsayılan", null], none: ["yok", null] };
    var m = map[source] || [source, null];
    return h("span", { class: "badge src" + (m[1] ? " badge-" + m[1] : ""), title: source === "ui" ? "Arayüzde kaydedildi (ortam değişkenini ezer)" : null, text: m[0] });
  }

  function button(label, opts) {
    opts = opts || {};
    var cls = "btn" + (opts.kind ? " btn-" + opts.kind : "") + (opts.size ? " btn-" + opts.size : "") + (opts.cls ? " " + opts.cls : "");
    return h("button", { class: cls, type: opts.type || "button", onclick: opts.onclick || null, "aria-label": opts.aria || null,
      disabled: opts.disabled || null },
      opts.icon ? icon(opts.icon) : null, label,
      opts.trail ? h("span", { class: "btn-trail", "aria-hidden": "true" }, icon(opts.trail)) : null);
  }

  function field(id, label, input, hint) {
    input.id = id;
    return h("div", { class: "field" }, h("label", { for: id, text: label }), input,
      hint ? h("span", { class: "hint", text: hint }) : null,
      h("span", { class: "error-text", "data-error-for": input.name || id }));
  }

  /** Secret input: never pre-filled, shows "saved …1234 (arayüz)" state next to it. */
  function secretInput(name, placeholder) {
    var input = h("input", { class: "input", type: "password", name: name, autocomplete: "off", spellcheck: "false",
      autocapitalize: "off", placeholder: placeholder || "Yeni değer girin" });
    return { input: input, group: KUI.passwordField(input) };
  }

  function secretState(desc) {
    if (!desc || !desc.set) return h("span", { class: "secret-state" }, badge("Kayıtlı değil", "outline"));
    return h("span", { class: "secret-state" }, icon("lock"),
      h("span", { text: "Kayıtlı " + (desc.preview || "…") }), sourceBadge(desc.source));
  }

  /** Server texts written as "Türkçe / English": pick the half for the current language. */
  function bi(text) {
    if (typeof text !== "string") return text;
    var i = text.indexOf(" / ");
    if (i < 0 || !/[çğıöşüÇĞİÖŞÜ]|\b(ve|bir|için|ile)\b/.test(text.slice(0, i))) return text;
    var tr = text.slice(0, i), en = text.slice(i + 3);
    if (/[çğıöşüÇĞİÖŞÜ]/.test(en)) return text;   // a slash inside Turkish prose
    return KUI.lang() === "en" ? en : tr;
  }

  function fmtTime(ts, tz) {
    if (!ts) return "—";
    try {
      return new Intl.DateTimeFormat(KUI.locale(), { timeZone: tz || undefined, day: "2-digit", month: "short",
        hour: "2-digit", minute: "2-digit" }).format(new Date(ts * 1000));
    } catch (e) {
      return new Date(ts * 1000).toLocaleString(KUI.locale());
    }
  }

  function relTime(ts) {
    if (!ts) return "—";
    var diff = ts - Date.now() / 1000;
    var abs = Math.abs(diff);
    var txt = abs < 60 ? "1 dk'dan az" : abs < 3600 ? Math.round(abs / 60) + " dk" : abs < 86400 ? Math.round(abs / 3600) + " sa" : Math.round(abs / 86400) + " gün";
    return diff >= 0 ? txt + " sonra" : txt + " önce";
  }

  function applyNote(applied) {
    if (!applied) return "Kaydedildi.";
    if (applied.restart_required) return "Kaydedildi. Bu değişiklik için servisi yeniden başlatın.";
    var notes = (applied.notes || []).join(" ");
    return "Kaydedildi ve canlı uygulandı." + (notes ? " " + notes : "");
  }

  function errorsFrom(err, form) {
    if (err && err.code === "invalid_value" && form) {
      var m = /^([A-Z_]+):/.exec(err.message || "");
      if (m) { var e = {}; e[m[1]] = err.message.replace(/^[A-Z_]+:\s*/, ""); KUI.setErrors(form, e); return; }
    }
    KUI.errorToast(err);
  }


  // ------------------------------------------------------------ env / panel source of a field
  var SOURCE_TEXT = { env: "Ortam değişkeninden", ui: "Panelde kayıtlı", "default": "Varsayılan", none: "Ayarlı değil", komuta: "Komuta girişi" };

  /** One line telling where a value comes from: "Ortam değişkeninden: NAME …1234" / "Panelde kayıtlı" + "env'e dön". */
  function sourceLine(f, onReset) {
    if (!f) return null;
    var parts = [];
    if (f.source === "env") {
      parts.push(icon("info"), h("span", null, "Ortam değişkeninden: ", h("code", { text: f.name })));
      if (f.secret && f.preview) parts.push(h("span", { class: "mono-sm", text: f.preview }));
    } else if (f.source === "ui") {
      parts.push(icon("check"), h("span", { text: SOURCE_TEXT.ui + (f.secret && f.preview ? " " + f.preview : "") }));
      if (f.env_set) parts.push(h("span", { class: "small muted", text: "· ortam değerini eziyor" }));
      if (f.env_set && onReset) {
        var back = button("Ortam değişkenine dön", { kind: "ghost", size: "sm", icon: "undo" });
        back.addEventListener("click", function () {
          var path = (f.secret ? "/secrets/" : "/config/") + f.name;
          busy(back, KUI.api(path, { method: "DELETE" }).then(function (r) {
            KUI.toast(f.name + ": ortam değişkeni değeri geçerli.", { kind: "success" });
            onReset(r);
          }).catch(KUI.errorToast));
        });
        parts.push(back);
      }
    } else if (f.source === "default") {
      parts.push(h("span", { class: "small muted", text: "Varsayılan değer" }));
    } else {
      parts.push(h("span", { class: "small muted", text: "Ayarlı değil" }));
    }
    return h("div", { class: "source-line source-" + f.source }, parts);
  }
  function busy(b, p) { return KUI.busy(b, p); }

  /** Shows a just-created secret ONCE (kit dialog: masked, reveal on demand, copy, forgotten on close). */
  function showOnce(title, value, note) { return KUI.showOnce(value, { title: title, note: note, label: title }); }
  /** Clipboard via the kit (secure-context API with a fallback) -> Promise<boolean>. */
  function copyText(text) { return KUI.copy(text); }

  /** A code block with a copy button (examples on the bridge page). */
  function codeBlock(text, label) {
    var pre = h("pre", { class: "code" }, h("code", { text: text }));
    var copy = button("Kopyala", { size: "sm", kind: "ghost", icon: "check", aria: (label || "Örnek") + " kopyala" });
    copy.addEventListener("click", function () {
      copyText(text).then(function (ok) { KUI.toast(ok ? "Kopyalandı" : "Kopyalanamadı; elle seçin", { kind: ok ? "success" : "danger" }); });
    });
    return h("div", { class: "code-wrap" }, h("div", { class: "code-head" }, h("span", { class: "small muted", text: label || "" }), copy), pre);
  }

  /** "Hazır" / done / todo state chip for a wizard step. */
  function stateChip(state) {
    if (state === "done") return badge("Tamam", "success", "success");
    if (state === "ready") return badge("Hazır · ortam", "accent");
    return badge("Bekliyor", "outline");
  }

  // ------------------------------------------------------------ sheet (mobile "more")
  function openSheet() {
    var sheet = KUI.$("[data-sheet]");
    if (!sheet) return;
    sheet.showModal();
    KUI.$("[data-more]").setAttribute("aria-expanded", "true");
  }
  function closeSheet() {
    var sheet = KUI.$("[data-sheet]");
    if (sheet && sheet.open) sheet.close();
    var more = KUI.$("[data-more]");
    if (more) more.setAttribute("aria-expanded", "false");
  }

  function refreshSetupCount() {
    return KUI.api("/setup").then(function (s) {
      if (s.lang && !KUI.langChosen()) { KUI.setLang(s.lang); return s; }  // another browser picked a language earlier
      var n = KUI.$("[data-setup-count]");
      if (!n) return s;
      var left = s.total - s.done;
      n.hidden = left <= 0;
      n.textContent = left > 0 ? String(left) : "";
      n.setAttribute("aria-label", KUI.t(left + " adım kaldı"));
      return s;
    }).catch(function () { /* the page shows its own errors */ });
  }

  function wireLanguage() {
    KUI.$all("[data-lang]").forEach(function (b) {
      var on = b.getAttribute("data-lang") === KUI.lang();
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.addEventListener("click", function () {
        var next = b.getAttribute("data-lang");
        if (next === KUI.lang()) return;
        KUI.api("/setup/lang", { method: "POST", body: { lang: next } }).catch(function () { /* the browser still switches */ })
          .then(function () { KUI.setLang(next); });
      });
    });
  }

  function start() {
    wireLanguage();
    KUI.loadMe().then(function (me) { AIB.me = me; if (current) document.title = KUI.t(pages[current].title) + " · " + me.title; })
      .catch(function () { /* 401 redirects; other errors shown by pages */ });
    var more = KUI.$("[data-more]");
    if (more) more.addEventListener("click", openSheet);
    var close = KUI.$("[data-sheet-close]");
    if (close) close.addEventListener("click", closeSheet);
    var sheet = KUI.$("[data-sheet]");
    if (sheet) {
      sheet.addEventListener("close", function () { KUI.$("[data-more]").setAttribute("aria-expanded", "false"); });
      sheet.addEventListener("click", function (e) { if (e.target === sheet) closeSheet(); });
    }
    window.addEventListener("hashchange", function () {
      var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (document.startViewTransition && !calm) document.startViewTransition(function () { render(); });
      else render();
    });
    render();
    refreshSetupCount();
    KUI.api("/overview").then(function (d) {
      var live = KUI.$("[data-live-text]");
      var ready = (d.channels || []).filter(function (c) { return c.status === "ready"; });
      if (live) live.textContent = KUI.t(ready.length === 0 ? "Çalışıyor" : ready.length === 1 ? ready[0].title + " bağlı" : ready.length + " kanal bağlı");
    }).catch(function () { /* shown by pages */ });
  }

  window.AIB = {
    pages: pages, icon: icon, h: h, onLeave: onLeave, onLeaveNow: onLeaveNow, render: render, start: start,
    pageHead: pageHead, morph: morph, card: card, empty: empty, skeleton: skeleton, errorBox: errorBox, callout: callout,
    badge: badge, sourceBadge: sourceBadge, button: button, field: field, secretInput: secretInput,
    secretState: secretState, fmtTime: fmtTime, relTime: relTime, applyNote: applyNote, errorsFrom: errorsFrom,
    refreshSetupCount: refreshSetupCount, me: null, bi: bi,
    sourceLine: sourceLine, showOnce: showOnce, copyText: copyText, codeBlock: codeBlock, stateChip: stateChip
  };
})();
