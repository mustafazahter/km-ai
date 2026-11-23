/* Password sign-in (UI_AUTH=password). Works without JS too (plain form post). */
(function () {
  "use strict";
  var MESSAGES = {
    invalid_password: "Parola yanlış. / Wrong password.",
    rate_limited: "Çok fazla deneme. Biraz bekleyin. / Too many attempts, wait a moment.",
    csrf_failed: "İstek reddedildi. Sayfayı yenileyin. / Request refused, reload the page.",
    ui_disabled: "Parolayla giriş kapalı. / Password sign-in is disabled."
  };
  function show(code) {
    var box = document.getElementById("login-error");
    document.getElementById("login-error-text").textContent = MESSAGES[code] || MESSAGES.invalid_password;
    box.hidden = false;
    var input = document.getElementById("password");
    input.setAttribute("aria-invalid", "true");
    input.focus();
    input.select();
  }
  // Where to go after sign-in: ?next=/ui/... (same-origin /ui/ paths only) plus the original #route.
  function nextTarget(params) {
    var next = params.get("next") || "/ui/";
    if (next.indexOf("/ui/") !== 0 || next.indexOf("//") === 0 || /[\\\s]/.test(next) || /^\/ui\/(login|logout|api\/)/.test(next)) next = "/ui/";
    var hash = location.hash;
    if (hash && hash.length < 256 && /^#[A-Za-z0-9\-._~!$&'()*+,;=:@\/?%]*$/.test(hash)) next = next.split("#")[0] + hash;
    return next;
  }
  document.addEventListener("DOMContentLoaded", function () {
    var params = new URLSearchParams(location.search);
    var hidden = document.getElementById("login-next");
    if (hidden) hidden.value = nextTarget(params);
    if (params.get("error")) show(params.get("error"));
    var form = document.getElementById("login-form");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var password = document.getElementById("password").value;
      if (!password) { show("invalid_password"); return; }
      var button = form.querySelector("button[type=submit]");
      var req = fetch("/ui/login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ password: password, next: nextTarget(params) })
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (res.ok && data.ok) { location.assign(data.next || "/ui/"); return; }
          show((data.error && data.error.code) || "invalid_password");
        });
      }).catch(function () { show("invalid_password"); });
      window.KUI.busy(button, req);
    });
  });
})();
