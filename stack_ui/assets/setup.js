/* First-time setup: setup code from the service log + new admin password. */
(function () {
  "use strict";
  var MESSAGES = {
    invalid_setup_code: "Kurulum kodu yanlış. / Wrong setup code.",
    weak_password: "Parola zayıf. / Password too weak.",
    password_mismatch: "Parolalar aynı değil. / Passwords do not match.",
    rate_limited: "Çok fazla deneme. Biraz bekleyin. / Too many attempts.",
    setup_locked: "Kurulum kilitlendi; yeni kod için servisi yeniden başlatın. / Locked: restart for a new code.",
    setup_done: "Kurulum zaten tamam. / Setup already done.",
    csrf_failed: "İstek reddedildi; sayfayı yenileyin. / Request refused, reload."
  };
  function show(code, message) {
    document.getElementById("setup-error-text").textContent = message || MESSAGES[code] || code;
    document.getElementById("setup-error").hidden = false;
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
    if (params.get("error")) show(params.get("error"));
    document.getElementById("setup-next").value = nextTarget(params);
    var form = document.getElementById("setup-form");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var data = { code: form.code.value, password: form.password.value, password2: form.password2.value, next: nextTarget(params) };
      if (data.password !== data.password2) { show("password_mismatch"); return; }
      var req = fetch("/ui/setup", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(data)
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (res.ok && body.ok) { location.assign(body.next || "/ui/"); return; }
          var err = body.error || {};
          show(err.code, err.code === "weak_password" ? err.message : null);
        });
      }).catch(function () { show("csrf_failed"); });
      window.KUI.busy(form.querySelector("button[type=submit]"), req);
    });
  });
})();
