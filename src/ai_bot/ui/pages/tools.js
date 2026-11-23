/* Araçlar ve köprüler: built-in, plugin and remote bridge tools; tools that need a human
   confirmation are marked; bridge connection test. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;
  var SOURCE = { builtin: ["yerleşik", null], plugin: ["plugin", "accent"], bridge: ["köprü", "outline"] };

  function toolTable(tools) {
    if (!tools.length) return A.empty("tool", "Araç yok");
    return h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", null, h("tr", null,
        h("th", { scope: "col", text: "Araç" }), h("th", { scope: "col", text: "Kaynak" }), h("th", { scope: "col", text: "Kapsam" }),
        h("th", { scope: "col", text: "Onay" }))),
      h("tbody", null, tools.map(function (t) {
        var src = SOURCE[t.source] || [t.source, null];
        return h("tr", null,
          h("td", { class: "tool-cell" }, h("div", { class: "tool-name", text: t.name }),
            t.description ? h("div", { class: "tool-desc", text: A.bi(t.description) }) : null,
            t.bridge || t.hidden_from_ai ? h("div", { class: "small muted", text: [t.bridge ? "köprü: " + t.bridge : "", t.hidden_from_ai ? "AI'ya gösterilmez" : ""].filter(Boolean).join(" · ") }) : null),
          h("td", null, A.badge(src[0], src[1])),
          h("td", { class: "small", text: t.scope }),
          h("td", null, t.needs_confirmation ? A.badge("Onay gerekir", "danger", "danger") : h("span", { class: "small muted", text: "—" })));
      }))));
  }

  function bridgeList(bridges) {
    if (!bridges.length) return A.empty("plug", "Köprü tanımlı değil", "BRIDGES ortam değişkeni veya BRIDGE_<AD>_URL + _TOKEN ile başka stack'leri bağlayın.");
    return h("ul", { class: "list" }, bridges.map(function (b) {
      return h("li", null,
        h("span", { class: "dot " + (b.status === "ok" ? "dot-success" : "dot-danger") }),
        h("div", { class: "grow" }, h("div", { class: "ink small", text: b.name + (b.kind === "local" ? " (yerel)" : "") }),
          h("div", { class: "small muted break", text: b.error || b.host || "aynı süreç" })),
        A.badge(b.tools + " araç", "outline"));
    }));
  }

  function render(main) {
    var testBtn = A.button("Köprüleri test et", { icon: "refresh" });
    main.appendChild(A.pageHead("Sistem", "Araçlar ve köprüler", "AI'nın kullanabildiği her araç. Onay gerektirenler Telegram'da düğmeyle onaylanmadan çalışmaz.", testBtn));
    var bridgesBody = h("div", null, A.skeleton(2));
    var localBody = h("div", null, A.skeleton(5));
    var remoteBody = h("div", null);
    var pluginNote = h("div");
    main.appendChild(h("section", { class: "grid grid-3" },
      A.card("Köprü bağlantıları", "Uzak stack'ler (/bridge)", bridgesBody),
      h("div", { class: "span-2 stack gap-lg" },
        A.card("Yerel araçlar", "Yerleşik ve plugins/ klasöründen", [pluginNote, localBody], { flush: true }),
        A.card("Köprü araçları", "Uzak stack'lerden gelen araçlar", remoteBody, { flush: true }))));

    function load() {
      return KUI.api("/tools").then(function (r) {
        KUI.clear(localBody); localBody.appendChild(toolTable(r.local));
        KUI.clear(remoteBody); remoteBody.appendChild(r.remote.length ? toolTable(r.remote) : A.empty("plug", "Köprü aracı yok"));
        KUI.clear(bridgesBody); bridgesBody.appendChild(bridgeList(r.bridges));
        KUI.clear(pluginNote);
        if (r.plugin_errors.length) pluginNote.appendChild(A.callout("danger", "Yüklenemeyen plugin: " + r.plugin_errors.join(" · ")));
      }).catch(function (e) { KUI.clear(localBody); localBody.appendChild(A.errorBox(e)); });
    }
    testBtn.addEventListener("click", function () {
      KUI.busy(testBtn, KUI.api("/bridges/test", { method: "POST" }).then(function (r) {
        var bad = r.bridges.filter(function (b) { return b.status !== "ok"; }).length;
        KUI.toast(r.bridges.length ? (bad ? bad + " köprüye ulaşılamadı." : "Tüm köprüler yanıt verdi.") : "Tanımlı köprü yok.", { kind: bad ? "danger" : "success" });
        return load();
      }).catch(KUI.errorToast));
    });
    load();
  }

  A.pages.tools = { title: "Araçlar ve köprüler", render: render };
})();
