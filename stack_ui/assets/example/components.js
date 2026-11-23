/* Component gallery wiring (demo only). */
(function () {
  "use strict";
  var K = window.KUI;
  document.addEventListener("DOMContentLoaded", function () {
    K.loadMe().catch(function () {});
    K.$("#demo-toast").addEventListener("click", function () { K.toast("Ayarlar kaydedildi", { kind: "success" }); });
    K.$("#demo-error").addEventListener("click", function () { K.toast("Bağlantı kurulamadı", { kind: "danger" }); });
    K.$("#demo-confirm").addEventListener("click", function () {
      K.confirm({ title: "Bot durdurulsun mu?", body: "Açık emirler etkilenmez.", confirmText: "Durdur", danger: true })
        .then(function (yes) { K.toast(yes ? "Durduruldu" : "Vazgeçildi"); });
    });
    K.$all(".segmented button").forEach(function (b) {
      b.addEventListener("click", function () {
        K.$all("button", b.parentNode).forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
      });
    });
    var form = K.$("#demo-form");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var data = K.formData(form);
      if (!data.name || !/^[a-z0-9-]+$/.test(data.name)) { K.setErrors(form, { name: "Küçük harf, rakam ve tire kullanın." }); return; }
      K.setErrors(form, {});
      K.toast("Kaydedildi: " + data.name, { kind: "success" });
    });
  });
})();
