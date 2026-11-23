/* Günlük: recent log records (secrets redacted on the server) and stack events. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;
  var LEVEL_KIND = { error: "danger", critical: "danger", warning: "outline", info: null, debug: null };

  function render(main) {
    var level = "info";
    var seg = h("div", { class: "segmented", role: "group", "aria-label": "Seviye" });
    [["info", "Tümü"], ["warning", "Uyarı"], ["error", "Hata"]].forEach(function (x) {
      var b = h("button", { type: "button", "aria-pressed": x[0] === level ? "true" : "false", text: x[1] });
      b.addEventListener("click", function () {
        level = x[0];
        KUI.$all("button", seg).forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
        tick();
      });
      seg.appendChild(b);
    });
    main.appendChild(A.pageHead("Sistem", "Günlük", "Son olaylar ve log kayıtları. Token ve anahtarlar sunucuda maskelenir (***).", seg));
    var logList = h("ul", { class: "list" }, h("li", null, A.skeleton(6)));
    var eventList = h("ul", { class: "list" });
    main.appendChild(h("section", { class: "grid grid-3" },
      h("div", { class: "span-2" }, A.card("Log", "Bu süreç başladığından beri, en yeni üstte", logList)),
      A.card("Olaylar", "ai.reply, schedule.fired, memory.*", eventList)));

    function tick() {
      return KUI.api("/logs?level=" + level + "&limit=150").then(function (r) {
        KUI.clear(logList);
        if (!r.logs.length) logList.appendChild(h("li", null, A.empty("check", "Kayıt yok")));
        r.logs.forEach(function (l) {
          logList.appendChild(h("li", { class: "log-line" },
            h("span", { class: "badge level" + (LEVEL_KIND[l.level] ? " badge-" + LEVEL_KIND[l.level] : "") , text: l.level }),
            h("span", { class: "msg", text: l.message }),
            h("span", { class: "meta", text: A.fmtTime(l.ts) + " · " + l.logger })));
        });
        KUI.clear(eventList);
        if (!r.events.length) eventList.appendChild(h("li", { class: "small muted", text: "Henüz olay yok." }));
        r.events.forEach(function (e) {
          eventList.appendChild(h("li", { class: "stack-sm" },
            h("div", { class: "row-between" }, h("span", { class: "tool-name", text: e.event }), h("span", { class: "small muted", text: KUI.timeAgo(e.ts) })),
            h("span", { class: "mono-sm break", text: e.data })));
        });
      });
    }
    var stop = KUI.poll(tick, 5000, { onError: function (e) { KUI.clear(logList); logList.appendChild(h("li", null, A.errorBox(e))); } });
    A.onLeave(stop);
  }

  A.pages.logs = { title: "Günlük", render: render };
})();
