/* Sohbet: panelden bota yazın, modeli seçin, araçlar çalışsın. Aynı ajan ve aynı güvenlik kuralları;
   onay gerektiren araçlar burada, tarayıcıda onaylanır. Konuşma geçmişi yalnız bu sekmede (bellekte)
   tutulur; sunucuya ve kanal geçmişlerine yazılmaz. Metin textContent ile eklenir (innerHTML yok). */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;
  var SUGGESTIONS = [
    "Bugün İstanbul'da hava nasıl olacak?",
    "Hafızamda neler kayıtlı?",
    "Yarın sabah 09:00'da bana su içmeyi hatırlat",
    "Komuta cloud platformu hakkında web'de ara ve özetle"
  ];
  // survives navigation between panel pages, gone on reload (nothing is stored)
  var store = window.__aibChat = window.__aibChat || { history: [], model: "", chatId: "", entries: [] };

  // ------------------------------------------------------------ tiny, safe markdown (DOM nodes only)
  function inline(text) {
    var out = [], re = /(`[^`\n]+`|\*\*[^*\n]+\*\*|https?:\/\/[^\s<>)]+)/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      var t = m[0];
      if (t.charAt(0) === "`") out.push(h("code", { text: t.slice(1, -1) }));
      else if (t.slice(0, 2) === "**") out.push(h("strong", { text: t.slice(2, -2) }));
      else out.push(h("a", { href: t, target: "_blank", rel: "noopener noreferrer", text: t }));
      last = m.index + t.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  function markdown(text) {
    var box = h("div", { class: "md" });
    String(text || "").split(/```/).forEach(function (part, i) {
      if (i % 2) {
        var code = part.replace(/^[^\n]*\n/, "");
        box.appendChild(h("pre", { class: "code" }, h("code", { text: code.replace(/\n$/, "") })));
        return;
      }
      part.split(/\n{2,}/).forEach(function (para) {
        if (!para.trim()) return;
        var lines = para.split("\n");
        if (lines.every(function (l) { return /^\s*[-*•]\s+/.test(l); })) {
          box.appendChild(h("ul", null, lines.map(function (l) { return h("li", null, inline(l.replace(/^\s*[-*•]\s+/, ""))); })));
        } else if (lines.every(function (l) { return /^\s*\d+[.)]\s+/.test(l); })) {
          box.appendChild(h("ol", null, lines.map(function (l) { return h("li", null, inline(l.replace(/^\s*\d+[.)]\s+/, ""))); })));
        } else {
          var p = h("p");
          lines.forEach(function (l, k) { if (k) p.appendChild(h("br")); inline(l).forEach(function (n) { p.appendChild(typeof n === "string" ? document.createTextNode(n) : n); }); });
          box.appendChild(p);
        }
      });
    });
    return box;
  }

  function prettyPreview(p) {
    if (typeof p === "string") { try { p = JSON.parse(p); } catch (e) { return p; } }
    return JSON.stringify(p, null, 2);
  }

  function render(main) {
    var info = null;
    var models = [], busyTurn = null, pollTimer = null, stopped = false;
    A.onLeave(function () { stopped = true; clearTimeout(pollTimer); });

    var log = h("div", { class: "chat-log", role: "log", "aria-live": "polite", "aria-label": "Konuşma" });
    var input = h("textarea", { class: "textarea chat-input", rows: "1", placeholder: "Bir şey yazın…", "aria-label": "Mesaj", maxlength: "8000" });
    var send = h("button", { class: "btn btn-primary btn-icon chat-send", type: "button", "aria-label": "Gönder" }, A.icon("send"));
    var stop = h("button", { class: "btn btn-icon chat-stop", type: "button", "aria-label": "Durdur", hidden: true }, A.icon("x"));
    var modelBox = h("div", { class: "chat-model" });
    var chatSel = h("select", { class: "select chat-select", "aria-label": "Sohbet bağlamı", hidden: true });
    var meta = h("div", { class: "chat-meta small muted" });
    var fresh = A.button("Yeni sohbet", { kind: "ghost", size: "sm", icon: "plus" });

    var combo = null;
    function buildCombo() {
      combo = A.modelCombo(store.model || (info && info.model) || "", []);
      combo.setModels(models, []);
      KUI.clear(modelBox);
      modelBox.appendChild(h("label", { class: "visually-hidden", for: "llm-model", text: "Model" }));
      modelBox.appendChild(combo.node);
    }

    // ------------------------------------------------------------ rendering of the log
    function scrollDown() { log.scrollTop = log.scrollHeight; }
    function bubble(role, content) {
      var b = h("div", { class: "msg-bubble msg-" + role });
      if (role === "user") b.appendChild(h("p", { text: content }));
      else b.appendChild(markdown(content));
      return b;
    }
    function drawEmpty() {
      KUI.clear(log);
      log.appendChild(h("div", { class: "chat-empty" },
        h("span", { class: "chat-empty-mark" }, A.icon("spark")),
        h("h2", { text: "Botla konuşun" }),
        h("p", { class: "muted", text: "Model, araçlar ve hafıza bağlı kanallardakiyle aynı. Onay gerektiren işlemler burada sorulur." }),
        h("div", { class: "chat-suggest" }, SUGGESTIONS.map(function (s) {
          return h("button", { type: "button", class: "chip chip-btn", onclick: function () { input.value = s; submit(); } }, s);
        }))));
    }
    function replay() {
      if (!store.entries.length) { drawEmpty(); return; }
      KUI.clear(log);
      store.entries.forEach(function (e) {
        if (e.role === "user" || e.role === "assistant") log.appendChild(bubble(e.role, e.content));
        else if (e.role === "error") log.appendChild(A.callout("danger", e.content));
      });
      scrollDown();
    }

    // ------------------------------------------------------------ one turn
    function setBusy(on) {
      send.hidden = on; stop.hidden = !on; input.disabled = false; fresh.disabled = on;
      if (!on) { busyTurn = null; input.focus(); }
    }
    function submit() {
      var text = input.value.trim();
      if (!text || busyTurn) return;
      if (!info || !info.configured) { KUI.toast("Önce Kurulum'dan yapay zekâ sağlayıcısını ayarlayın.", { kind: "danger" }); return; }
      if (!store.entries.length) KUI.clear(log);
      input.value = ""; grow();
      store.entries.push({ role: "user", content: text });
      log.appendChild(bubble("user", text));
      var work = h("div", { class: "msg-work", "aria-live": "polite" }, h("span", { class: "typing", "aria-label": "Yanıt hazırlanıyor" }, h("i"), h("i"), h("i")));
      log.appendChild(work); scrollDown();
      setBusy(true);
      var history = store.history.slice();
      KUI.api("/chat/send", { method: "POST", body: { message: text, model: combo.value() || undefined, history: history, chat_id: store.chatId || undefined, tainted: store.tainted === true || undefined } })
        .then(function (r) {
          busyTurn = r.turn;
          store.history.push({ role: "user", content: text });
          poll(r.turn, 0, work, {});
        }).catch(function (e) { finish(work, null, e.message); });
    }
    function finish(work, answer, error) {
      work.remove();
      if (answer) {
        store.entries.push({ role: "assistant", content: answer });
        store.history.push({ role: "assistant", content: answer });
        log.appendChild(bubble("assistant", answer));
      } else {
        store.entries.push({ role: "error", content: error || "Yanıt alınamadı." });
        store.history.pop();  // the question stays out of the next context if it failed
        log.appendChild(A.callout("danger", error || "Yanıt alınamadı."));
      }
      if (store.history.length > 40) store.history = store.history.slice(-40);
      setBusy(false); scrollDown();
    }
    function toolChip(ev) {
      var chip = h("div", { class: "tool-chip is-running", "data-tool": ev.tool },
        h("span", { class: "tool-state" }, A.icon("tool")), h("span", { class: "tool-name", text: ev.tool }),
        ev.args && ev.args !== "{}" ? h("span", { class: "tool-args ellipsis", text: ev.args }) : null);
      return chip;
    }
    function confirmCard(turn, ev) {
      var yes = A.button("Onayla", { kind: "primary", size: "sm", icon: "check" });
      var no = A.button("Reddet", { size: "sm" });
      var card = h("div", { class: "confirm-card", role: "group", "aria-label": "Onay gerekli" },
        h("div", { class: "row" }, A.icon("shield"), h("strong", { text: ev.tool + " için onay gerekli" })),
        h("pre", { class: "code" }, h("code", { text: prettyPreview(ev.preview) })),
        h("div", { class: "cluster" }, yes, no));
      function answer(ok, btn) {
        KUI.busy(btn, KUI.api("/chat/confirm", { method: "POST", body: { turn: turn, id: ev.id, approve: ok } }).then(function () {
          card.classList.add("is-answered");
          KUI.clear(card); card.appendChild(h("div", { class: "small muted", text: ev.tool + ": " + (ok ? "onaylandı" : "reddedildi") }));
        }).catch(function (e) { KUI.errorToast(e); }));
      }
      yes.addEventListener("click", function () { answer(true, yes); });
      no.addEventListener("click", function () { answer(false, no); });
      return card;
    }
    function poll(turn, since, work, chips) {
      if (stopped) return;
      KUI.api("/chat/turn/" + encodeURIComponent(turn) + "?since=" + since).then(function (r) {
        r.events.forEach(function (ev) {
          if (ev.type === "tool_start") {
            var c = toolChip(ev); chips[ev.tool] = c; work.insertBefore(c, work.querySelector(".typing"));
          } else if (ev.type === "tool_end") {
            var chip = chips[ev.tool];
            if (chip) {
              chip.classList.remove("is-running"); chip.classList.add(ev.ok ? "is-ok" : "is-fail");
              var st = chip.querySelector(".tool-state"); KUI.clear(st); st.appendChild(A.icon(ev.ok ? "check" : "alert"));
              if (!ev.ok && ev.error) chip.appendChild(h("span", { class: "small", text: ev.error }));
            }
          } else if (ev.type === "confirm") {
            work.insertBefore(confirmCard(turn, ev), work.querySelector(".typing"));
          } else if (ev.type === "note") {
            work.insertBefore(h("div", { class: "small muted", text: ev.text }), work.querySelector(".typing"));
          }
        });
        scrollDown();
        if (r.status === "running") { pollTimer = setTimeout(function () { poll(turn, r.next, work, chips); }, 600); return; }
        // keep the finished tool chips above the answer
        var keep = work.querySelectorAll(".tool-chip");
        if (keep.length) {
          var trail = h("div", { class: "tool-trail" }); keep.forEach(function (k) { trail.appendChild(k); });
          log.insertBefore(trail, work);
        }
        if (r.tainted) store.tainted = true;  // outside content was read: later turns keep the stricter rules
        finish(work, r.status === "done" ? r.answer : null, r.error);
      }).catch(function (e) { finish(work, null, e.message); });
    }

    function grow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 200) + "px"; }
    input.addEventListener("input", grow);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
    send.addEventListener("click", submit);
    stop.addEventListener("click", function () {
      if (busyTurn) KUI.api("/chat/cancel", { method: "POST", body: { turn: busyTurn } }).catch(function () { /* ignore */ });
    });
    fresh.addEventListener("click", function () { store.history = []; store.entries = []; store.tainted = false; drawEmpty(); input.focus(); });
    chatSel.addEventListener("change", function () { store.chatId = chatSel.value; });

    var shell = h("div", { class: "chat" },
      h("div", { class: "chat-bar" },
        h("div", { class: "chat-bar-title" }, h("span", { class: "eyebrow", text: "Bot ile sohbet" }), meta),
        h("div", { class: "chat-bar-tools" }, chatSel, modelBox, fresh)),
      log,
      h("div", { class: "chat-composer" }, h("div", { class: "chat-field" }, input, send, stop),
        h("p", { class: "hint chat-hint", text: "Enter gönderir, Shift+Enter yeni satır. Konuşma bu sekmede tutulur; kanal geçmişlerine yazılmaz." })));
    main.appendChild(shell);
    drawEmpty();

    KUI.api("/chat/info").then(function (d) {
      info = d;
      store.model = store.model || d.model;
      meta.textContent = KUI.t(d.tools.length + " araç hazır") + (d.configured ? "" : KUI.t(" · yapay zekâ ayarlı değil"));
      if (d.chats.length > 1) {
        chatSel.hidden = false;
        d.chats.forEach(function (c) { chatSel.appendChild(h("option", { value: String(c.id), text: (c.name || "Sohbet") + " · " + c.id, selected: String(c.id) === store.chatId ? true : null })); });
        if (!store.chatId) store.chatId = String(d.chats[0].id);
      }
      buildCombo();
      replay();
      if (!d.configured) log.appendChild(A.callout("danger", h("span", null, "Yapay zekâ sağlayıcısı henüz ayarlı değil. ", h("a", { href: "#/kurulum/llm", text: "Kurulumu tamamlayın" }), ".")));
      KUI.api("/llm/models", { method: "POST", body: {} }).then(function (r) { models = r.models || []; if (combo) combo.setModels(models, r.recent || []); }).catch(function () { /* manual entry still works */ });
    }).catch(function (e) { log.appendChild(A.errorBox(e)); });
    // remember the typed model between turns
    modelBox.addEventListener("change", function () { if (combo) store.model = combo.value(); });
    modelBox.addEventListener("focusout", function () { if (combo) store.model = combo.value(); });
    input.focus();
  }

  A.pages.chat = { title: "Sohbet", render: render };
})();
