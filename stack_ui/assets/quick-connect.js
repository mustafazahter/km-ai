/* Quick-connect card: copy a prompt that lets Claude Code / another agent set up the MCP connection.
   Usage: <div data-quick-connect></div> + <script src="/ui/_kit/quick-connect.js" defer></script>
   or var qc = KUI.quickConnect(element, {enabled: true, token: null, client: "claude-code"}).
   opts.enabled=false shows "bridge/MCP closed" and disables copying; opts.token = a token the page has just
   received (e.g. from its own rotate call) enables "copy with token" without a server round trip.
   The returned controller has refresh(), setEnabled(bool), setToken(token|null). After a new bridge token is created in this session, dispatch
   document.dispatchEvent(new CustomEvent("kui:fresh-token")) to offer the "with token" copy (one-shot).
   Needs the /ui/api/bridge/quickstart endpoint (mount_ui bridge_panel=True). */
(function () {
  "use strict";
  var K = window.KUI, el = K.el;
  var CLIENTS = [["claude-code", "Claude Code"], ["claude-desktop", "Claude Desktop"], ["generic", "Genel / curl"], ["n8n", "n8n"]];

  function mask(text) { return text.replace(/Bearer [A-Za-z0-9_\-]{20,}/g, "Bearer ••••••••"); }

  // Put a client-side token into a token-less prompt (used when the page itself holds a fresh token).
  function embedToken(prompt, token) {
    return prompt.split("\n").map(function (line) {
      if (line.indexOf("- <TOKEN> bir yer tutucudur") === 0) {
        return "- Token bu metne dahil edildi: onu ASLA ekrana yazma, echo etme, dosyaya veya repoya koyma; hemen bir ortam değişkenine veya sır yöneticisine taşı.";
      }
      return line.split("<TOKEN>").join(token);
    }).join("\n");
  }

  function quickConnect(root, opts) {
    opts = opts && typeof opts === "object" ? opts : {};
    if (!root) return null;
    if (root.__kuiQuickConnect) {
      var existing = root.__kuiQuickConnect;
      if ("enabled" in opts) existing.setEnabled(opts.enabled !== false);
      if ("token" in opts) existing.setToken(opts.token || null);
      return existing;
    }
    root.setAttribute("data-qc-ready", "1");
    var client = opts.client || root.getAttribute("data-client") || "claude-code";
    var enabled = opts.enabled !== false;
    var localToken = opts.token || null;
    var tabs = el("div", { class: "segmented qc-tabs", role: "group", "aria-label": "İstemci" });
    var preview = el("pre", { class: "qc-preview", tabindex: "0", "aria-label": "Prompt önizlemesi" });
    var urlNote = el("div", { class: "callout", hidden: true }, K.icon("info"), el("span", { text: "Adres istek başlığından türetildi; doğrulamak için UI_PUBLIC_URL veya UI_ALLOWED_HOSTS ayarlayın." }));
    var copyPlain = el("button", { class: "btn btn-primary", type: "button", text: "Prompt'u kopyala (token'sız)" });
    var copyToken = el("button", { class: "btn btn-danger", type: "button", text: "Prompt'u kopyala (token dahil)" });
    var tokenBox = el("div", { class: "qc-token", hidden: true }, copyToken,
      el("span", { class: "qc-warn", text: "Token ajanın sohbet geçmişine girer; güvenmediğin ajana verme. Yalnız bir kez kopyalanabilir." }));

    CLIENTS.forEach(function (c) {
      var b = el("button", { type: "button", "aria-pressed": c[0] === client ? "true" : "false", text: c[1] });
      b.addEventListener("click", function () {
        client = c[0];
        K.$all("button", tabs).forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
        load();
      });
      tabs.appendChild(b);
    });

    function fetchPrompt(withToken) {
      return K.api("/bridge/quickstart?client=" + encodeURIComponent(client) + "&with_token=" + (withToken ? 1 : 0));
    }
    var current = "";  // token-less prompt, kept so "copy" runs synchronously inside the click (clipboard rules)
    var closedNote = el("div", { class: "callout callout-danger", hidden: true }, K.icon("alert"),
      el("span", { text: "Köprü/MCP yüzeyi kapalı: önce açın, sonra prompt'u kopyalayın." }));
    function load() {
      closedNote.hidden = enabled;
      if (!enabled) { copyPlain.disabled = true; tokenBox.hidden = true; }
      return fetchPrompt(false).then(function (r) {
        current = r.prompt;
        copyPlain.disabled = !enabled;
        preview.textContent = mask(r.prompt);
        urlNote.hidden = !!r.url_verified;
        tokenBox.hidden = !enabled || !(r.fresh_token_available || localToken);
      }).catch(function (err) {
        preview.textContent = err.status === 404 ? "Köprü paneli bu stack'te kapalı." : (err.message || "Yüklenemedi");
        copyPlain.disabled = true;
      });
    }
    function copy(text, label) {
      return K.copyText(text).then(function (ok) {
        K.toast(ok ? label + " panoya kopyalandı" : "Kopyalanamadı; önizlemeden elle seçin", { kind: ok ? "success" : "danger" });
      });
    }
    copyPlain.addEventListener("click", function () {
      if (current) copy(current, "Prompt");
    });
    // The token prompt is fetched once (the server forgets the token), then copied from a dialog so the
    // clipboard write happens inside a fresh click.
    copyToken.addEventListener("click", function () {
      K.busy(copyToken, fetchPrompt(true).then(function (r) {
        tokenBox.hidden = true;  // one-shot
        var text = r.token_included ? r.prompt : (localToken ? embedToken(r.prompt, localToken) : "");
        localToken = null;
        if (!text) { K.toast("Token artık mevcut değil; token'sız prompt kullanın", { kind: "danger" }); return; }
        var go = el("button", { class: "btn btn-danger", type: "button", text: "Panoya kopyala" });
        var close = el("button", { class: "btn", type: "button", text: "Kapat" });
        var dialog = el("dialog", { class: "modal", "aria-label": "Token'lı prompt" },
          el("div", { class: "modal-body" }, el("h2", { text: "Token'lı prompt hazır" }),
            el("p", { text: "Bu prompt gerçek token'ı içerir ve bir kez verilir. Kopyaladıktan sonra pencereyi kapatın." }),
            el("pre", { class: "qc-preview", text: mask(text) }),
            el("span", { class: "qc-warn", text: "Token ajanın sohbet geçmişine girer; güvenmediğin ajana verme." })),
          el("div", { class: "modal-foot" }, close, go));
        function done() { text = ""; if (dialog.open) dialog.close(); dialog.remove(); }
        go.addEventListener("click", function () { copy(text, "Token'lı prompt").then(done); });
        close.addEventListener("click", done);
        dialog.addEventListener("cancel", function (e) { e.preventDefault(); done(); });
        document.body.appendChild(dialog);
        dialog.showModal();
        go.focus();
      }).catch(K.errorToast));
    });
    document.addEventListener("kui:fresh-token", load);

    K.clear(root).appendChild(el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("div", {}, el("h2", { text: "Hızlı bağlantı" }),
          el("p", { class: "card-sub", text: "Prompt'u kopyalayıp Claude Code'a (veya başka bir ajana) yapıştırın; ajan MCP bağlantısını kendisi kurar." })),
        K.icon("message", "icon-lg muted")),
      tabs, closedNote, urlNote, preview, el("div", { class: "qc-actions" }, copyPlain, tokenBox)));
    load();
    var controller = {
      refresh: load,
      setEnabled: function (value) { enabled = value !== false; return load(); },
      setToken: function (token) { localToken = token || null; return load(); }
    };
    root.__kuiQuickConnect = controller;
    return controller;
  }

  K.quickConnect = quickConnect;
  document.addEventListener("DOMContentLoaded", function () { K.$all("[data-quick-connect]").forEach(function (node) { quickConnect(node); }); });
})();
