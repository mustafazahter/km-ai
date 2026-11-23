/* komuta-ui.js — tiny helpers for komuta-stack admin pages. No dependencies, no build.
   Load in <head> with `defer` NOT set for the theme to apply before paint:
     <script src="/ui/_kit/komuta-ui.js"></script>
   Everything lives on window.KUI. Text is always inserted with textContent (never as HTML). */
(function () {
  "use strict";

  var root = document.documentElement;
  var THEME_KEY = "kui-theme";
  var ICONS = "/ui/_kit/icons.svg";

  // ------------------------------------------------------------- theme (before first paint)
  function storedTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }
  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }
  function currentTheme() {
    var t = root.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function setTheme(theme) {
    applyTheme(theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* private mode */ }
    syncThemeButtons();
  }
  applyTheme(storedTheme());

  // ------------------------------------------------------------- DOM helpers
  /** el("div", {class: "card", onclick: fn}, "text", childNode) */
  // ------------------------------------------------------------- i18n
  // Source text is Turkish. With lang "en" every visible string goes through a dictionary (exact match on the trimmed
  // text, whitespace kept) and then regex rules; unknown text stays as is. Dictionary: window.AIB_I18N_EN / _RULES.
  var LANG_KEY = "kui-lang";
  var lang = (function () {
    try { var v = localStorage.getItem(LANG_KEY); if (v === "tr" || v === "en") return v; } catch (e) { /* storage blocked */ }
    return "tr";
  })();
  function langChosen() { try { return !!localStorage.getItem(LANG_KEY); } catch (e) { return false; } }
  document.documentElement.setAttribute("lang", lang);
  var ruleCache = null;
  function tr(text) {
    if (lang === "tr" || typeof text !== "string" || !text) return text;
    var dict = window.AIB_I18N_EN || {};
    if (dict[text] !== undefined) return dict[text];
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text), core = m[2];
    if (!core) return text;
    if (dict[core] !== undefined) return m[1] + dict[core] + m[3];
    if (!ruleCache) {
      ruleCache = [];
      (window.AIB_I18N_RULES || []).forEach(function (r) { try { ruleCache.push([new RegExp(r[0]), r[1]]); } catch (e) { /* bad rule */ } });
    }
    for (var i = 0; i < ruleCache.length; i++) {
      if (ruleCache[i][0].test(core)) return m[1] + core.replace(ruleCache[i][0], ruleCache[i][1]) + m[3];
    }
    return text;
  }
  var TR_ATTRS = { placeholder: 1, "aria-label": 1, title: 1, alt: 1 };
  /** Translate static markup (text nodes and a few attributes) once at start-up. */
  function translateTree(root) {
    if (lang === "tr" || !root) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null), n, nodes = [];
    while ((n = walker.nextNode())) {
      var tag = n.parentNode && n.parentNode.nodeName;
      if (tag !== "SCRIPT" && tag !== "STYLE" && n.nodeValue.trim()) nodes.push(n);
    }
    nodes.forEach(function (t) { t.nodeValue = tr(t.nodeValue); });
    Array.prototype.forEach.call(root.querySelectorAll("[placeholder],[aria-label],[title],[alt]"), function (e) {
      Object.keys(TR_ATTRS).forEach(function (a) { if (e.hasAttribute(a)) e.setAttribute(a, tr(e.getAttribute(a))); });
    });
    if (document.title) document.title = tr(document.title);
  }
  function setLang(next) {
    if (next !== "tr" && next !== "en") return;
    try { localStorage.setItem(LANG_KEY, next); } catch (e) { /* storage blocked: stays per page load */ }
    window.location.reload();
  }
  function locale() { return lang === "en" ? "en-US" : "tr-TR"; }

  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k.slice(0, 2) === "on" && typeof v === "function") node.addEventListener(k.slice(2), v);
        else if (k === "text") node.textContent = tr(String(v));
        else node.setAttribute(k, v === true ? "" : (TR_ATTRS[k] ? tr(String(v)) : String(v)));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }
  function append(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(node, c); }); return; }
    node.appendChild(child instanceof Node ? child : document.createTextNode(tr(String(child))));
  }
  function icon(name, cls) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", iconHref(name));
    svg.appendChild(use);
    return svg;
  }
  // The server inlines the sprites into the page (svg.kui-sprite): icons then never wait for a second request.
  // Without that (login pages, old servers) the external file is used.
  var spriteNode;
  function iconHref(name, external) {
    if (spriteNode === undefined) spriteNode = document.querySelector("svg.kui-sprite") || null;
    if (spriteNode && spriteNode.querySelector('symbol[id="' + name + '"]')) return "#" + name;
    return (external || ICONS) + "#" + name;
  }
  function $(sel, scope) { return (scope || document).querySelector(sel); }
  function $all(sel, scope) { return Array.prototype.slice.call((scope || document).querySelectorAll(sel)); }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); return node; }

  // ------------------------------------------------------------- API
  function cookie(name) {
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      var idx = parts[i].indexOf("=");
      if (parts[i].slice(0, idx) === name) return decodeURIComponent(parts[i].slice(idx + 1));
    }
    return "";
  }

  function ApiError(status, code, message, body) {
    this.name = "ApiError"; this.status = status; this.code = code; this.message = message; this.body = body;
  }
  ApiError.prototype = Object.create(Error.prototype);

  /** api("/status") or api("/settings/X", {method: "PUT", body: {value: 1}}) — path is under /ui/api. */
  // ------------------------------------------------------------- "updating" screen
  // When the service restarts for a new version, requests fail for a few seconds. Show a calm full-page notice,
  // poll /health, and reload as soon as the server answers again.
  var updating = (function () {
    var node = null, timer = null, probing = false, attempts = 0;
    function brand() { var t = (document.title || "").split("·"); return (t[t.length - 1] || "").trim() || "Panel"; }
    function health() {
      var ctl = window.AbortController ? new AbortController() : null;
      var to = setTimeout(function () { if (ctl) ctl.abort(); }, 3000);
      return fetch("/health", { cache: "no-store", credentials: "same-origin", signal: ctl ? ctl.signal : undefined })
        .then(function (r) { clearTimeout(to); return r.ok; }, function () { clearTimeout(to); return false; });
    }
    function markCore() {                        // the page's own logo (its favicon) sits in the ring, else a neutral tile
      var ic = document.querySelector('link[rel~="icon"]');
      if (ic && ic.getAttribute("href")) return el("img", { class: "upd-logo", src: ic.getAttribute("href"), alt: "" });
      return el("span", { class: "upd-core" });
    }
    function show() {
      if (node) return;
      var status = el("span", { class: "upd-status", text: "Yeniden bağlanılıyor…" });
      node = el("div", { class: "upd-screen", role: "alert", "aria-live": "assertive" },
        el("div", { class: "upd-card" },
          el("span", { class: "upd-mark" }, el("span", { class: "upd-orbit" }), markCore()),
          el("h2", { text: "Güncelleniyor" }),
          el("p", { text: brand() + " yeni sürüme geçiyor. Birkaç saniye sürer; sayfa hazır olunca kendiliğinden yenilenir." }),
          el("div", { class: "upd-bar", "aria-hidden": "true" }, el("span")),
          status));
      document.body.appendChild(node);
      poll(status);
    }
    function poll(status) {
      clearTimeout(timer);
      timer = setTimeout(function () {
        attempts += 1;
        health().then(function (ok) {
          if (ok) { status.textContent = "Hazır, yenileniyor…"; setTimeout(function () { window.location.reload(); }, 500); return; }
          status.textContent = "Yeniden bağlanılıyor… (deneme " + attempts + ")";
          poll(status);
        });
      }, 2000);
    }
    return {
      maybe: function () {
        if (node || probing) return;
        probing = true;
        setTimeout(function () {                 // one confirmed failure is enough, but wait a beat: ignore single blips
          health().then(function (ok) { probing = false; if (!ok) show(); });
        }, 800);
      }
    };
  })();

  // ------------------------------------------------------------- new version while the tab is open
  // A rolling update keeps the old version serving until the new one is ready, so an open tab never sees an outage
  // and keeps running old scripts. The server stamps every response with a build id; when it differs from the id
  // baked into this page, reload (at most once per 30 s so a mixed rollout cannot loop).
  var newVersion = (function () {
    var meta = document.querySelector('meta[name="kui-build"]');
    var mine = meta ? meta.getAttribute("content") : null;
    var KEY = "kui-reload";
    function state() {
      try { var v = JSON.parse(sessionStorage.getItem(KEY) || "{}"); return v && typeof v === "object" ? v : {}; } catch (e) { return {}; }
    }
    return {
      check: function (seen) {
        if (!mine || !seen || seen === mine) return;
        var st = state(), n = st.build === seen ? Number(st.n) || 0 : 0;
        // at most 2 reloads per server build, 30 s apart: a mixed rollout (old and new pods answering) cannot loop
        if (n >= 2 || Date.now() - (Number(st.at) || 0) < 30000) return;
        try { sessionStorage.setItem(KEY, JSON.stringify({ build: seen, n: n + 1, at: Date.now() })); } catch (e) { /* private mode: still reload once */ }
        toast("Yeni sürüm hazır, sayfa yenileniyor…", { kind: "info", timeout: 1500 });
        setTimeout(function () { window.location.reload(); }, 1200);
      }
    };
  })();

  function api(path, opts) {
    opts = opts || {};
    var method = (opts.method || "GET").toUpperCase();
    var headers = { "Accept": "application/json" };
    var body;
    if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
    if (method !== "GET" && method !== "HEAD") headers["X-CSRF-Token"] = cookie("skui_csrf");
    var url = path.charAt(0) === "/" && path.indexOf("/ui/") !== 0 ? "/ui/api" + path : path;
    return fetch(url, { method: method, headers: headers, body: body, credentials: "same-origin", cache: "no-store" })
      .catch(function (err) { updating.maybe(); throw err; })   // network down: maybe a new version is rolling out
      .then(function (res) {
        newVersion.check(res.headers.get("X-UI-Build"));
        return res.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
          if (res.status === 401 && data && data.error && data.error.code === "ui_login_required") {
            window.location.assign("/ui/login");
          }
          if (res.status === 401 && data && data.error && data.error.code === "ui_setup_required") {
            window.location.assign("/ui/setup");
          }
          if ((res.status === 502 || res.status === 503 || res.status === 504) && !(data && data.error)) updating.maybe();
          if (!res.ok || (data && data.ok === false)) {
            var err = (data && data.error) || {};
            throw new ApiError(res.status, err.code || "http_" + res.status, err.message || res.statusText || ("İstek başarısız (HTTP " + res.status + ")"), data);
          }
          return data;
        });
      });
  }

  // ------------------------------------------------------------- toast
  function toastHost() {
    var host = $(".toasts");
    if (!host) {
      host = el("div", { class: "toasts", role: "region", "aria-label": "Bildirimler", "aria-live": "polite" });
      document.body.appendChild(host);
    }
    return host;
  }
  /** toast("Kaydedildi", {kind: "success"|"danger"|"info", timeout: 4000}) */
  function toast(message, opts) {
    opts = opts || {};
    var kind = opts.kind || "info";
    var name = kind === "success" ? "check" : kind === "danger" ? "alert" : "info";
    var node = el("div", { class: "toast toast-" + kind, role: kind === "danger" ? "alert" : "status" },
      icon(name), el("div", { class: "toast-msg", text: message }),
      el("button", { class: "toast-close", type: "button", "aria-label": "Kapat", onclick: function () { dismiss(); } }, icon("x")));
    var timer;
    function dismiss() {
      clearTimeout(timer);
      node.setAttribute("data-leaving", "");
      setTimeout(function () { node.remove(); }, 260);
    }
    toastHost().appendChild(node);
    var timeout = opts.timeout === undefined ? (kind === "danger" ? 7000 : 4000) : opts.timeout;
    if (timeout > 0) timer = setTimeout(dismiss, timeout);
    return dismiss;
  }
  function errorToast(err) {
    toast((err && err.message) || "Bir hata oluştu / Something went wrong", { kind: "danger" });
  }

  // ------------------------------------------------------------- confirm modal
  /** confirm({title, body, confirmText, cancelText, danger}) -> Promise<boolean> */
  function confirmDialog(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var titleId = "kui-modal-" + Math.random().toString(36).slice(2);
      var ok = el("button", { class: "btn " + (opts.danger ? "btn-danger" : "btn-primary"), type: "button", value: "ok",
        text: opts.confirmText || "Onayla / Confirm" });
      var cancel = el("button", { class: "btn", type: "button", value: "cancel", text: opts.cancelText || "Vazgeç / Cancel" });
      var dialog = el("dialog", { class: "modal", "aria-labelledby": titleId },
        el("div", { class: "modal-body" },
          el("h2", { id: titleId, text: opts.title || "Emin misiniz? / Are you sure?" }),
          opts.body ? el("p", { text: opts.body }) : null),
        el("div", { class: "modal-foot" }, cancel, ok));
      var done = false;
      function finish(value) {
        if (done) return;
        done = true;
        if (dialog.open) dialog.close();
        dialog.remove();
        resolve(value);
      }
      ok.addEventListener("click", function () { finish(true); });
      cancel.addEventListener("click", function () { finish(false); });
      dialog.addEventListener("cancel", function (e) { e.preventDefault(); finish(false); });
      dialog.addEventListener("click", function (e) { if (e.target === dialog) finish(false); });
      document.body.appendChild(dialog);
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
      (opts.danger ? cancel : ok).focus();
    });
  }

  // ------------------------------------------------------------- forms
  /** formData(form): {name: value}; inputs with data-type="number"|"bool" are converted; empty optional fields skipped */
  function formData(form) {
    var out = {};
    $all("input, select, textarea", form).forEach(function (f) {
      if (!f.name || f.disabled) return;
      var type = f.getAttribute("data-type");
      if (f.type === "checkbox") { out[f.name] = f.checked; return; }
      if (f.type === "radio") { if (f.checked) out[f.name] = f.value; return; }
      var v = f.value;
      if (v === "" && !f.required) return;
      out[f.name] = type === "number" ? Number(v) : type === "bool" ? v === "true" : v;
    });
    return out;
  }
  /** setErrors(form, {field: "message"}) — shows messages in [data-error-for=field] and sets aria-invalid */
  function setErrors(form, errors) {
    errors = errors || {};
    $all("[aria-invalid]", form).forEach(function (f) { f.removeAttribute("aria-invalid"); });
    $all("[data-error-for]", form).forEach(function (n) { n.textContent = ""; });
    Object.keys(errors).forEach(function (name) {
      var field = form.querySelector('[name="' + name.replace(/"/g, "") + '"]');
      var slot = form.querySelector('[data-error-for="' + name.replace(/"/g, "") + '"]');
      if (field) field.setAttribute("aria-invalid", "true");
      if (slot) slot.textContent = errors[name];
    });
  }
  /** busy(button, promise) — spinner + disabled while the promise runs; returns the promise */
  function busy(button, promise) {
    if (!button) return promise;
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    return Promise.resolve(promise).finally(function () {
      button.removeAttribute("aria-busy");
      button.disabled = false;
    });
  }

  // ------------------------------------------------------------- polling
  /** poll(fn, 5000) — calls fn now and every interval while the tab is visible; returns stop() */
  function poll(fn, interval, opts) {
    opts = opts || {};
    var timer = null, stopped = false, running = false;
    function tick() {
      if (stopped) return;
      if (document.hidden) { schedule(); return; }
      if (running) { schedule(); return; }
      running = true;
      Promise.resolve().then(fn).catch(function (e) { if (opts.onError) opts.onError(e); })
        .finally(function () { running = false; schedule(); });
    }
    function schedule() { if (!stopped) timer = setTimeout(tick, interval); }
    function onVisible() { if (!document.hidden && !stopped) { clearTimeout(timer); tick(); } }
    document.addEventListener("visibilitychange", onVisible);
    if (opts.immediate === false) schedule(); else tick();
    return function stop() { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }

  // ------------------------------------------------------------- clipboard
  /** copyText(text) -> Promise<boolean>: navigator.clipboard, falls back to a hidden textarea + execCommand */
  function copyText(text) {
    function fallback() {
      var area = el("textarea", { class: "visually-hidden", readonly: true, "aria-hidden": "true" });
      area.value = text;
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      return ok;
    }
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallback(); });
    }
    return Promise.resolve(fallback());
  }

  /** showOnce(secret, {title, note, label}) -> Promise: dialog that shows a just-created secret masked
      (reveal on demand), copies it, and forgets it when closed. Never stores it anywhere. */
  function showOnce(secret, opts) {
    opts = opts || {};
    var value = String(secret || "");
    var masked = value.length > 8 ? value.slice(0, 4) + "••••••••" + value.slice(-4) : "••••••••";
    return new Promise(function (resolve) {
      var box = el("div", { class: "secret-once", text: masked, "aria-live": "polite" });
      var reveal = el("button", { class: "btn btn-ghost", type: "button", text: "Göster", "aria-pressed": "false" });
      var copyBtn = el("button", { class: "btn", type: "button", text: "Kopyala" });
      var done = el("button", { class: "btn btn-primary", type: "button", text: "Kaydettim, kapat" });
      var dialog = el("dialog", { class: "modal", "aria-label": opts.title || "Gizli değer" },
        el("div", { class: "modal-body" }, el("h2", { text: opts.title || "Yeni gizli değer" }),
          el("p", { text: (opts.note || "Bu değer yalnız şimdi gösterilir.") + " Şimdi güvenli bir yere kaydedin." }), box),
        el("div", { class: "modal-foot" }, reveal, copyBtn, done));
      reveal.addEventListener("click", function () {
        var shown = reveal.getAttribute("aria-pressed") === "true";
        box.textContent = shown ? masked : value;
        reveal.setAttribute("aria-pressed", shown ? "false" : "true");
        reveal.textContent = shown ? "Göster" : "Gizle";
      });
      copyBtn.addEventListener("click", function () {
        copyText(value).then(function (ok) { toast(ok ? (opts.label || "Değer") + " kopyalandı" : "Kopyalanamadı; Göster ile elle seçin", { kind: ok ? "success" : "danger" }); });
      });
      function close() {
        value = ""; box.textContent = "";
        if (dialog.open) dialog.close();
        dialog.remove();
        resolve();
      }
      done.addEventListener("click", close);
      dialog.addEventListener("cancel", function (e) { e.preventDefault(); close(); });
      document.body.appendChild(dialog);
      if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
      copyBtn.focus();
    });
  }

  // ------------------------------------------------------------- formatting
  function number(n, digits) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return Number(n).toLocaleString(locale(), { maximumFractionDigits: digits === undefined ? 2 : digits });
  }
  function duration(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    var d = Math.floor(seconds / 86400), h = Math.floor(seconds % 86400 / 3600), m = Math.floor(seconds % 3600 / 60);
    if (d) return d + "g " + h + "s";
    if (h) return h + "s " + m + "dk";
    if (m) return m + "dk";
    return seconds + "sn";
  }
  function timeAgo(ts) {
    var diff = Date.now() / 1000 - Number(ts);
    if (!isFinite(diff)) return "—";
    if (diff < 60) return "az önce";
    if (diff < 3600) return Math.floor(diff / 60) + " dk önce";
    if (diff < 86400) return Math.floor(diff / 3600) + " sa önce";
    return new Date(ts * 1000).toLocaleDateString();
  }

  // ------------------------------------------------------------- shell wiring
  function syncThemeButtons() {
    var dark = currentTheme() === "dark";
    $all("[data-theme-toggle]").forEach(function (b) {
      b.setAttribute("aria-pressed", dark ? "true" : "false");
      b.setAttribute("aria-label", dark ? "Açık temaya geç / Light theme" : "Koyu temaya geç / Dark theme");
      var use = b.querySelector("use");
      if (use) use.setAttribute("href", iconHref(dark ? "sun" : "moon"));
    });
  }
  /** Marks the current menu item: by path, and on hash-routed pages also by location.hash.
      Rules on the current path: links whose #route matches the hash (exactly or as a prefix
      segment, "#/bots" matches "#/bots/42") win; otherwise links without a hash; otherwise, when
      there is no hash at all, the first hash link (the default route). */
  function markNav() {
    var here = location.pathname.replace(/index\.html$/, "");
    var hash = location.hash && location.hash !== "#" ? location.hash : "";
    var links = $all(".nav a, .bottombar a").map(function (a) {
      var url = new URL(a.getAttribute("href"), location.href);
      return { a: a, path: url.pathname.replace(/index\.html$/, ""), hash: url.hash && url.hash !== "#" ? url.hash : "" };
    });
    var onPath = links.filter(function (l) { return l.path === here; });
    function matches(l) { return !!hash && !!l.hash && (hash === l.hash || hash.indexOf(l.hash + "/") === 0); }
    var active = onPath.filter(matches);
    if (!active.length) active = onPath.filter(function (l) { return !l.hash; });
    if (!active.length && !hash) {
      var first = onPath.filter(function (l) { return l.hash; })[0];
      if (first) active = onPath.filter(function (l) { return l.hash === first.hash; });
    }
    links.forEach(function (l) {
      if (active.indexOf(l) >= 0) l.a.setAttribute("aria-current", "page");
      else l.a.removeAttribute("aria-current");
    });
  }
  function wireLogout() {
    $all("[data-logout]").forEach(function (b) {
      b.addEventListener("click", function () {
        api("/ui/logout", { method: "POST" }).then(function () { location.assign("/ui/login"); }).catch(errorToast);
      });
    });
  }
  var mePromise = null;
  function me() {
    if (!mePromise) mePromise = api("/me");
    return mePromise;
  }

  /** Sticky container for banners; publishes its height as --kui-banner-offset so sticky top bars sit below it. */
  function bannerHost() {
    var host = $(".kui-banners");
    if (!host) {
      host = el("div", { class: "kui-banners" });
      document.body.insertBefore(host, document.body.firstChild);
      var update = function () { root.style.setProperty("--kui-banner-offset", host.offsetHeight + "px"); };
      if (window.ResizeObserver) new ResizeObserver(update).observe(host);
      window.addEventListener("resize", update);
      setTimeout(update, 0);
    }
    return host;
  }

  /** Persistent red banner while the panel's protection is switched off, with one-click re-enable. */
  function protectionBanner(info) {
    var existing = $(".protection-banner");
    if (info.protection !== false) { if (existing) existing.remove(); return; }
    if (existing) return;
    var button = el("button", { class: "btn btn-sm", type: "button", text: "Korumayı aç / Turn protection on" });
    button.addEventListener("click", function () {
      busy(button, api("/auth/protection", { method: "POST", body: { enabled: true } })
        .then(function () { location.reload(); }).catch(errorToast));
    });
    var host = bannerHost();
    host.insertBefore(el("div", { class: "protection-banner", role: "alert" }, icon("alert"),
      el("span", { text: "Koruma kapalı: bu paneli linki bilen herkes açabilir. / Protection is off: anyone with the link can open this panel." }),
      button), host.firstChild);
  }

  /** Persistent configuration hints from /ui/api/me "warnings" (e.g. UI_ALLOWED_HOSTS unset). */
  function configBanner(info) {
    var warnings = (info && info.warnings) || [];
    if (!warnings.length || $(".config-banner")) return;
    var text = warnings.indexOf("allowed_hosts_unset") >= 0
      ? "Kimlik doğrulamayı sıkılaştırmak için UI_ALLOWED_HOSTS ayarlayın (servisin dış adresi). / Set UI_ALLOWED_HOSTS to tighten identity checks."
      : warnings.join(", ");
    bannerHost().appendChild(el("div", { class: "config-banner", role: "status" }, icon("info"), el("span", { text: text })));
  }

  /** Fills [data-user], [data-title], [data-stack] from /ui/api/me; hides [data-logout] unless password mode. */
  function loadMe() {
    return me().then(function (me) {
      $all("[data-title]").forEach(function (n) { n.textContent = me.title; });
      $all("[data-stack-version]").forEach(function (n) { n.textContent = "v" + me.stack.version; });
      $all("[data-user]").forEach(function (n) { n.textContent = me.user.email || me.user.id; });
      $all("[data-logout]").forEach(function (n) { n.hidden = me.user.method !== "password"; });
      protectionBanner(me);
      configBanner(me);
      if (me.title) document.title = document.title.replace("{title}", me.title);
      return me;
    });
  }

  // ------------------------------------------------------------- images: calm placeholder instead of the browser's
  // broken-image glyph. A tile shows while an image loads (slow network); a failed image stays an empty tile.
  function watchImage(img) {
    if (img.getAttribute("data-kui-img")) return;
    img.setAttribute("data-kui-img", "1");
    img.classList.add("kui-img");
    function ok() { img.classList.remove("is-loading", "is-failed"); img.classList.add("is-ready"); }
    function fail() { img.classList.remove("is-loading"); img.classList.add("is-failed"); }
    if (img.complete && img.naturalWidth > 0) return ok();
    if (img.complete && img.getAttribute("src")) return fail();
    img.classList.add("is-loading");
    img.addEventListener("load", ok, { once: true });
    img.addEventListener("error", fail, { once: true });
  }
  function watchImages(scope) {
    var list = (scope.tagName === "IMG") ? [scope] : (scope.querySelectorAll ? scope.querySelectorAll("img") : []);
    Array.prototype.forEach.call(list, watchImage);
  }
  function startImageWatch() {
    watchImages(document.body);
    if (!window.MutationObserver) return;
    new MutationObserver(function (records) {
      records.forEach(function (r) { Array.prototype.forEach.call(r.addedNodes, function (n) { if (n.nodeType === 1) watchImages(n); }); });
    }).observe(document.body, { childList: true, subtree: true });
  }

  document.addEventListener("DOMContentLoaded", function () {
    startImageWatch();
    translateTree(document.body);
    syncThemeButtons();
    $all("[data-theme-toggle]").forEach(function (b) {
      b.addEventListener("click", function () { setTheme(currentTheme() === "dark" ? "light" : "dark"); });
    });
    markNav();
    window.addEventListener("hashchange", markNav);
    wireLogout();
    // every signed-in page shows the protection-off banner, even pages that never call loadMe()
    if (!document.body.hasAttribute("data-kui-public")) me().then(function (m) { protectionBanner(m); configBanner(m); }).catch(function () {});
  });

  // ------------------------------------------------------------- password fields
  /** passwordField(input) -> wrapper element: the input with an eye button inside the field (show / hide).
   *  Every <input type="password"> gets one automatically; opt out with data-no-eye. */
  function eyeState(wrap, on) {
    var input = wrap.querySelector("input"), btn = wrap.querySelector(".pw-eye");
    if (!input || !btn) return;
    input.type = on ? "text" : "password";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    var label = tr(on ? "Parolayı gizle" : "Parolayı göster");
    btn.setAttribute("aria-label", label);
    btn.setAttribute("title", label);
    btn.replaceChild(icon(on ? "eye-off" : "eye"), btn.firstChild);
  }
  function hideAllEyes(scope) {
    $all(".pw-wrap", scope || document).forEach(function (w) {
      var b = w.querySelector(".pw-eye");
      if (b && b.getAttribute("aria-pressed") === "true") eyeState(w, false);
    });
  }
  var eyesBound = false;
  function passwordField(input) {
    var parent = input.parentNode;
    if (parent && parent.classList && parent.classList.contains("pw-wrap")) return parent;
    var wrap = el("span", { class: "pw-wrap" });
    var btn = el("button", { class: "pw-eye", type: "button", "aria-pressed": "false",
      "aria-label": "Parolayı göster", title: "Parolayı göster" }, icon("eye"));
    btn.addEventListener("mousedown", function (e) { e.preventDefault(); });   // keep focus and caret in the field
    btn.addEventListener("click", function () {
      var pos = [input.selectionStart, input.selectionEnd];
      eyeState(wrap, input.type === "password");
      input.focus();
      try { input.setSelectionRange(pos[0], pos[1]); } catch (e) { /* not selectable */ }
    });
    if (!eyesBound) {   // one listener for the whole page: hide every revealed field on submit and when leaving
      eyesBound = true;
      document.addEventListener("submit", function (e) { hideAllEyes(e.target); }, true);
      window.addEventListener("pagehide", function () { hideAllEyes(); });
    }
    var focused = document.activeElement === input;
    if (parent) parent.insertBefore(wrap, input);
    wrap.appendChild(input);
    wrap.appendChild(btn);
    if (focused) input.focus();   // moving a node drops focus
    return wrap;
  }
  function enhancePasswords(root) {
    $all('input[type="password"]:not([data-no-eye])', root || document).forEach(function (input) {
      if (input.parentNode && !input.parentNode.classList.contains("pw-wrap")) passwordField(input);
    });
  }
  document.addEventListener("DOMContentLoaded", function () {
    enhancePasswords(document);
    if (!window.MutationObserver) return;
    new MutationObserver(function (list) {
      list.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType !== 1) return;
          if (n.matches && n.matches('input[type="password"]')) enhancePasswords(n.parentNode);
          else if (n.querySelector) enhancePasswords(n);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  });

  window.KUI = {
    api: api, ApiError: ApiError, cookie: cookie,
    toast: toast, errorToast: errorToast, confirm: confirmDialog,
    formData: formData, setErrors: setErrors, busy: busy, poll: poll,
    el: el, passwordField: passwordField, icon: icon, iconHref: iconHref, $: $, $all: $all, clear: clear,
    number: number, duration: duration, timeAgo: timeAgo,
    setTheme: setTheme, theme: currentTheme, loadMe: loadMe, me: me, markNav: markNav,
    protectionBanner: protectionBanner, copyText: copyText, copy: copyText, showOnce: showOnce,
    t: tr, lang: function () { return lang; }, setLang: setLang, langChosen: langChosen, translateTree: translateTree, locale: locale
  };
})();
