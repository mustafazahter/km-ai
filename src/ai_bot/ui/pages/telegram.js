/* Telegram: bot token (write/test/getMe), connection, allowed chats with live chat id capture,
   command list sync, recent Telegram errors. Every change applies live. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  function statusCard(t, reload) {
    var restart = A.button("Yeniden bağlan", { size: "sm", icon: "refresh", onclick: function () {
      KUI.busy(restart, KUI.api("/telegram/restart", { method: "POST" }).then(function (r) {
        KUI.toast(r.running ? "Telegram bağlantısı yeniden kuruldu." : "Token yok; bot başlatılmadı.", { kind: r.running ? "success" : "danger" });
        setTimeout(reload, 800);
      }).catch(KUI.errorToast));
    } });
    return A.card("Bağlantı", null, [
      h("dl", { class: "kv" },
        h("dt", { text: "Kanal" }), h("dd", null, A.telegramSwitch(t.enabled, function () { setTimeout(reload, 600); })),
        h("dt", { text: "Durum" }), h("dd", null, !t.enabled ? A.badge("Kapalı", "outline") : t.running ? A.badge("Bağlı", "success", "success") : A.badge(t.configured ? "Kapalı" : "Token yok", "outline")),
        h("dt", { text: "Bot" }), h("dd", { text: t.bot && t.bot.username ? "@" + t.bot.username : "—" }),
        h("dt", { text: "İzinli sohbet" }), h("dd", { class: "num", text: String(t.allowed.length) }),
        h("dt", { text: "Onay bekleme süresi" }), h("dd", { class: "num", text: t.confirm_timeout + " sn" })),
      t.managed ? (t.enabled ? h("div", { class: "cluster" }, restart) : h("span", { class: "hint", text: "Kanal kapalı; bot mesaj dinlemiyor. Açınca bağlantı yeniden kurulur." })) : A.callout("info", "Bot bu serviste başka bir modüle ait; token değişince servisi yeniden başlatın.")
    ]);
  }

  function commandsCard(t) {
    var sync = A.button("Telegram'a gönder", { size: "sm", icon: "send", onclick: function () {
      KUI.busy(sync, KUI.api("/telegram/commands/sync", { method: "POST" }).then(function (r) {
        KUI.toast(r.count + " komut Telegram menüsüne gönderildi.", { kind: "success" });
        note.textContent = "Son senkron: az önce";
      }).catch(KUI.errorToast));
    } });
    var note = h("span", { class: "small muted", text: t.commands_synced_at ? "Son senkron: " + KUI.timeAgo(t.commands_synced_at) : "Bot her açılışta komut listesini kendisi gönderir." });
    return A.card("Komutlar", "Telegram'daki / menüsü", [
      h("ul", { class: "list" }, t.commands.map(function (c) {
        return h("li", null, h("span", { class: "tool-name", text: "/" + c.name }), h("span", { class: "small muted grow", text: A.bi(c.help || "") }));
      })),
      h("div", { class: "row-between" }, note, sync)
    ]);
  }

  function errorsCard(t) {
    var body = t.errors.length ? h("ul", { class: "list" }, t.errors.map(function (r) {
      return h("li", { class: "log-line" }, A.badge(r.level, r.level === "warning" ? "outline" : "danger"),
        h("span", { class: "msg", text: r.message }), h("span", { class: "meta", text: KUI.timeAgo(r.ts) + " · " + r.logger }));
    })) : A.empty("check", "Telegram hatası yok", "409 (başka bir örnek), 401 (token reddedildi) gibi hatalar burada görünür.");
    return A.card("Son hatalar", null, body);
  }

  function render(main) {
    main.appendChild(h("div", null, h("a", { class: "btn btn-ghost btn-sm", href: "#/kanallar" }, A.icon("chev-left"), "Kanallar")));
    main.appendChild(A.pageHead("Bağlantılar", "Telegram", "Bot token'ı, izinli sohbetler ve komut menüsü. Değişiklikler hemen uygulanır."));
    var host = h("div", { class: "stack gap-lg" }, A.skeleton(5));
    main.appendChild(host);
    function load() {
      return KUI.api("/telegram").then(function (t) {
        A.onLeaveNow();
        KUI.clear(host);
        var left = h("div", { class: "span-2 stack gap-lg" },
          A.card("Bot token", t.bot && t.bot.username ? "@" + t.bot.username + " · getMe ile doğrulandı" : "BotFather'dan alınan token",
            A.telegramToken(t.token, { onSaved: function () { setTimeout(load, 300); } })),
          A.card("İzinli sohbetler", "Sohbet kimliği yakalama canlı", A.chatManager().node),
          A.promptCard("SYSTEM_PROMPT_TELEGRAM", { title: "Telegram sistem istemi", sub: "Yalnız Telegram konuşmalarında genel istemden sonra eklenir.",
            placeholder: "Örn. Kısa ve samimi yaz. Emoji kullanabilirsin.", hint: "Genel istem Kanallar sayfasında ve Ayarlar > Ajan bölümünde." }));
        var right = h("div", { class: "stack gap-lg" }, statusCard(t, load), commandsCard(t), errorsCard(t));
        host.appendChild(h("section", { class: "grid grid-3" }, left, right));
      }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
    }
    load();
  }

  A.pages.telegram = { title: "Telegram", render: render };
})();
