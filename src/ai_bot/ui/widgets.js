/* Shared widgets used by more than one page: chat allow-list with live chat id capture, Telegram token
   form, LLM provider picker with model list. Text always via textContent (KUI.el). */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  // ------------------------------------------------------------ allowed chats + live capture
  /** Returns {node}; polls /telegram/chats every 3 s while the page is open (stopped on leave). */
  function chatManager(onChange) {
    var allowedBox = h("div", { class: "chat-list" });
    var seenBox = h("ul", { class: "list" });
    var listen = h("div", { class: "listen" });
    var source = h("div");
    var manual = h("input", { class: "input", name: "chat_id", id: "chat-manual", inputmode: "numeric",
      placeholder: "örn. 123456789 veya -100123…", autocomplete: "off" });
    var lastSeen = [], lastRunning = false, lastAllowed = [];

    function changed(r) { draw(r.allowed, lastSeen, lastRunning); refreshSource(); if (onChange) onChange(); }
    function add(id, btn) {
      return KUI.busy(btn, KUI.api("/telegram/chats", { method: "POST", body: { chat_id: id } }).then(function (r) {
        KUI.toast("Sohbet izin listesine eklendi; hemen geçerli.", { kind: "success" });
        changed(r);
      }).catch(KUI.errorToast));
    }
    function remove(id, btn) {
      KUI.confirm({ title: "Sohbet çıkarılsın mı?", body: id + " artık botu kullanamaz. Liste boşalırsa sohbet araçları kapanır.",
        confirmText: "Çıkar", cancelText: "Vazgeç", danger: true }).then(function (ok) {
        if (!ok) return;
        KUI.busy(btn, KUI.api("/telegram/chats/" + encodeURIComponent(id), { method: "DELETE" }).then(changed).catch(KUI.errorToast));
      });
    }
    function refreshSource() {
      KUI.api("/config").then(function (d) {
        var f = null;
        d.settings.forEach(function (x) { if (x.name === "ALLOWED_CHAT_IDS") f = x; });
        if (!f) return;
        KUI.clear(source);
        source.appendChild(A.sourceLine({ name: "ALLOWED_CHAT_IDS", secret: false, source: f.source, env_set: f.env_set },
          function () { refreshSource(); if (onChange) onChange(); }));
      }).catch(function () {});
    }

    function draw(allowed, seen, running) {
      lastSeen = seen; lastRunning = running; lastAllowed = allowed;
      KUI.clear(allowedBox);
      if (!allowed.length) allowedBox.appendChild(A.badge("İzin listesi boş: bot yalnız /id ve /start'a yanıt verir", "outline"));
      allowed.forEach(function (id) {
        var x = h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", "aria-label": id + " sohbetini çıkar" }, A.icon("x"));
        x.addEventListener("click", function () { remove(id, x); });
        allowedBox.appendChild(h("span", { class: "chat-pill" }, String(id), x));
      });
      KUI.clear(listen);
      if (running) {
        listen.appendChild(h("span", { class: "dot dot-success dot-live" }));
        listen.appendChild(h("span", { text: "Dinleniyor. Botunuza Telegram'dan bir mesaj gönderin (ör. /id); gönderen sohbet birkaç saniye içinde aşağıda belirir." }));
      } else {
        listen.appendChild(A.icon("alert"));
        listen.appendChild(h("span", { text: "Telegram bağlantısı kapalı. Önce bot token'ını kaydedin." }));
      }
      KUI.clear(seenBox);
      if (!seen.length) seenBox.appendChild(h("li", { class: "small muted", text: "Henüz mesaj gelmedi." }));
      seen.forEach(function (c) {
        var label = c.name || (c.username ? "@" + c.username : "Sohbet");
        var isAllowed = allowed.indexOf(c.chat_id) >= 0;
        var btn = isAllowed ? A.badge("İzinli", "success", "success") : A.button("İzin ver", { kind: "primary", size: "sm", icon: "plus" });
        if (!isAllowed) btn.addEventListener("click", function () { add(c.chat_id, btn); });
        seenBox.appendChild(h("li", null,
          h("div", { class: "grow" }, h("div", { class: "ink small ellipsis", text: label }),
            h("div", { class: "mono-sm", text: c.chat_id + " · " + (c.type || "?") + " · " + KUI.timeAgo(c.last_seen) })),
          btn));
      });
    }

    var stop = KUI.poll(function () {
      return KUI.api("/telegram/chats").then(function (r) { draw(r.allowed, r.seen, r.running); });
    }, 3000);
    A.onLeave(stop);
    refreshSource();

    var addBtn = A.button("Ekle", { icon: "plus" });
    addBtn.addEventListener("click", function () {
      var v = manual.value.trim();
      if (!/^-?\d{1,20}$/.test(v)) { KUI.toast("Sohbet kimliği bir tam sayıdır.", { kind: "danger" }); return; }
      add(v, addBtn).then(function () { manual.value = ""; });
    });

    return {
      node: h("div", { class: "stack" },
        h("div", { class: "stack-sm" }, h("span", { class: "label", text: "İzinli sohbetler" }), allowedBox, source),
        listen,
        h("div", { class: "stack-sm" }, h("span", { class: "label", text: "Bota yazan sohbetler" }), seenBox),
        h("div", { class: "field" }, h("label", { for: "chat-manual", text: "Elle ekle" }),
          h("div", { class: "input-group" }, manual, addBtn),
          h("span", { class: "hint", text: "Kimliği bilmiyorsanız bota /id yazın. Grup kimlikleri eksiyle başlar. Panelden eklenen liste ALLOWED_CHAT_IDS ortam değişkenini ezer." })))
    };
  }

  // ------------------------------------------------------------ Telegram token
  /** Token form: write-only input, test (getMe), save (+reconnect), source line. opts.onSaved() */
  function telegramToken(field, opts) {
    opts = opts || {};
    var sec = A.secretInput("token", "123456789:AA…");
    sec.input.id = "tg-token";
    var result = h("div");
    var srcBox = h("div");
    function show(kind, text) { KUI.clear(result); result.appendChild(A.callout(kind, text)); }
    function drawSource(f) { KUI.clear(srcBox); srcBox.appendChild(A.sourceLine(f, function () { if (opts.onSaved) opts.onSaved(); })); }
    drawSource(field);

    var hasStored = !!(field && field.set);
    var action = A.button(hasStored ? "Doğrula" : "Kaydet ve bağlan", { kind: "primary", trail: "arrow-right" });
    sec.input.addEventListener("input", function () {
      var label = sec.input.value.trim() || !hasStored ? "Kaydet ve bağlan" : "Doğrula";
      for (var n = action.firstChild; n; n = n.nextSibling) if (n.nodeType === 3) { n.nodeValue = label; break; }
    });
    action.addEventListener("click", function () {
      var token = sec.input.value.trim();
      if (!token && !hasStored) { show("danger", "Önce token'ı yapıştırın."); return; }
      var p;
      if (!token) {
        p = KUI.api("/telegram/test", { method: "POST", body: {} }).then(function (t) { show("success", "@" + (t.bot.username || "?") + " doğrulandı."); });
      } else {
        // check first (nothing is stored for a wrong token), then save + reconnect
        p = KUI.api("/telegram/test", { method: "POST", body: { token: token } }).then(function (t) {
          return KUI.api("/secrets/TELEGRAM_BOT_TOKEN", { method: "PUT", body: { value: token } }).then(function (r) {
            sec.input.value = ""; hasStored = true;
            drawSource({ name: "TELEGRAM_BOT_TOKEN", secret: true, set: true, preview: r.secret.preview, source: "ui",
              env_set: field && field.env_set });
            KUI.toast(A.applyNote(r.applied), { kind: "success" });
            show("success", "@" + (t.bot.username || "?") + " bağlandı.");
          });
        });
      }
      KUI.busy(action, p.then(function () { return opts.onSaved ? opts.onSaved(true) : null; }).catch(function (e) { show("danger", e.message); }));
    });
    return h("div", { class: "stack" },
      h("div", { class: "field" }, A.labelWithHelp("tg-token", "Bot token", "telegram-token"), sec.group, srcBox,
        h("span", { class: "hint", text: "Önce Telegram'a sorulup doğrulanır; yanlış token kaydedilmez. Değer günlüğe ve bu sayfaya yazılmaz." })),
      h("div", { class: "cluster" }, action), result);
  }

  // ------------------------------------------------------------ LLM provider + model list
  /** Provider cards, base URL, key, "Bağlan" (fetch model list), searchable model combobox, save + test. */
  function llmPicker(state, opts) {
    opts = opts || {};
    var providers = state.llm_providers;
    var byId = {};
    providers.forEach(function (p) { byId[p.id] = p; });
    var current = byId[state.llm_provider] || byId.custom;
    var fields = {};
    (state.llm_fields || []).forEach(function (f) { fields[f.name] = f; });
    var recent = state.recent_models || [];

    var base = h("input", { class: "input", id: "llm-base", name: "base_url", value: state.values.LLM_BASE_URL, autocomplete: "off", spellcheck: "false", inputmode: "url" });
    var sec = A.secretInput("api_key", "Anahtar (yalnız yazılır)");
    sec.input.id = "llm-key";
    var hint = h("span", { class: "hint" });
    var keyField = h("div", { class: "field" });
    var result = h("div");
    var models = [];
    var combo = modelCombo(state.values.LLM_MODEL || "", recent);
    var listInfo = h("span", { class: "hint", text: "Model listesi otomatik gelir; gelmezse model adını elle yazın." });
    var tiles = [];
    var baseHint = h("span", { class: "hint" });
    var priv = h("input", { type: "checkbox", id: "llm-private" });
    priv.checked = !!(state.values.LLM_ALLOW_PRIVATE_NETWORK);
    var privBox = h("div", { class: "stack-sm" },
      h("label", { class: "check", for: "llm-private" }, priv,
        h("span", { text: "Sunucu yerel/özel ağda (localhost, 10.x, 192.168.x). Link-local, bulut metadata ve bu servisin kendi portu yine engelli." })));

    var baseField = h("div", { class: "field" }, h("label", { for: "llm-base", text: "Adres (Base URL)" }), base, baseHint,
      A.sourceLine(fields.LLM_BASE_URL || null, function () { if (opts.onSaved) opts.onSaved(false); }));
    function isCloud(p) { return !p.local && !p.custom; }
    function drawBase() {
      var locked = isCloud(current);
      base.readOnly = locked;
      base.setAttribute("aria-readonly", locked ? "true" : "false");
      baseHint.textContent = locked ? "Bu sağlayıcının adresi sabittir (ağ politikası). Başka bir adres için 'Özel' kartını seçin." :
        current.custom ? "Herkese açık bir adres yazın; yerel/özel ağ için aşağıdaki kutuyu işaretleyin." :
        "Yerel sunucu: localhost ve özel ağ adreslerine izin var.";
      privBox.hidden = !current.custom;
      baseField.hidden = locked;  // a fixed cloud address is not something to edit: keep the form short
    }

    function show(kind, text) { KUI.clear(result); result.appendChild(A.callout(kind, text)); }
    function drawKey() {
      KUI.clear(keyField);
      keyField.appendChild(A.labelWithHelp("llm-key", current.key ? "API anahtarı" : "API anahtarı (isteğe bağlı)", "llm-key", function () { return { provider: current }; }));
      keyField.appendChild(sec.group);
      keyField.appendChild(A.sourceLine(fields.LLM_API_KEY || null, function () { if (opts.onSaved) opts.onSaved(false); }));
      keyField.appendChild(hint);
    }
    function pick(p, tile) {
      current = p;
      tiles.forEach(function (t) { t.setAttribute("aria-pressed", t === tile ? "true" : "false"); });
      if (p.base_url) base.value = p.base_url;
      else if (p.custom) { base.value = ""; base.focus(); }
      if (p.model) combo.set(p.model);
      models = []; combo.setModels([], recent);
      hint.textContent = p.hint + (p.key ? "" : " Anahtar gerekmez.");
      drawBase();
      if (p.models === "none") listInfo.textContent = "Bu sağlayıcı model listesi sunmuyor; model adını elle yazın.";
      drawKey();
      fetchModels(false);
    }
    var tileBox = h("div", { class: "tiles tiles-provider", role: "group", "aria-label": "Sağlayıcı" }, providers.map(function (p) {
      var t = h("button", { type: "button", class: "tile", "aria-pressed": p.id === current.id ? "true" : "false" },
        p.name, h("small", { text: p.local ? "Yerel · anahtarsız" : p.custom ? "Kendi adresin" : p.kind === "anthropic" ? "Yerel adaptör" : "Anahtarlı" }));
      t.addEventListener("click", function () { pick(p, t); });
      tiles.push(t);
      return t;
    }));
    hint.textContent = current.hint;
    drawKey();
    drawBase();

    function target() {
      var b = { provider: current.id, base_url: base.value.trim() };
      if (sec.input.value.trim()) b.api_key = sec.input.value.trim();
      if (current.custom) b.allow_private = priv.checked;
      return b;
    }
    var seq = 0;  // only the newest request may write into the list (provider/key may change while one is in flight)
    function fetchModels(manual) {
      var mine = ++seq;
      var needKey = current.key && !sec.input.value.trim() && !(fields.LLM_API_KEY && fields.LLM_API_KEY.set && current.id === state.llm_provider);
      if (needKey && !manual) { listInfo.textContent = "Anahtarı girin; model listesi kendiliğinden gelir."; return Promise.resolve(); }
      listInfo.textContent = "Model listesi alınıyor…";
      return KUI.api("/llm/models", { method: "POST", body: target() }).then(function (r) {
        if (mine !== seq) return;
        models = r.models;
        combo.setModels(models, r.recent || recent);
        var priced = models.filter(function (m) { return m.price_in !== null && m.price_in !== undefined; }).length;
        listInfo.textContent = r.count + " model geldi" + (r.count ? ". Arayın ve seçin; araç destekleyenler rozetli" : "") +
          (priced ? "; fiyatlar 1M token başına USD (giriş / çıkış)." : (r.count ? ". Bu sağlayıcı fiyat bilgisi vermiyor." : "."));
        if (manual && r.count) combo.open();
      }).catch(function (e) {
        if (mine !== seq) return;
        listInfo.textContent = "Model listesi alınamadı: " + e.message + " Model adını elle yazabilirsiniz.";
        if (manual) show("danger", e.message);
      });
    }
    var connect = A.button("Yenile", { icon: "refresh", onclick: function () { KUI.busy(connect, fetchModels(true)); } });
    var keyTimer = null;
    sec.input.addEventListener("input", function () {   // list follows the key: no button needed
      clearTimeout(keyTimer);
      if (sec.input.value.trim().length >= 8) keyTimer = setTimeout(function () { fetchModels(false); }, 700);
    });
    base.addEventListener("change", function () { if (!base.readOnly) fetchModels(false); });
    A.onLeave(function () { clearTimeout(keyTimer); seq++; });
    setTimeout(function () { fetchModels(false); }, 0);
    function okText(r) { return r.model + " yanıt verdi (" + r.latency_ms + " ms)" + (r.reply ? ": “" + r.reply + "”" : "."); }
    var test = A.button("Yalnız test et", { icon: "check", onclick: function () {
      var b = target(); b.model = combo.value();
      KUI.busy(test, KUI.api("/llm/test", { method: "POST", body: b })
        .then(function (r) { show("success", okText(r) + (r.saved ? "" : " Henüz kaydedilmedi.")); })
        .catch(function (e) { show("danger", e.message); }));
    } });
    var save = A.button("Kaydet ve test et", { kind: "primary", trail: "arrow-right", onclick: function () {
      var b = target(); b.model = combo.value();
      if (!b.model) { show("danger", "Bir model seçin veya yazın."); return; }
      KUI.busy(save, KUI.api("/llm/save", { method: "POST", body: b }).then(function (r) {
        sec.input.value = "";
        KUI.toast(A.applyNote(r.applied), { kind: "success" });
        return KUI.api("/llm/test", { method: "POST", body: {} }).then(function (t) {
          KUI.toast("Test: " + okText(t), { kind: "success", timeout: 7000 });
        }, function (e) { KUI.toast("Kaydedildi, ama test başarısız: " + e.message, { kind: "danger" }); })
          .then(function () { if (opts.onSaved) opts.onSaved(false); });  // redraw: field sources are now "panel"
      }).catch(function (e) { show("danger", e.message); }));
    } });

    return h("div", { class: "stack" },
      tileBox,
      baseField,
      privBox,
      keyField,
      h("div", { class: "field" }, h("label", { for: "llm-model", text: "Model" }),
        h("div", { class: "input-group" }, combo.node, connect), listInfo,
        A.sourceLine(fields.LLM_MODEL || null, function () { if (opts.onSaved) opts.onSaved(false); })),
      h("div", { class: "cluster" }, save, test),
      result);
  }

  function money(x) { return x === 0 ? "0" : x < 0.01 ? x.toFixed(4).replace(/0+$/, "") : x < 1 ? x.toFixed(3).replace(/0+$/, "").replace(/\.$/, "") : x.toFixed(2).replace(/\.?0+$/, ""); }
  /** "$3 / $15" (USD per 1M tokens, input / output) when the provider publishes prices; "ücretsiz" for 0. */
  function priceBadge(m) {
    if (m.price_in === null || m.price_in === undefined) return null;
    var free = m.price_in === 0 && (m.price_out === 0 || m.price_out === null || m.price_out === undefined);
    var text = free ? "ücretsiz" : "$" + money(m.price_in) + (m.price_out === null || m.price_out === undefined ? "" : " / $" + money(m.price_out));
    return h("span", { class: "badge price-badge" + (free ? " badge-success" : " badge-outline"), title: "1M token başına USD: giriş / çıkış", text: text });
  }

  /** Searchable model list (combobox). Recent models first; tool-calling badge when known. */
  function modelCombo(value, recent) {
    var input = h("input", { class: "input", id: "llm-model", name: "model", role: "combobox", autocomplete: "off", spellcheck: "false",
      "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": "llm-model-list", placeholder: "Model ara veya yaz" });
    input.value = value;
    var list = h("ul", { class: "combo-list", id: "llm-model-list", role: "listbox", hidden: true });
    var all = [], rec = recent || [], active = -1, shown = [], typed = false;
    function render() {
      var q = typed ? input.value.trim().toLowerCase() : "";  // freshly opened: show everything
      var recentItems = rec.filter(function (id) { return !q || id.toLowerCase().indexOf(q) >= 0; })
        .map(function (id) { var m = all.filter(function (x) { return x.id === id; })[0]; return m || { id: id, name: id, tools: null, recent: true }; });
      var rest = all.filter(function (m) { return rec.indexOf(m.id) < 0 && (!q || m.id.toLowerCase().indexOf(q) >= 0 || (m.name || "").toLowerCase().indexOf(q) >= 0); });
      shown = recentItems.map(function (m) { return Object.assign({ recent: true }, m); }).concat(rest).slice(0, 60);
      KUI.clear(list);
      if (!shown.length) { close(); return; }
      shown.forEach(function (m, i) {
        var li = h("li", { role: "option", id: "llm-opt-" + i, class: "combo-item" + (i === active ? " is-active" : ""), "aria-selected": i === active ? "true" : "false" },
          h("span", { class: "grow ellipsis" }, h("span", { class: "tool-name", text: m.id }),
            m.name && m.name !== m.id ? h("span", { class: "small muted", text: " · " + m.name }) : null),
          m.recent ? A.badge("son", "outline") : null,
          priceBadge(m),
          m.tools === true ? A.badge("araç", "accent") : m.tools === false ? A.badge("araçsız", "outline") : null);
        li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(m.id); });
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; input.removeAttribute("aria-activedescendant"); }
    function choose(id) { input.value = id; typed = false; close(); }
    input.addEventListener("input", function () { typed = true; active = -1; render(); });
    input.addEventListener("focus", function () { typed = false; if (all.length || rec.length) render(); });
    input.addEventListener("blur", function () { setTimeout(close, 120); });
    input.addEventListener("keydown", function (e) {
      if (list.hidden && (e.key === "ArrowDown")) { render(); return; }
      if (e.key === "ArrowDown") { active = Math.min(active + 1, shown.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter" && active >= 0 && shown[active]) { choose(shown[active].id); e.preventDefault(); }
      else if (e.key === "Escape") { close(); }
      if (active >= 0) input.setAttribute("aria-activedescendant", "llm-opt-" + active);
    });
    return {
      node: h("div", { class: "combo grow" }, input, list),
      value: function () { return input.value.trim(); },
      set: function (v) { input.value = v; },
      setModels: function (m, r) { all = m || []; rec = r || rec; },
      open: function () { typed = false; input.focus(); render(); }
    };
  }

  /** System-prompt editor card for one setting (SYSTEM_PROMPT_EXTRA or SYSTEM_PROMPT_<CHANNEL>); saves via /config/batch. */
  function promptCard(name, opts) {
    opts = opts || {};
    var max = 4000;
    var area = h("textarea", { class: "textarea prompt-area", id: "prompt-" + name, name: name, rows: "7", maxlength: String(max),
      spellcheck: "false", placeholder: opts.placeholder || "", "aria-label": opts.title });
    var count = h("span", { class: "small muted prompt-count", text: "0 / " + max });
    var save = A.button("Kaydet", { kind: "primary" });
    var clear = A.button("Temizle", { icon: "undo" });
    var saved = "";
    function sync() {
      count.textContent = area.value.length + " / " + max;
      save.disabled = area.value === saved;
      clear.disabled = !area.value;
    }
    area.addEventListener("input", sync);
    function put(value) {
      return KUI.api("/config/batch", { method: "POST", body: { values: (function () { var o = {}; o[name] = value; return o; })() } }).then(function (r) {
        saved = value; area.value = value; sync();
        KUI.toast(value ? A.applyNote(r.applied) : "İstem temizlendi.", { kind: "success" });
      });
    }
    save.addEventListener("click", function () { KUI.busy(save, put(area.value).catch(KUI.errorToast)); });
    clear.addEventListener("click", function () {
      KUI.confirm({ title: "İstem temizlensin mi?", body: "Bu alandaki talimat silinir.", confirmText: "Temizle", cancelText: "Vazgeç", danger: true })
        .then(function (ok) { if (ok) KUI.busy(clear, put("").catch(KUI.errorToast)); });
    });
    var body = h("div", { class: "stack-sm" }, A.skeleton(2));
    var card = A.card(opts.title, opts.sub, body, { cls: "prompt-card" });
    KUI.api("/config").then(function (d) {
      var item = d.settings.filter(function (x) { return x.name === name; })[0];
      saved = item && typeof item.value === "string" ? item.value : "";
      area.value = saved;
      KUI.clear(body);
      body.appendChild(area);
      body.appendChild(h("div", { class: "row-between" }, count, h("div", { class: "cluster" }, clear, save)));
      if (opts.hint) body.appendChild(h("span", { class: "hint", text: opts.hint }));
      sync();
    }).catch(function (e) { KUI.clear(body); body.appendChild(A.errorBox(e)); });
    return card;
  }

  /** On/off switch for the Telegram channel (TELEGRAM_ENABLED); ``onDone(enabled)`` runs after the change applied. */
  function telegramSwitch(enabled, onDone) {
    var sw = h("input", { class: "switch", type: "checkbox", role: "switch", id: "en-telegram", "aria-label": "Telegram kanalını aç/kapat" });
    sw.checked = !!enabled;
    sw.addEventListener("change", function () {
      var want = sw.checked;
      sw.disabled = true;
      KUI.api("/config/batch", { method: "POST", body: { values: { TELEGRAM_ENABLED: want } } }).then(function () {
        KUI.toast(want ? "Telegram açıldı." : "Telegram kapatıldı; bot mesaj dinlemiyor.", { kind: "success" });
        if (onDone) onDone(want);
      }).catch(function (e) { sw.checked = !want; KUI.errorToast(e); }).finally(function () { sw.disabled = false; });
    });
    return sw;
  }

  A.telegramSwitch = telegramSwitch;
  A.promptCard = promptCard;
  A.chatManager = chatManager;
  A.telegramToken = telegramToken;
  A.llmPicker = llmPicker;
  A.modelCombo = modelCombo;
})();
