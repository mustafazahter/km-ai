/* Ayarlar: safe settings that override env vars (UI > env > default), write-only secrets,
   read-only security locks, audit log. Every save is applied to the running bot. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;
  var SECRET_LABELS = { TELEGRAM_BOT_TOKEN: "Telegram bot token", LLM_API_KEY: "LLM API anahtarı",
    TAVILY_API_KEY: "Tavily anahtarı", BRAVE_API_KEY: "Brave anahtarı", SERPER_API_KEY: "Serper anahtarı" };

  var SECRET_HELP = { TELEGRAM_BOT_TOKEN: "telegram-token", LLM_API_KEY: "llm-key", TAVILY_API_KEY: "search-tavily",
    BRAVE_API_KEY: "search-brave", SERPER_API_KEY: "search-serper" };

  function control(item) {
    var sc = item.schema, v = item.value, input;
    var id = "set-" + item.name;
    if (sc.type === "bool") {
      input = h("input", { class: "switch", type: "checkbox", name: item.name, role: "switch" });
      input.checked = !!v;
    } else if (sc.type === "choice") {
      input = h("select", { class: "select", name: item.name }, sc.choices.map(function (c) {
        return h("option", { value: c, selected: c === v ? true : null, text: c });
      }));
    } else if (item.name === "SYSTEM_PROMPT_EXTRA") {
      input = h("textarea", { class: "textarea", name: item.name, rows: "6", maxlength: "4000" });
      input.value = v || "";
    } else {
      var num = sc.type === "int" || sc.type === "float";
      input = h("input", { class: "input", name: item.name, type: num ? "number" : "text", "data-type": num ? "number" : null,
        step: sc.type === "float" ? "any" : (num ? "1" : null), min: sc.min === null ? null : String(sc.min),
        max: sc.max === null ? null : String(sc.max), autocomplete: "off", spellcheck: "false",
        inputmode: num ? "decimal" : null });
      input.value = v === null || v === undefined ? "" : String(v);
    }
    input.id = id;
    input.setAttribute("data-initial", sc.type === "bool" ? String(!!v) : (v === null || v === undefined ? "" : String(v)));
    return input;
  }

  function currentValue(input) {
    if (input.type === "checkbox") return String(input.checked);
    return input.value;
  }

  function row(item, reload) {
    var input = control(item);
    var reset = item.source === "ui" ? A.button("", { kind: "ghost", size: "sm", icon: "undo", cls: "btn-icon", aria: item.name + " ortam/varsayılan değerine dön" }) : null;
    if (reset) reset.addEventListener("click", function () {
      KUI.busy(reset, KUI.api("/config/" + item.name, { method: "DELETE" }).then(function (r) {
        KUI.toast(item.name + " ortam/varsayılan değerine döndü.", { kind: "success" }); reload();
      }).catch(KUI.errorToast));
    });
    var wide = item.name === "SYSTEM_PROMPT_EXTRA";
    return h("div", { class: "setting-row" + (wide ? " is-wide" : "") },
      h("div", { class: "label-col" },
        h("div", { class: "cluster" }, h("label", { for: input.id, text: item.schema.description || item.name }), A.sourceBadge(item.source)),
        h("code", { text: item.name + (item.schema.min !== null && item.schema.max !== null ? " · " + item.schema.min + "–" + item.schema.max : "") })),
      h("div", { class: "control-col" }, input, reset || h("span", { class: "reset-slot", "aria-hidden": "true" })),
      h("span", { class: "error-text", "data-error-for": item.name }));
  }

  function groupCard(key, label, items, reload) {
    var form = h("form", { class: "form", novalidate: true });
    items.forEach(function (it) { form.appendChild(row(it, reload)); });
    var save = A.button("Değişiklikleri kaydet", { kind: "primary", type: "submit" });
    var bar = h("div", { class: "form-bar" }, save);
    form.appendChild(bar);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      KUI.setErrors(form, {});
      var values = {};
      KUI.$all("[data-initial]", form).forEach(function (input) {
        var now = currentValue(input);
        if (now === input.getAttribute("data-initial")) return;
        values[input.name] = input.type === "checkbox" ? input.checked : (input.getAttribute("data-type") === "number" ? Number(now) : now);
      });
      if (!Object.keys(values).length) { KUI.toast("Değişiklik yok.", { kind: "info" }); return; }
      KUI.busy(save, KUI.api("/config/batch", { method: "POST", body: { values: values } }).then(function (r) {
        KUI.toast(A.applyNote(r.applied), { kind: "success" });
        reload();
      }).catch(function (err) { A.errorsFrom(err, form); }));
    });
    return A.card(label, null, form);
  }

  function secretsCard(secrets, reload) {
    var list = h("ul", { class: "list" }, secrets.map(function (s) {
      var sec = A.secretInput("value", "Yeni değer");
      sec.input.id = "sec-" + s.name;
      var editor = h("div", { class: "stack-sm", hidden: true },
        h("label", { class: "visually-hidden", for: sec.input.id, text: SECRET_LABELS[s.name] }), sec.group);
      var save = A.button("Kaydet", { kind: "primary", size: "sm" });
      var cancel = A.button("Vazgeç", { size: "sm" });
      var edit = A.button(s.set ? "Değiştir" : "Ekle", { size: "sm", icon: "key" });
      var del = s.source === "ui" ? A.button("", { kind: "ghost", size: "sm", icon: "trash", cls: "btn-icon", aria: SECRET_LABELS[s.name] + " sil" }) : null;
      var actions = h("div", { class: "cluster" }, edit, del);
      var editActions = h("div", { class: "cluster", hidden: true }, save, cancel);
      editor.appendChild(editActions);
      edit.addEventListener("click", function () { editor.hidden = false; editActions.hidden = false; actions.hidden = true; sec.input.focus(); });
      cancel.addEventListener("click", function () { editor.hidden = true; actions.hidden = false; sec.input.value = ""; });
      save.addEventListener("click", function () {
        var v = sec.input.value.trim();
        if (!v) { KUI.toast("Değer boş olamaz.", { kind: "danger" }); return; }
        KUI.busy(save, KUI.api("/secrets/" + s.name, { method: "PUT", body: { value: v } }).then(function (r) {
          sec.input.value = "";
          KUI.toast(A.applyNote(r.applied), { kind: "success" }); reload();
        }).catch(KUI.errorToast));
      });
      if (del) del.addEventListener("click", function () {
        KUI.confirm({ title: SECRET_LABELS[s.name] + " silinsin mi?", body: "Arayüzde kaydedilen değer silinir. Ortam değişkeni varsa o kullanılır.", confirmText: "Sil", danger: true })
          .then(function (ok) {
            if (!ok) return;
            KUI.busy(del, KUI.api("/secrets/" + s.name, { method: "DELETE" }).then(function (r) {
              KUI.toast(A.applyNote(r.applied), { kind: "success" }); reload();
            }).catch(KUI.errorToast));
          });
      });
      return h("li", { class: "stack-sm" },
        h("div", { class: "row-between" },
          h("div", { class: "grow" }, h("div", { class: "row" }, h("span", { class: "ink small", text: SECRET_LABELS[s.name] || s.name }),
            SECRET_HELP[s.name] ? A.helpButton(SECRET_HELP[s.name]) : null), A.sourceLine(s, reload)),
          actions),
        editor);
    }));
    return A.card("Gizli değerler", "Yalnız yazılır. Kayıtlı değer bir daha gösterilmez; yalnız son 4 karakter.", list,
      { action: A.icon("lock", "icon-lg muted") });
  }


  // ------------------------------------------------------------ panel protection (stack_ui /auth API)
  function protectionCard(reload) {
    var body = h("div", { class: "stack" }, A.skeleton(3));
    var card = A.card("Panel koruması", "Parola, oturumlar ve koruma anahtarı", body, { cls: "protection-card" });
    card.id = "koruma";
    KUI.api("/auth").then(function (a) {
      KUI.clear(body);
      var on = a.protection.enabled;
      body.appendChild(h("dl", { class: "kv" },
        h("dt", { text: "Koruma" }), h("dd", null, on ? A.badge("Açık", "success", "success") : A.badge("KAPALI", "danger", "danger")),
        h("dt", { text: "Giriş yöntemi" }), h("dd", { text: a.mode === "komuta" ? "Komuta girişi" : a.mode === "auto" ? "Komuta veya parola" : "Parola" }),
        h("dt", { text: "Parola" }), h("dd", { text: { env: "UI_PASSWORD ortam değişkeni", stored: "Panelde (scrypt özeti)", none: "Yok" }[a.password.source] })));

      if (!on) {
        var enable = A.button("Korumayı yeniden aç", { kind: "primary", icon: "shield", onclick: function () {
          KUI.busy(enable, KUI.api("/auth/protection", { method: "POST", body: { enabled: true } }).then(function () {
            KUI.toast("Koruma açıldı.", { kind: "success" }); location.reload();
          }).catch(KUI.errorToast));
        } });
        body.appendChild(A.callout("danger", "Koruma kapalı: linki bilen herkes paneli açabilir, ayarları ve sırları değiştirebilir."));
        body.appendChild(h("div", { class: "cluster" }, enable));
      }

      // password change
      if (a.password.can_change) {
        var cur = h("input", { class: "input", type: "password", id: "pw-cur", autocomplete: "current-password" });
        var nw = h("input", { class: "input", type: "password", id: "pw-new-s", autocomplete: "new-password", minlength: String(a.password.min_length) });
        var change = A.button("Parolayı değiştir", { icon: "key", onclick: function () {
          var b = { "new": nw.value };
          if (a.password.source === "stored") b.current = cur.value;
          KUI.busy(change, KUI.api("/auth/password", { method: "POST", body: b }).then(function () {
            cur.value = ""; nw.value = "";
            KUI.toast("Parola değişti; diğer tüm oturumlar kapatıldı.", { kind: "success" });
          }).catch(KUI.errorToast));
        } });
        body.appendChild(h("div", { class: "stack-sm" },
          h("span", { class: "label", text: "Parolayı değiştir" }),
          a.password.source === "stored" ? A.field("pw-cur", "Mevcut parola", cur) : null,
          A.field("pw-new-s", "Yeni parola", nw, "En az " + a.password.min_length + " karakter. Değişince diğer oturumlar düşer."),
          h("div", { class: "cluster" }, change)));
      } else if (a.password.source === "env") {
        body.appendChild(A.callout("info", "Parola UI_PASSWORD ortam değişkeninden geliyor; yalnız orada değişir."));
      }

      if (a.user.method !== "unprotected" && a.mode !== "komuta") {
        var revoke = A.button("Tüm oturumları kapat", { size: "sm", icon: "logout", onclick: function () {
          KUI.confirm({ title: "Tüm oturumlar kapatılsın mı?", body: "Siz dahil herkes yeniden giriş yapmak zorunda kalır.", confirmText: "Kapat", cancelText: "Vazgeç", danger: true })
            .then(function (ok) {
              if (!ok) return;
              KUI.busy(revoke, KUI.api("/auth/sessions/revoke", { method: "POST" }).then(function () { location.assign("/ui/login"); }).catch(KUI.errorToast));
            });
        } });
        body.appendChild(h("div", { class: "row-between" }, h("span", { class: "small muted", text: "Başka bir cihazda açık kalan oturumları sonlandırır." }), revoke));
      }

      // turning protection off: password + phrase + warnings + acceptance
      if (on && !a.protection.forced && a.password.source !== "none" && a.user.method !== "unprotected") {
        var pw = h("input", { class: "input", type: "password", id: "off-pw", autocomplete: "current-password" });
        var phrase = h("input", { class: "input mono", id: "off-phrase", autocomplete: "off", spellcheck: "false", placeholder: a.protection.disable_phrase });
        var accept = h("input", { type: "checkbox", id: "off-accept" });
        var off = A.button("Korumayı kapat", { kind: "danger", icon: "alert" });
        function ready() { off.disabled = !(accept.checked && phrase.value.trim() === a.protection.disable_phrase && pw.value); }
        [pw, phrase].forEach(function (x) { x.addEventListener("input", ready); });
        accept.addEventListener("change", ready);
        ready();
        off.addEventListener("click", function () {
          KUI.busy(off, KUI.api("/auth/protection", { method: "POST", body: { enabled: false, password: pw.value, confirm: phrase.value.trim() } })
            .then(function () { KUI.toast("Koruma kapatıldı. Her sayfada kırmızı bant görünecek.", { kind: "danger" }); location.reload(); })
            .catch(KUI.errorToast));
        });
        body.appendChild(h("details", { class: "danger-zone" },
          h("summary", { text: "Korumayı kapat (önerilmez)" }),
          h("div", { class: "stack-sm" },
            h("ul", { class: "warn-list" },
              h("li", { text: "Linki bilen herkes paneli parolasız açar." }),
              h("li", { text: "Token, anahtar ve izin listesi dahil tüm ayarlar değiştirilebilir; köprü açılıp token üretilebilir." }),
              h("li", { text: "Kapalıyken her sayfada kırmızı bant görünür, her açılışta günlüğe uyarı yazılır." }),
              h("li", { text: "Yalnız güvenilir, kapalı bir ağda kullanın. UI_FORCE_PROTECTION=1 bu seçeneği tamamen kapatır." })),
            A.field("off-pw", "Mevcut parola", pw),
            A.field("off-phrase", "Onay için yazın: " + a.protection.disable_phrase, phrase),
            h("label", { class: "check", for: "off-accept" }, accept, h("span", { text: "Riskleri okudum, panelin herkese açık olacağını kabul ediyorum." })),
            h("div", { class: "cluster" }, off))));
      } else if (a.protection.forced) {
        body.appendChild(A.callout("info", "UI_FORCE_PROTECTION=1: koruma kapatılamaz."));
      }
      if (location.hash.indexOf("/koruma") >= 0) card.scrollIntoView({ block: "start" });
    }).catch(function (e) { KUI.clear(body); body.appendChild(A.errorBox(e)); });
    return card;
  }

  function locksCard(locks) {
    return A.card("Güvenlik kilitleri", "Kodda sabit; arayüzden kapatılamaz.", h("ul", { class: "list lock-list" }, locks.map(function (l) {
      return h("li", null, A.icon("shield"), h("div", { class: "grow" }, h("div", { class: "ink small", text: l.title }),
        h("div", { class: "small muted", text: l.text })), A.badge("Kilitli", "outline"));
    })));
  }

  function auditCard() {
    var list = h("ul", { class: "list" }, h("li", null, A.skeleton(1)));
    KUI.api("/config/audit?limit=12").then(function (r) {
      KUI.clear(list);
      if (!r.audit.length) { list.appendChild(h("li", { class: "small muted", text: "Henüz değişiklik yok." })); return; }
      r.audit.forEach(function (a) {
        var val = a.action === "reset" ? "varsayılana döndü" : JSON.stringify(a.new);
        list.appendChild(h("li", { class: "log-line" }, A.badge(a.action === "reset" ? "geri" : "ayar", "outline"),
          h("span", { class: "msg", text: a.name + " → " + (val && val.length > 80 ? val.slice(0, 80) + "…" : val) }),
          h("span", { class: "meta", text: KUI.timeAgo(a.ts) + " · " + (a.actor || "?") })));
      });
    }).catch(function (e) { KUI.clear(list); list.appendChild(h("li", null, A.errorBox(e))); });
    return A.card("Son değişiklikler", "Kim, ne zaman, ne değiştirdi.", list);
  }

  var TAB_ORDER = [
    { id: "model", label: "Model", icon: "spark", groups: ["llm"] },
    { id: "yanit", label: "Yanıt ve araçlar", icon: "tool", groups: ["agent"] },
    { id: "arama", label: "Arama", icon: "search", groups: ["search"] },
    { id: "hafiza", label: "Hafıza ve görevler", icon: "brain", groups: ["memory"] },
    { id: "telegram", label: "Telegram", icon: "send", groups: ["telegram"] },
    { id: "guvenlik", label: "Güvenlik", icon: "shield", special: "security" },
    { id: "anahtarlar", label: "Gizli değerler", icon: "key", special: "secrets" },
    { id: "gecmis", label: "Değişiklik geçmişi", icon: "clock", special: "audit" }
  ];

  function render(main, route) {
    var sub = route.sub[0];
    if (sub === "koruma") sub = "guvenlik";
    var active = TAB_ORDER.filter(function (t) { return t.id === sub; })[0] || TAB_ORDER[0];
    main.appendChild(A.pageHead("Yapılandırma", "Ayarlar",
      "Burada kaydedilen değer ortam değişkenini ezer (arayüz > ortam > varsayılan) ve anında çalışan bota uygulanır."));
    var nav = h("nav", { class: "settings-nav", "aria-label": "Ayar bölümleri" }, TAB_ORDER.map(function (t) {
      return h("a", { href: "#/ayarlar/" + t.id, "aria-current": t.id === active.id ? "page" : null }, A.icon(t.icon), h("span", { text: t.label }));
    }));
    var pane = h("div", { class: "settings-pane stack gap-lg" }, A.skeleton(6));
    main.appendChild(h("div", { class: "settings" }, nav, pane));
    function load() {
      return KUI.api("/config").then(function (d) {
        KUI.clear(pane);
        if (active.special === "security") {
          pane.appendChild(protectionCard(load));
          pane.appendChild(locksCard(d.locks));
        } else if (active.special === "secrets") {
          pane.appendChild(secretsCard(d.secrets, load));
        } else if (active.special === "audit") {
          pane.appendChild(auditCard());
        } else {
          var groups = {};
          d.settings.forEach(function (x) {
            if (/^SYSTEM_PROMPT_(?!EXTRA$)/.test(x.name)) return;  // per-channel prompts live on the channel pages
            (groups[x.group] = groups[x.group] || []).push(x); });
          active.groups.forEach(function (k) { if (groups[k]) pane.appendChild(groupCard(k, d.groups[k], groups[k], load)); });
        }
      }).catch(function (e) { KUI.clear(pane); pane.appendChild(A.errorBox(e)); });
    }
    var active_nav = nav.querySelector("[aria-current]");
    if (active_nav && active_nav.scrollIntoView) active_nav.scrollIntoView({ block: "nearest", inline: "center" });
    load();
  }

  A.pages.settings = { title: "Ayarlar", render: render };
})();
