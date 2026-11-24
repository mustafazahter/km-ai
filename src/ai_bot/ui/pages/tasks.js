/* Görevler: scheduled tasks of every chat (next run, repeat, cancel). */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  function render(main) {
    var showAll = false;
    var seg = h("div", { class: "segmented", role: "group", "aria-label": "Görev filtresi" });
    var bActive = h("button", { type: "button", "aria-pressed": "true", text: "Aktif" });
    var bAll = h("button", { type: "button", "aria-pressed": "false", text: "Geçmiş dahil" });
    seg.appendChild(bActive); seg.appendChild(bAll);
    main.appendChild(A.pageHead("Veri", "Görevler", "Zamanlanmış hatırlatmalar ve AI istemleri. Saatler botun saat diliminde gösterilir.", seg));
    var body = h("div", null, A.skeleton(4));
    var tz = h("span", { class: "card-sub" });
    main.appendChild(A.card("Zamanlanmış görevler", null, [tz, body], { flush: false }));

    function load() {
      KUI.api("/tasks" + (showAll ? "?include_inactive=true" : "")).then(function (r) {
        tz.textContent = KUI.t("Saat dilimi: " + r.timezone + " · " + r.tasks.length + " görev");
        KUI.clear(body);
        if (!r.tasks.length) {
          body.appendChild(A.empty("clock", "Görev yok", "Sohbette (panel ya da bir kanal) \"her sabah 9'da haberleri özetle\" gibi bir istekle görev kurulur."));
          return;
        }
        function cancelButton(t) {
          var cancel = t.active ? A.button("İptal", { size: "sm", kind: "ghost", icon: "x", aria: "Görev #" + t.id + " iptal" }) : A.badge("bitti", "outline");
          if (t.active) cancel.addEventListener("click", function () {
            KUI.confirm({ title: "Görev #" + t.id + " iptal edilsin mi?", body: t.description || t.text, confirmText: "İptal et", danger: true }).then(function (ok) {
              if (!ok) return;
              KUI.busy(cancel, KUI.api("/tasks/" + t.id, { method: "DELETE" }).then(function () {
                KUI.toast("Görev iptal edildi.", { kind: "success" }); load();
              }).catch(KUI.errorToast));
            });
          });
          return cancel;
        }
        // phones: a list (the table needs sideways scrolling)
        body.appendChild(h("ul", { class: "list show-mobile" }, r.tasks.map(function (t) {
          return h("li", { class: "mem-item" },
            h("div", { class: "stack-sm grow" },
              h("p", { class: "small ink", text: (t.description || t.text) }),
              h("div", { class: "meta" },
                A.badge(t.kind === "prompt" ? "AI istemi" : "Mesaj", t.kind === "prompt" ? "accent" : null),
                h("span", { text: t.cron ? t.cron : "tek sefer" }),
                h("span", { class: "num", text: t.next_run ? A.fmtTime(t.next_run, r.timezone) + " · " + A.relTime(t.next_run) : "—" }),
                h("span", { class: "num", text: "#" + t.id + " · " + t.chat_id }))),
            cancelButton(t));
        })));
        var rows = r.tasks.map(function (t) {
          var cancel = cancelButton(t);
          return h("tr", null,
            h("td", { class: "t-mono num", text: "#" + t.id }),
            h("td", { class: "wrap-cell" }, h("div", { class: "ink small", text: t.description || t.text }),
              t.description ? h("div", { class: "small muted", text: t.text }) : null),
            h("td", null, A.badge(t.kind === "prompt" ? "AI istemi" : "Mesaj", t.kind === "prompt" ? "accent" : null)),
            h("td", { class: "small" }, t.cron ? h("code", { text: t.cron }) : "tek sefer"),
            h("td", { class: "small num" }, h("div", { text: A.fmtTime(t.next_run, r.timezone) }),
              h("div", { class: "muted", text: t.next_run ? A.relTime(t.next_run) : (t.last_run ? "son: " + A.fmtTime(t.last_run, r.timezone) : "") })),
            h("td", { class: "t-mono small", text: String(t.chat_id) }),
            h("td", { class: "t-right" }, cancel));
        });
        body.appendChild(h("div", { class: "table-wrap hide-mobile" }, h("table", { class: "table" },
          h("thead", null, h("tr", null, ["#", "Görev", "Tür", "Tekrar", "Sonraki", "Sohbet", ""].map(function (x, i) {
            return h("th", { scope: "col", class: i === 6 ? "t-right" : null }, x || h("span", { class: "visually-hidden", text: "İşlem" }));
          }))),
          h("tbody", null, rows))));
      }).catch(function (e) { KUI.clear(body); body.appendChild(A.errorBox(e)); });
    }
    function setMode(all) {
      showAll = all;
      bActive.setAttribute("aria-pressed", all ? "false" : "true");
      bAll.setAttribute("aria-pressed", all ? "true" : "false");
      load();
    }
    bActive.addEventListener("click", function () { setMode(false); });
    bAll.addEventListener("click", function () { setMode(true); });
    load();
  }

  A.pages.tasks = { title: "Görevler", render: render };
})();
