/* Access page: password, sessions, protection switch, bridge gate. Uses /ui/api/auth* and /ui/api/bridge. */
(function () {
  "use strict";
  var K = window.KUI, el = K.el;
  var SURFACES = { bridge: ["Köprü", "/bridge/*"], mcp: ["MCP", "/mcp"], hook: ["Webhook", "/hook/*"] };

  function showOnce(title, value, note) {
    return K.showOnce(value, { title: title, note: note, label: title });
  }

  function loadAuth() {
    return K.api("/auth").then(function (a) {
      K.$("#auth-mode").textContent = "Mod: " + a.mode + " · parola: " + ({ env: "ortam değişkeni", stored: "panelde (scrypt)", none: "yok" })[a.password.source];
      K.$("#password-form").hidden = !a.password.can_change;
      K.$("#current-field").hidden = a.password.source !== "stored";
      K.$("#password-env").hidden = a.password.source !== "env";
      var on = a.protection.enabled;
      var badge = K.$("#protection-badge");
      badge.className = "badge " + (on ? "badge-success" : "badge-danger");
      badge.textContent = on ? "açık" : "kapalı";
      K.$("#protection-off-form").hidden = !on || a.protection.forced || a.password.source === "none";
      K.$("#protection-forced").hidden = !a.protection.forced;
      K.$("#protection-on").hidden = on;
    });
  }

  function loadGate() {
    return K.api("/bridge").then(renderGate).catch(function (err) {
      if (err.status === 404) { K.$("#gate-card").hidden = true; var qc = K.$("#quick-connect"); if (qc) qc.hidden = true; return; }
      K.errorToast(err);
    });
  }

  function renderGate(g) {
    var list = K.clear(K.$("#gate-surfaces"));
    Object.keys(SURFACES).forEach(function (name) {
      var id = "gate-" + name;
      var sw = el("input", { class: "switch", type: "checkbox", role: "switch", id: id });
      sw.checked = !!g.surfaces[name];
      sw.addEventListener("change", function () { change({ action: "set", surface: name, enabled: sw.checked }); });
      list.appendChild(el("li", {},
        el("label", { class: "check spacer", for: id }, sw, el("span", {}, el("strong", { text: SURFACES[name][0] }), " ", el("code", { text: SURFACES[name][1] }))),
        el("span", { class: "badge badge-outline", text: g.sources[name] })));
    });
    var tok = K.clear(K.$("#token-state"));
    tok.appendChild(el("span", { class: g.env_token ? "badge badge-success" : "badge badge-outline", text: "env " + (g.env_token ? "var" : "yok") }));
    tok.appendChild(el("span", { class: g.panel_token.set ? "badge badge-success" : "badge badge-outline", text: "panel " + (g.panel_token.set ? "var" : "yok") }));
    if (g.generated_token) tok.appendChild(el("span", { class: "badge badge-danger", text: "geçici (log)" }));
    var hook = K.clear(K.$("#hook-state"));
    hook.appendChild(el("span", { class: g.env_hook_secret ? "badge badge-success" : "badge badge-outline", text: "env " + (g.env_hook_secret ? "var" : "yok") }));
    hook.appendChild(el("span", { class: g.panel_hook_secret.set ? "badge badge-success" : "badge badge-outline", text: "panel " + (g.panel_hook_secret.set ? "var" : "yok") }));
    renderAudit(g.audit || []);
  }

  function change(body) {
    return K.api("/bridge", { method: "POST", body: body }).then(function (res) {
      if (res.token) {
        showOnce("Yeni köprü token'ı", res.token, "Sunucu yalnız özetini saklar; bir daha görüntülenemez.");
        document.dispatchEvent(new CustomEvent("kui:fresh-token"));
      }
      if (res.hook_secret) showOnce("Yeni webhook sırrı", res.hook_secret, "HMAC doğrulaması için sunucuda korumalı dosyada (izin 600) saklanır ama hiçbir ekranda tekrar gösterilmez.");
      K.toast("Kaydedildi", { kind: "success" });
      return loadGate();
    }).catch(function (err) { K.errorToast(err); return loadGate(); });
  }

  var authAudit = [];
  function renderAudit(gateAudit) {
    var rows = authAudit.concat(gateAudit).sort(function (a, b) { return b.ts - a.ts; }).slice(0, 15);
    var list = K.clear(K.$("#audit"));
    if (!rows.length) list.appendChild(el("li", { class: "muted", text: "Kayıt yok." }));
    rows.forEach(function (r) {
      list.appendChild(el("li", {}, el("span", { class: "t-mono small spacer", text: r.action }),
        el("span", { class: "small muted", text: r.actor || "—" }), el("span", { class: "small muted num", text: K.timeAgo(r.ts) })));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    K.loadMe().catch(K.errorToast);
    K.api("/auth/audit").then(function (a) { authAudit = a.audit; }).catch(function () {}).finally(function () { loadAuth().catch(K.errorToast); loadGate(); });

    var pwForm = K.$("#password-form");
    pwForm.addEventListener("submit", function (e) {
      e.preventDefault();
      K.setErrors(pwForm, {});
      var data = K.formData(pwForm);
      K.busy(pwForm.querySelector("[type=submit]"), K.api("/auth/password", { method: "POST", body: { current: data.current || "", new: data.new || "" } })
        .then(function () { pwForm.reset(); K.toast("Parola değişti; diğer oturumlar kapandı", { kind: "success" }); return loadAuth(); })
        .catch(function (err) { if (err.code === "weak_password") K.setErrors(pwForm, { new: err.message }); else K.errorToast(err); }));
    });
    K.$("#revoke-sessions").addEventListener("click", function () {
      K.confirm({ title: "Tüm oturumlar kapatılsın mı?", body: "Bu cihaz dahil herkes yeniden giriş yapmak zorunda kalır.", confirmText: "Kapat", danger: true })
        .then(function (yes) { if (yes) K.api("/auth/sessions/revoke", { method: "POST" }).then(function () { location.assign("/ui/login"); }).catch(K.errorToast); });
    });
    var protForm = K.$("#protection-off-form");
    protForm.addEventListener("submit", function (e) {
      e.preventDefault();
      K.setErrors(protForm, {});
      var data = K.formData(protForm);
      K.busy(protForm.querySelector("[type=submit]"), K.api("/auth/protection", { method: "POST", body: { enabled: false, password: data.password || "", confirm: data.confirm || "" } })
        .then(function () { location.reload(); })
        .catch(function (err) { if (err.code === "confirm_phrase") K.setErrors(protForm, { confirm: err.message }); else K.errorToast(err); }));
    });
    K.$("#protection-enable").addEventListener("click", function (e) {
      K.busy(e.currentTarget, K.api("/auth/protection", { method: "POST", body: { enabled: true } }).then(function () { location.reload(); }).catch(K.errorToast));
    });
    K.$("#rotate-token").addEventListener("click", function () {
      K.confirm({ title: "Yeni köprü token'ı üretilsin mi?", body: "Panelde üretilmiş eski token hemen geçersiz olur; bağlı istemcileri güncellemeniz gerekir.", confirmText: "Üret" })
        .then(function (yes) { if (yes) change({ action: "rotate_token" }); });
    });
    K.$("#revoke-token").addEventListener("click", function () {
      K.confirm({ title: "Panel token'ı iptal edilsin mi?", body: "Ortam değişkenindeki BRIDGE_TOKEN etkilenmez.", confirmText: "İptal et", danger: true })
        .then(function (yes) { if (yes) change({ action: "revoke_token" }); });
    });
    K.$("#rotate-hook").addEventListener("click", function () {
      K.confirm({ title: "Yeni webhook sırrı üretilsin mi?", body: "Panelde üretilmiş eski sır hemen geçersiz olur.", confirmText: "Üret" })
        .then(function (yes) { if (yes) change({ action: "rotate_hook_secret" }); });
    });
    K.$("#revoke-hook").addEventListener("click", function () {
      K.confirm({ title: "Panel webhook sırrı iptal edilsin mi?", confirmText: "İptal et", danger: true })
        .then(function (yes) { if (yes) change({ action: "revoke_hook_secret" }); });
    });
  });
})();
