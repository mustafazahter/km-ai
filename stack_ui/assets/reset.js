/* Forgotten password: reset code from the service log + new admin password. */
(function () {
  "use strict";
  var MESSAGES = {
    invalid_reset_code: "Sıfırlama kodu yanlış ya da süresi dolmuş. Sayfayı yenileyip günlükteki yeni kodu kullanın. / Wrong or expired reset code: reload this page and use the new code from the log.",
    weak_password: "Parola zayıf. / Password too weak.",
    password_mismatch: "Parolalar aynı değil. / Passwords do not match.",
    rate_limited: "Çok fazla deneme. Biraz bekleyin. / Too many attempts.",
    reset_locked: "Çok fazla yanlış kod; biraz bekleyin. / Too many wrong codes: wait a while.",
    reset_unavailable: "Parola sıfırlama bu kurulumda yok (parola UI_PASSWORD ortam değişkeninden geliyor). / Password reset is not available here.",
    csrf_failed: "İstek reddedildi; sayfayı yenileyin. / Request refused, reload.",
    request_failed: "İstek tamamlanamadı. Sayfayı yenileyip tekrar deneyin. / The request failed. Reload the page and try again."
  };
  function show(code, message) {
    // server texts are English-only except the weak-password one; unknown shapes (proxy pages, HTML) still say something
    var text = (code === "weak_password" ? message : null) || MESSAGES[code] || message || code ||
      "İstek tamamlanamadı. Sayfayı yenileyip tekrar deneyin. / The request failed. Reload the page and try again.";
    document.getElementById("reset-error-text").textContent = text;
    document.getElementById("reset-error").hidden = false;
  }
  /** error code and message from any answer; a body without our error shape (gateway page, redirect) gets the HTTP status. */
  function problem(res, body) {
    var err = body && body.error;
    if (err && typeof err === "object") return { code: err.code, message: err.message };
    if (typeof err === "string") return { code: null, message: err };
    return { code: null, message: "İstek tamamlanamadı (HTTP " + res.status + "). Oturum süresi dolmuş olabilir; sayfayı yenileyin. / "
      + "The request failed (HTTP " + res.status + "). Your session may have expired; reload the page." };
  }
  function nextTarget(params) {
    var next = params.get("next") || "/ui/";
    if (next.indexOf("/ui/") !== 0 || next.indexOf("//") === 0 || /[\\\s]/.test(next) || /^\/ui\/(login|logout|setup|api\/)/.test(next)) next = "/ui/";
    var hash = location.hash;
    if (hash && hash.length < 256 && /^#[A-Za-z0-9\-._~!$&'()*+,;=:@\/?%]*$/.test(hash)) next = next.split("#")[0] + hash;
    return next;
  }
  document.addEventListener("DOMContentLoaded", function () {
    var params = new URLSearchParams(location.search);
    var fromAddress = params.get("error");   // only known codes are worded; arbitrary text from a link is never shown
    if (fromAddress) show(MESSAGES[fromAddress] ? fromAddress : "request_failed");
    document.getElementById("reset-next").value = nextTarget(params);
    var form = document.getElementById("reset-form");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var data = { code: form.code.value, password: form.password.value, password2: form.password2.value, next: nextTarget(params) };
      if (data.password !== data.password2) { show("password_mismatch"); return; }
      var req = fetch("/ui/reset", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(data)
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (res.ok && body.ok) { location.assign(body.next || "/ui/"); return; }
          var err = problem(res, body);
          show(err.code, err.message);
        });
      }).catch(function () { show("csrf_failed"); });
      window.KUI.busy(form.querySelector("button[type=submit]"), req);
    });
  });
})();
