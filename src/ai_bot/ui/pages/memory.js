/* Hafıza: per-chat memories (list, search, delete, clear). Chats never share memories. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  function render(main, route) {
    main.appendChild(A.pageHead("Veri", "Hafıza", "Botun her sohbet için ayrı tuttuğu anılar. Sohbet seçin, arayın veya silin."));
    var chatSelect = h("select", { class: "select", id: "mem-chat", "aria-label": "Sohbet" });
    var q = h("input", { class: "input", type: "search", id: "mem-q", placeholder: "Anılarda ara", "aria-label": "Anılarda ara", autocomplete: "off" });
    var searchBtn = A.button("Ara", { icon: "search", type: "submit" });
    var clearAll = A.button("Tümünü sil", { kind: "ghost", size: "sm", icon: "trash" });
    var form = h("form", { class: "toolbar", role: "search" },
      h("div", { class: "field grow" }, chatSelect), h("div", { class: "input-group" }, q, searchBtn));
    var list = h("ul", { class: "list" });
    var count = h("span", { class: "card-sub" });
    var listCard = A.card("Anılar", null, [count, list], { action: clearAll });
    var host = h("div", { class: "stack gap-lg" }, A.card(null, null, form), listCard);
    main.appendChild(host);

    var chatId = route.query.chat || null;

    function loadList() {
      KUI.clear(list);
      if (!chatId) { list.appendChild(h("li", null, A.empty("brain", "Henüz anı yok", "Bot bir şeyi hatırladığında sohbetiyle birlikte burada görünür."))); count.textContent = ""; clearAll.hidden = true; return; }
      clearAll.hidden = false;
      list.appendChild(h("li", null, A.skeleton(3)));
      var url = "/memory?chat_id=" + encodeURIComponent(chatId) + "&limit=100" + (q.value.trim() ? "&q=" + encodeURIComponent(q.value.trim()) : "");
      KUI.api(url).then(function (r) {
        KUI.clear(list);
        count.textContent = q.value.trim() ? r.items.length + " eşleşme" : r.total + " anı" + (r.total > r.items.length ? " · en yeni " + r.items.length : "");
        if (!r.items.length) { list.appendChild(h("li", null, A.empty("search", q.value.trim() ? "Eşleşme yok" : "Bu sohbette anı yok"))); return; }
        r.items.forEach(function (m) {
          var del = A.button("", { kind: "ghost", size: "sm", icon: "trash", cls: "btn-icon", aria: "Anı #" + m.id + " sil" });
          var li = h("li", { class: "mem-item" },
            h("div", { class: "stack-sm grow" }, h("p", { text: m.content }),
              h("div", { class: "meta" }, h("span", { class: "num", text: "#" + m.id }), h("span", { text: m.created }),
                m.tags ? h("span", { text: m.tags }) : null)),
            del);
          del.addEventListener("click", function () {
            KUI.confirm({ title: "Anı silinsin mi?", body: m.content, confirmText: "Sil", danger: true }).then(function (ok) {
              if (!ok) return;
              KUI.busy(del, KUI.api("/memory/" + encodeURIComponent(chatId) + "/" + m.id, { method: "DELETE" }).then(function () {
                li.remove(); KUI.toast("Anı silindi.", { kind: "success" });
              }).catch(KUI.errorToast));
            });
          });
          list.appendChild(li);
        });
      }).catch(function (e) { KUI.clear(list); list.appendChild(h("li", null, A.errorBox(e))); });
    }

    function loadChats() {
      return KUI.api("/memory/chats").then(function (r) {
        KUI.clear(chatSelect);
        if (!r.chats.length) { chatSelect.appendChild(h("option", { value: "", text: "Sohbet yok" })); chatSelect.disabled = true; chatId = null; return; }
        chatSelect.disabled = false;
        if (!chatId || !r.chats.some(function (c) { return String(c.chat_id) === String(chatId); })) chatId = String(r.chats[0].chat_id);
        r.chats.forEach(function (c) {
          chatSelect.appendChild(h("option", { value: String(c.chat_id), selected: String(c.chat_id) === String(chatId) ? true : null,
            text: c.chat_id + " · " + c.count + " anı" + (c.allowed ? "" : " · izinli değil") }));
        });
      });
    }

    chatSelect.addEventListener("change", function () { chatId = chatSelect.value; q.value = ""; loadList(); });
    form.addEventListener("submit", function (e) { e.preventDefault(); loadList(); });
    clearAll.addEventListener("click", function () {
      KUI.confirm({ title: "Bu sohbetin tüm anıları silinsin mi?", body: "Sohbet " + chatId + " için kayıtlı her anı kalıcı olarak silinir.", confirmText: "Hepsini sil", danger: true })
        .then(function (ok) {
          if (!ok) return;
          KUI.busy(clearAll, KUI.api("/memory/" + encodeURIComponent(chatId), { method: "DELETE" }).then(function (r) {
            KUI.toast(r.deleted + " anı silindi.", { kind: "success" });
            return loadChats().then(loadList);
          }).catch(KUI.errorToast));
        });
    });
    loadChats().then(loadList).catch(function (e) { KUI.clear(list); list.appendChild(h("li", null, A.errorBox(e))); });
  }

  A.pages.memory = { title: "Hafıza", render: render };
})();
