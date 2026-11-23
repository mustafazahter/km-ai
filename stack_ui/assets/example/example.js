/* Example overview page. Copy this file as a starting point for a stack page.
   Uses only window.KUI (komuta-ui.js) and the built-in APIs:
   GET /ui/api/me, GET /ui/api/status, and — when the stack mounts them —
   /ui/api/settings (settings_router) and /ui/api/secrets (secrets_router). */
(function () {
  "use strict";
  var K = window.KUI;
  var el = K.el;

  function scopeBadge(tool) {
    var cls = tool.scope === "read" ? "badge" : tool.scope === "trade" ? "badge badge-danger" : "badge badge-accent";
    return el("span", { class: "row" }, el("span", { class: cls, text: tool.scope }),
      tool.confirm ? el("span", { class: "badge badge-outline", text: "onaylı" }) : null);
  }

  function renderStatus(s) {
    K.$("#stat-tools").textContent = K.number(s.tools.length, 0);
    var confirmCount = s.tools.filter(function (t) { return t.confirm; }).length;
    K.$("#stat-tools-foot").textContent = confirmCount + " tanesi onay ister";
    K.$("#stat-events").textContent = K.number(s.events.length, 0);
    K.$("#stat-webhooks").textContent = K.number(s.webhooks.delivered, 0);
    K.$("#stat-webhooks-foot").textContent = s.webhooks.targets + " hedef · " + s.webhooks.failed + " başarısız";
    K.$("#stat-uptime").textContent = K.duration(s.stack.uptime);
    K.$("#stat-plugins").textContent = "v" + s.stack.version;

    var errors = K.$("#plugin-errors");
    errors.hidden = !s.plugin_errors.length;
    K.clear(K.$("#plugin-error-list"));
    s.plugin_errors.forEach(function (e) {
      K.$("#plugin-error-list").appendChild(el("li", {}, el("code", { text: e.plugin }), " ", e.error));
    });

    var tbody = K.clear(K.$("#tools"));
    if (!s.tools.length) {
      tbody.appendChild(el("tr", {}, el("td", { colspan: "3" },
        el("div", { class: "empty" }, K.icon("tool"), el("strong", { text: "Henüz araç yok" }),
          el("span", { text: "plugins/ klasörüne bir dosya ekleyin." })))));
    }
    s.tools.forEach(function (t) {
      tbody.appendChild(el("tr", {},
        el("td", { class: "t-mono", text: t.name }),
        el("td", {}, scopeBadge(t)),
        el("td", { class: "muted hide-mobile", text: t.description || "—" })));
    });

    var list = K.clear(K.$("#events"));
    if (!s.events.length) {
      list.appendChild(el("li", {}, el("div", { class: "empty" }, K.icon("activity"),
        el("strong", { text: "Olay yok" }), el("span", { text: "emit() çağrıldığında burada görünür." }))));
    }
    s.events.slice(0, 8).forEach(function (e) {
      list.appendChild(el("li", {},
        el("span", { class: "dot dot-accent" }),
        el("span", { class: "t-mono small spacer", text: e.event }),
        el("span", { class: "small muted num", text: K.timeAgo(e.ts) })));
    });
  }

  // ------------------------------------------------------------- settings
  function settingField(item) {
    var s = item.schema, id = "set-" + s.name, input;
    if (s.type === "bool") {
      input = el("input", { class: "switch", type: "checkbox", id: id, name: s.name, role: "switch" });
      input.checked = !!item.value;
    } else if (s.type === "choice") {
      input = el("select", { class: "select", id: id, name: s.name },
        s.choices.map(function (c) { var o = el("option", { value: c, text: c }); if (c === item.value) o.selected = true; return o; }));
    } else {
      input = el("input", { class: "input", id: id, name: s.name, type: s.type === "str" ? "text" : "number",
        "data-type": s.type === "str" ? null : "number", value: item.value === null || item.value === undefined ? "" : item.value,
        min: s.min, max: s.max, step: s.type === "float" ? "any" : null });
    }
    var source = item.source === "ui" ? el("span", { class: "badge badge-accent", text: "panel" })
      : item.source === "env" ? el("span", { class: "badge", text: "env" }) : el("span", { class: "badge badge-outline", text: "varsayılan" });
    var save = el("button", { class: "btn", type: "button", text: "Kaydet" });
    var reset = item.source === "ui" ? el("button", { class: "btn btn-ghost btn-icon", type: "button",
      "aria-label": s.name + " ortam değerine döndür", title: "Sıfırla" }, K.icon("refresh")) : null;
    var control = s.type === "bool"
      ? el("div", { class: "row-between" }, el("label", { class: "check" }, input, el("span", { class: "muted", text: s.description || "" })), el("div", { class: "row" }, reset, save))
      : el("div", { class: "input-group" }, input, reset, save);
    var field = el("div", { class: "field" },
      el("div", { class: "row-between" }, el("label", { class: "label t-mono", for: id, text: s.name }), source),
      control,
      s.type !== "bool" && s.description ? el("span", { class: "hint", text: s.description }) : null,
      el("span", { class: "error-text", "data-error-for": s.name }));
    save.addEventListener("click", function () {
      var form = K.$("#settings-form");
      var value = K.formData(field)[s.name];
      if (s.type === "bool") value = input.checked;
      K.setErrors(form, {});
      K.busy(save, K.api("/settings/" + encodeURIComponent(s.name), { method: "PUT", body: { value: value } })
        .then(function () { K.toast(s.name + " kaydedildi", { kind: "success" }); return loadSettings(); })
        .catch(function (err) {
          if (err.code === "invalid_value") { var e = {}; e[s.name] = err.message; K.setErrors(form, e); }
          else K.errorToast(err);
        }));
    });
    if (reset) reset.addEventListener("click", function () {
      K.busy(reset, K.api("/settings/" + encodeURIComponent(s.name), { method: "DELETE" })
        .then(function () { K.toast(s.name + " ortam değerine döndü"); return loadSettings(); }).catch(K.errorToast));
    });
    return field;
  }

  function loadSettings() {
    var form = K.$("#settings-form");
    return K.api("/settings").then(function (data) {
      K.clear(form);
      if (!data.settings.length) form.appendChild(el("p", { class: "muted", text: "Ayar tanımlı değil." }));
      data.settings.forEach(function (item) { form.appendChild(settingField(item)); });
    }).catch(function (err) {
      if (err.status === 404) {
        K.clear(form).appendChild(el("div", { class: "empty" }, K.icon("sliders"), el("strong", { text: "Bu stack ayar sunmuyor" }),
          el("span", { text: "settings_router(...) ile eklenir." })));
      } else K.errorToast(err);
    });
  }

  // ------------------------------------------------------------- secrets
  function secretRow(item) {
    var id = "sec-" + item.name;
    var input = el("input", { class: "input", id: id, type: "password", autocomplete: "off", spellcheck: "false",
      placeholder: item.set ? "Yeni değer girin" : "Değer girin" });
    var save = el("button", { class: "btn btn-primary", type: "button", text: "Kaydet" });
    var del = item.source === "ui" ? el("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": item.name + " sil", title: "Sil" }, K.icon("trash")) : null;
    var status = item.set
      ? el("span", { class: "badge badge-success" }, K.icon("check"), item.source === "env" ? "env" : "kayıtlı", item.preview ? " " + item.preview : "")
      : el("span", { class: "badge badge-outline", text: "boş" });
    save.addEventListener("click", function () {
      if (!input.value) { input.focus(); return; }
      K.busy(save, K.api("/secrets/" + encodeURIComponent(item.name), { method: "PUT", body: { value: input.value } })
        .then(function () { input.value = ""; K.toast(item.name + " kaydedildi", { kind: "success" }); return loadSecrets(); })
        .catch(K.errorToast));
    });
    if (del) del.addEventListener("click", function () {
      K.confirm({ title: item.name + " silinsin mi?", body: "Panelde kayıtlı değer silinir. Ortam değişkeni varsa o kullanılır.",
        confirmText: "Sil", danger: true }).then(function (yes) {
        if (!yes) return;
        K.api("/secrets/" + encodeURIComponent(item.name), { method: "DELETE" })
          .then(function () { K.toast(item.name + " silindi"); return loadSecrets(); }).catch(K.errorToast);
      });
    });
    return el("li", { class: "stack-sm" },
      el("div", { class: "row-between" }, el("label", { class: "label t-mono", for: id, text: item.name }), status),
      el("div", { class: "input-group" }, input, save, del));
  }

  function loadSecrets() {
    var list = K.$("#secrets");
    return K.api("/secrets").then(function (data) {
      K.clear(list);
      data.secrets.forEach(function (item) { list.appendChild(secretRow(item)); });
      if (!data.secrets.length) list.appendChild(el("li", { class: "muted", text: "Gizli değer tanımlı değil." }));
    }).catch(function (err) {
      if (err.status === 404) {
        K.clear(list).appendChild(el("li", {}, el("div", { class: "empty" }, K.icon("key"),
          el("strong", { text: "Bu stack gizli değer sunmuyor" }), el("span", { text: "secrets_router(...) ile eklenir." }))));
      } else K.errorToast(err);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    K.loadMe().catch(K.errorToast);
    var live = K.$("#live");
    var stop = K.poll(function () {
      return K.api("/status").then(function (s) { renderStatus(s); live.hidden = false; });
    }, 10000, { onError: function (e) { live.hidden = true; K.errorToast(e); } });
    K.$("#refresh").addEventListener("click", function (e) {
      K.busy(e.currentTarget, K.api("/status").then(renderStatus).catch(K.errorToast));
    });
    loadSettings();
    loadSecrets();
    window.addEventListener("pagehide", stop);
  });
})();
