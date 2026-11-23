/* Genel bakış: bot status, setup progress, counts, search, bridges, recent errors. */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;

  function ring(done, total, animate) {
    var r = 56, c = 2 * Math.PI * r;
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 132 132");
    svg.setAttribute("aria-hidden", "true");
    function circle(cls) {
      var el = document.createElementNS(NS, "circle");
      el.setAttribute("cx", "66"); el.setAttribute("cy", "66"); el.setAttribute("r", String(r));
      el.setAttribute("class", cls);
      return el;
    }
    var track = circle("ring-track"), bar = circle("ring-bar");
    bar.setAttribute("stroke-dasharray", String(c));
    var target = String(c * (1 - done / total));
    bar.setAttribute("stroke-dashoffset", animate ? String(c) : target);
    svg.appendChild(track); svg.appendChild(bar);
    if (animate) requestAnimationFrame(function () {
      requestAnimationFrame(function () { bar.setAttribute("stroke-dashoffset", target); });
    });
    return h("div", { class: "ring", role: "img", "aria-label": "Kurulum " + done + " / " + total },
      svg, h("div", { class: "ring-label" }, h("strong", { text: done + "/" + total }), h("span", { text: "kurulum" })));
  }

  /** Channels that are on and working right now (Telegram counted when it is polling). */
  function liveChannels(d) { return (d.channels || []).filter(function (c) { return c.status === "ready"; }); }
  function channelNames(list) { return list.map(function (c) { return c.title; }).join(", "); }
  /** Sidebar chip: how many chat channels answer right now. */
  function liveText(d) {
    var n = liveChannels(d).length;
    return n === 0 ? "Çalışıyor" : n === 1 ? liveChannels(d)[0].title + " bağlı" : n + " kanal bağlı";
  }

  function hero(d, animate) {
    var s = d.setup;
    var ready = s.completed || s.done === s.total;
    var live = liveChannels(d);
    var anyConfigured = live.length > 0 || (d.channels || []).some(function (c) { return c.status !== "off" && c.status !== "missing"; });
    var title, text;
    if (!anyConfigured && !ready) {
      title = "Bir kanal bağlayalım";
      text = "Henüz hiçbir kanal bağlı değil. Panelde Sohbet sayfasından hemen konuşabilir ya da kurulum sihirbazıyla Telegram, WhatsApp, Teams, Slack veya e-postayı bağlayabilirsiniz.";
    } else if (ready) {
      title = live.length ? "Bot çalışıyor" : "Bot hazır, kanal bekleniyor";
      text = live.length
        ? channelNames(live) + " üzerinden gelen mesajları dinliyor. Ayarlar kaydedildiği anda uygulanır; yeniden başlatma gerekmez."
        : "Hiçbir kanal şu an açık değil ya da bağlanıyor. Kanallar sayfasından durumları ve testleri görün; panelde Sohbet her zaman çalışır.";
    } else {
      title = "Kurulumun " + s.done + "/" + s.total + " adımı tamam";
      text = "Kalan adımları tamamlayın; her adım kendi testini içerir ve sonucu burada görünür.";
    }
    var chips = s.steps.slice(0, -1).map(function (st) {
      return h("span", { class: "chip" + (st.done ? " is-done" : "") }, A.icon(st.done ? "check" : "dots"), st.title);
    });
    return h("div", { class: "bezel" },
      h("section", { class: "card hero", "aria-label": "Durum" },
        h("div", { class: "stack gap-lg" },
          h("div", { class: "stack-sm" },
            h("div", { class: "hero-meta" },
              live.length ? A.badge(live.length === 1 ? live[0].title + " bağlı" : live.length + " kanal bağlı", "success", "success") : A.badge("Kanal bağlı değil", "outline"),
              d.protection === false ? A.badge("Panel koruması kapalı", "danger", "danger") : A.badge("Panel korumalı", "outline"),
              A.badge("v" + d.stack.version), A.badge("Çalışma süresi " + KUI.duration(d.stack.uptime))),
            h("h2", { text: title }), h("p", { text: text })),
          h("div", { class: "steps-mini" }, chips),
          h("div", { class: "cluster" },
            ready ? h("a", { class: "btn", href: "#/ayarlar" }, A.icon("sliders"), "Ayarlara git")
              : h("a", { class: "btn btn-primary", href: "#/kurulum" }, "Eksikleri tamamla",
                  h("span", { class: "btn-trail", "aria-hidden": "true" }, A.icon("arrow-right"))),
            h("a", { class: "btn btn-ghost", href: "#/gunluk" }, A.icon("activity"), "Günlük"))),
        ring(s.done, s.total, animate)));
  }

  function stat(href, iconName, label, value, foot, textual) {
    return h("a", { class: "card stat", href: href },
      h("span", { class: "stat-label" }, A.icon(iconName), label),
      h("span", { class: "stat-value" + (textual ? " stat-text" : ""), text: value }),
      h("span", { class: "stat-foot", text: foot }));
  }

  function stats(d) {
    var live = liveChannels(d);
    var next = d.tasks.next;
    return h("section", { class: "grid grid-4", "aria-label": "Özet" },
      stat("#/kanallar", "send", "Kanallar", live.length ? live.length + " bağlı" : "Bağlı kanal yok",
        live.length ? channelNames(live) : "Kanallar sayfasından ekleyin", true),
      stat("#/kurulum/llm", "spark", "Model", d.llm.model,
        (d.llm.host || "—").replace(/^https?:\/\//, "") + (d.llm.tested ? " · test edildi" : ""), true),
      stat("#/hafiza", "brain", "Hafıza", KUI.number(d.memory.total), d.memory.chats + " sohbette"),
      stat("#/gorevler", "clock", "Görevler", KUI.number(d.tasks.active),
        next ? "Sonraki: " + A.relTime(next.next_run) : "Zamanlanmış görev yok"));
  }

  function firstSentence(t) {
    var m = /^(.+?[.!?])(\s|$)/.exec(t || "");
    var one = m ? m[1] : (t || "");
    return one.length > 140 ? one.slice(0, 137) + "…" : one;
  }

  function searchCard(d) {
    var s = d.search;
    return h("section", { class: "card ov-card" },
      h("div", { class: "ov-head" },
        h("span", { class: "ov-mark" }, A.icon("search")),
        h("div", { class: "grow" },
          h("h2", { class: "ov-title", text: "Web arama" }),
          h("span", { class: "ov-sub", text: "Sağlayıcı: " + s.provider })),
        A.badge(s.ready ? "Hazır" : "Eksik ayar", s.ready ? "success" : "danger", s.ready ? "success" : null)),
      s.warning ? h("p", { class: "ov-note" + (s.ready ? "" : " is-bad"), text: firstSentence(s.warning), title: s.warning }) : null,
      h("div", { class: "ov-foot" }, h("a", { class: "btn btn-sm", href: "#/kurulum/search" }, "Aramayı ayarla", A.icon("arrow-right"))));
  }

  function bridgesCard(d) {
    var gate = d.gate ? h("div", { class: "cluster" }, [["bridge", "Köprü"], ["mcp", "MCP"], ["hook", "Webhook"]].map(function (x) {
      return A.badge(x[1] + (d.gate[x[0]] ? " açık" : " kapalı"), d.gate[x[0]] ? "success" : "outline", d.gate[x[0]] ? "success" : null);
    })) : null;
    var list = d.bridges.length ? h("ul", { class: "ov-list" }, d.bridges.map(function (b) {
      return h("li", null,
        h("span", { class: "dot " + (b.status === "ok" ? "dot-success" : b.status === "error" ? "dot-danger" : "") }),
        h("span", { class: "ov-name", text: b.name }),
        h("span", { class: "small muted ellipsis grow", text: b.error || b.host || "yerel" }),
        A.badge(b.status === "ok" ? "bağlı" : b.status === "error" ? "hata" : "denenmedi", b.status === "ok" ? "success" : b.status === "error" ? "danger" : "outline"));
    })) : h("p", { class: "ov-note", text: "Henüz bağlı köprü yok. BRIDGES ile başka stack'lerin araçlarını ekleyebilirsiniz." });
    return h("section", { class: "card ov-card" },
      h("div", { class: "ov-head" },
        h("span", { class: "ov-mark" }, A.icon("plug")),
        h("div", { class: "grow" },
          h("h2", { class: "ov-title", text: "Köprü ve entegrasyonlar" }),
          h("span", { class: "ov-sub", text: d.tools.local + " yerel araç · kapalı yüzey 404 döner" }))),
      gate, list,
      h("div", { class: "ov-foot" }, h("a", { class: "btn btn-sm", href: "#/kopru" }, "Köprü ayarları", A.icon("arrow-right"))));
  }

  /** Same message repeated (only numbers differ, e.g. "retrying in 8.0s") collapses into one row with a count. */
  function groupErrors(rows) {
    var out = [], index = {};
    rows.forEach(function (r) {
      var key = r.level + "|" + r.logger + "|" + String(r.message).replace(/[\d.]+/g, "#");
      if (index[key]) { index[key].count += 1; return; }
      index[key] = { row: r, count: 1 };
      out.push(index[key]);
    });
    return out;
  }

  function errorsCard(d) {
    var rows = d.errors.concat((d.plugin_errors || []).map(function (p) { return { level: "error", message: p, ts: null, logger: "plugin" }; }));
    var groups = groupErrors(rows);
    var body = groups.length ? h("ul", { class: "ov-list ov-errors" }, groups.slice(0, 4).map(function (g) {
      var r = g.row;
      return h("li", null,
        h("span", { class: "dot " + (r.level === "warning" ? "" : "dot-danger") }),
        h("div", { class: "grow" },
          h("div", { class: "ov-msg", text: r.message, title: r.message }),
          h("div", { class: "small muted", text: (r.ts ? KUI.timeAgo(r.ts) + " · " : "") + r.logger + (g.count > 1 ? " · " + g.count + " kez" : "") })));
    })) : h("p", { class: "ov-note", text: "Hata yok. Son uyarı ve hatalar burada görünür." });
    return h("section", { class: "card ov-card" },
      h("div", { class: "ov-head" },
        h("span", { class: "ov-mark" }, A.icon("activity")),
        h("div", { class: "grow" },
          h("h2", { class: "ov-title", text: "Son hatalar" }),
          h("span", { class: "ov-sub", text: groups.length ? groups.length + " ayrı uyarı" : "Temiz" }))),
      body,
      h("div", { class: "ov-foot" }, h("a", { class: "btn btn-sm", href: "#/gunluk" }, "Tüm günlük", A.icon("arrow-right"))));
  }

  /** Whole-number stat values count up once when the page opens (skipped for reduced motion). */
  function countUp(root) {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    KUI.$all(".stat-value", root).forEach(function (el) {
      var txt = el.textContent.replace(/\s/g, "");
      if (!/^\d{1,6}$/.test(txt) || Number(txt) < 2) return;
      var target = Number(txt), t0 = performance.now(), dur = 700;
      function tick(now) {
        var k = Math.min(1, (now - t0) / dur), eased = 1 - Math.pow(1 - k, 3);
        el.textContent = KUI.number(Math.round(target * eased));
        if (k < 1) requestAnimationFrame(tick); else el.textContent = KUI.number(target);
      }
      requestAnimationFrame(tick);
    });
  }

  var last = null;   // last overview answer: reopening the page paints it at once, then refreshes quietly

  function paint(host, d, animate) {
    var fresh = h("div", null,
      hero(d, animate), stats(d),
      h("section", { class: "grid grid-3 ov-row" }, searchCard(d), bridgesCard(d), errorsCard(d)));
    if (host.querySelector("[aria-busy]") || host.querySelector(".callout-danger")) KUI.clear(host);
    A.morph(host, fresh);   // edits only what changed; nothing is rebuilt, nothing flashes
    var live = KUI.$("[data-live-text]");
    if (live) live.textContent = KUI.t(liveText(d));
  }

  function render(main) {
    main.appendChild(A.pageHead("Durum", "Genel bakış", "Botun bağlantıları, sayıları ve son olayları. Sayfa kendini yeniler."));
    var stale = h("p", { class: "hint ov-stale", role: "status", hidden: true,
      text: "Son yenileme başarısız; gösterilen veriler eski olabilir." });
    var host = h("div", { class: "stack gap-lg" });
    main.appendChild(stale);
    main.appendChild(host);
    var first = !last, checked = false;
    if (last) paint(host, last, false); else host.appendChild(A.skeleton(4));
    var stop = KUI.poll(function () {
      return KUI.api("/overview").then(function (d) {
        stale.hidden = true;
        if (!checked) {   // once per visit: first run goes to the welcome wizard until it is finished or dismissed
          checked = true;
          if (!d.setup.completed && !d.setup.dismissed) { location.hash = "#/hos-geldin"; return; }
        }
        last = d;
        paint(host, d, first);
        if (first) { countUp(host); host.classList.add("enter"); first = false; }
      });
    }, 10000, { onError: function (e) {
      if (host.querySelector(".stat")) { stale.hidden = false; return; }   // keep what is on screen, say it may be old
      KUI.clear(host); host.appendChild(A.errorBox(e));
    } });
    A.onLeave(stop);
  }

  A.pages.overview = { title: "Genel bakış", render: render };
})();
