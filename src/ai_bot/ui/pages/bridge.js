/* Köprü ve entegrasyonlar: /bridge, /mcp, /hook on/off (closed = 404), token / hook secret created
   here and shown ONCE, copy-paste examples (n8n, curl, Claude Desktop / Code, TradingView), audit.
   Uses the stack_ui bridge gate API (/ui/api/bridge). */
(function () {
  "use strict";
  var A = window.AIB, h = A.h;
  var SURFACES = [
    { id: "bridge", name: "Köprü (HTTP)", path: "/bridge/*", text: "n8n, başka stack veya betikler araçları çağırır (Bearer token)." },
    { id: "mcp", name: "MCP", path: "/mcp", text: "Claude Desktop / Claude Code gibi MCP istemcileri araçları görür (Bearer token)." },
    { id: "hook", name: "Webhook", path: "/hook/*", text: "Dışarıdan gelen bildirimler (/hook/notify → Telegram). HMAC imza, token veya gövdedeki gizli alan." }
  ];
  var SOURCE = { env: "ortam değişkeni", saved: "panel", "default": "varsayılan" };

  function change(body, after) {
    return KUI.api("/bridge", { method: "POST", body: body }).then(function (r) {
      if (r.token) {
        A.showOnce("Yeni köprü token'ı", r.token, "Köprü ve MCP istemcilerine bunu verin; eski panel token'ı artık geçersiz.");
        // the server keeps the new token for this session for a short while: the quick-connect card can
        // offer "token dahil kopyala" once (the page itself never stores it)
        document.dispatchEvent(new CustomEvent("kui:fresh-token"));
      }
      if (r.hook_secret) A.showOnce("Yeni webhook sırrı", r.hook_secret, "Webhook gönderen tarafa (imza anahtarı veya gövdedeki \"secret\" alanı) bunu verin.");
      if (after) after(r);
      return r;
    });
  }

  function surfacesCard(g, reload) {
    var list = h("ul", { class: "list" }, SURFACES.map(function (s) {
      var id = "gate-" + s.id;
      var sw = h("input", { class: "switch", type: "checkbox", role: "switch", id: id, "aria-describedby": id + "-d" });
      sw.checked = !!g.surfaces[s.id];
      sw.addEventListener("change", function () {
        sw.disabled = true;
        change({ action: "set", surface: s.id, enabled: sw.checked }, function () {
          KUI.toast(s.name + (sw.checked ? " açıldı." : " kapatıldı; artık 404 döner."), { kind: "success" });
        }).then(reload).catch(function (e) { sw.checked = !sw.checked; KUI.errorToast(e); }).finally(function () { sw.disabled = false; });
      });
      return h("li", { class: "gate-row" },
        h("div", { class: "grow stack-sm" },
          h("label", { for: id, class: "row" }, h("strong", { class: "small", text: s.name }), h("code", { text: s.path })),
          h("span", { class: "small muted", id: id + "-d", text: s.text })),
        h("div", { class: "row" }, h("span", { class: "badge badge-outline src", text: SOURCE[g.sources[s.id]] || g.sources[s.id] }), sw));
    }));
    var allOff = A.button("Hepsini kapat", { size: "sm", kind: "ghost", icon: "x", onclick: function () {
      KUI.busy(allOff, change({ action: "set_all", enabled: false }).then(function () { KUI.toast("Tüm yüzeyler kapatıldı.", { kind: "success" }); reload(); }).catch(KUI.errorToast));
    } });
    return A.card("Yüzeyler", "Kapalı yüzey kimlik denetiminden önce 404 döner: dışarıdan var olup olmadığı bile görünmez.",
      [list, h("div", { class: "row-between" },
        h("span", { class: "small muted", text: "Başlangıç değeri: BRIDGE_ENABLED, MCP_ENABLED, HOOKS_ENABLED (ortam) → panel → kapalı." }), allOff)]);
  }

  function secretsCard(g, reload) {
    var rotate = A.button(g.panel_token.set ? "Token'ı yenile" : "Token üret", { kind: "primary", size: "sm", icon: "key", onclick: function () {
      var go = function () { KUI.busy(rotate, change({ action: "rotate_token" }).then(reload).catch(KUI.errorToast)); };
      if (!g.panel_token.set) return go();
      KUI.confirm({ title: "Token yenilensin mi?", body: "Eski panel token'ını kullanan her istemci (n8n, MCP) çalışmayı durdurur.", confirmText: "Yenile", cancelText: "Vazgeç", danger: true })
        .then(function (ok) { if (ok) go(); });
    } });
    var revoke = g.panel_token.set ? A.button("İptal et", { size: "sm", kind: "ghost", icon: "trash", onclick: function () {
      KUI.confirm({ title: "Panel token'ı iptal edilsin mi?", body: "Bu token'la gelen istekler 401 alır.", confirmText: "İptal et", cancelText: "Vazgeç", danger: true })
        .then(function (ok) { if (ok) KUI.busy(revoke, change({ action: "revoke_token" }).then(reload).catch(KUI.errorToast)); });
    } }) : null;
    var hookRotate = A.button(g.panel_hook_secret.set ? "Sırrı yenile" : "Sır üret", { size: "sm", icon: "key", onclick: function () {
      KUI.busy(hookRotate, change({ action: "rotate_hook_secret" }).then(reload).catch(KUI.errorToast));
    } });
    var hookRevoke = g.panel_hook_secret.set ? A.button("İptal et", { size: "sm", kind: "ghost", icon: "trash", onclick: function () {
      KUI.busy(hookRevoke, change({ action: "revoke_hook_secret" }).then(reload).catch(KUI.errorToast));
    } }) : null;
    function state(set, at, env, envName) {
      return h("div", { class: "cluster" },
        set ? A.badge("Panel: var" + (at ? " · " + KUI.timeAgo(at) : ""), "success", "success") : A.badge("Panel: yok", "outline"),
        env ? A.badge("Ortam: " + envName, "accent") : null);
    }
    return A.card("Token ve sırlar", "Yeni değer yalnız bir kez gösterilir; sonra hiçbir yerde görünmez (sunucuda özeti saklanır).", [
      h("div", { class: "stack-sm" }, h("span", { class: "label", text: "Köprü / MCP token'ı" }),
        state(g.panel_token.set, g.panel_token.created_at, g.env_token, "BRIDGE_TOKEN"),
        h("div", { class: "cluster" }, rotate, revoke)),
      h("div", { class: "stack-sm" }, h("span", { class: "label", text: "Webhook sırrı" }),
        state(g.panel_hook_secret.set, g.panel_hook_secret.created_at, g.env_hook_secret, "HOOK_SECRET"),
        h("div", { class: "cluster" }, hookRotate, hookRevoke)),
      g.generated_token ? A.callout("info", "Bu süreç başlarken geçici bir token üretip log'a yazdı; panelden token üretince o geçersiz olur.") : null
    ]);
  }

  function examplesCard() {
    var base = location.origin;
    var tabs = [
      { id: "curl", name: "curl", label: "Terminal", text:
        "export BRIDGE_TOKEN=...   # panelde bir kez gösterilen token\n" +
        "curl -s " + base + "/bridge/manifest -H \"Authorization: Bearer $BRIDGE_TOKEN\"\n\n" +
        "curl -s -X POST " + base + "/bridge/call/ai.ask \\\n  -H \"Authorization: Bearer $BRIDGE_TOKEN\" -H \"Content-Type: application/json\" \\\n" +
        "  -d '{\"args\": {\"prompt\": \"Bugünün özetini çıkar\", \"notify\": true}}'" },
      { id: "n8n", name: "n8n", label: "HTTP Request düğümü", text:
        "Method: POST\nURL: " + base + "/bridge/call/ai.ask\nAuthentication: Generic Credential Type > Header Auth\n" +
        "  Name: Authorization\n  Value: Bearer <BRIDGE_TOKEN>\nSend Body: JSON\n" +
        "{\n  \"args\": { \"prompt\": \"{{ $json.text }}\", \"notify\": true }\n}" },
      { id: "claude-code", name: "Claude Code", label: "MCP (HTTP)", text:
        "claude mcp add --transport http km-ai " + base + "/mcp \\\n  --header \"Authorization: Bearer <BRIDGE_TOKEN>\"" },
      { id: "claude-desktop", name: "Claude Desktop", label: "claude_desktop_config.json", text:
        "{\n  \"mcpServers\": {\n    \"km-ai\": {\n      \"command\": \"npx\",\n" +
        "      \"args\": [\"-y\", \"mcp-remote\", \"" + base + "/mcp\", \"--header\", \"Authorization: Bearer ${BRIDGE_TOKEN}\"],\n" +
        "      \"env\": { \"BRIDGE_TOKEN\": \"<BRIDGE_TOKEN>\" }\n    }\n  }\n}" },
      { id: "tradingview", name: "TradingView", label: "Alarm > Webhook URL + Mesaj", text:
        "Webhook URL: " + base + "/hook/notify\n\nMesaj (JSON):\n" +
        "{\n  \"secret\": \"<WEBHOOK_SIRRI>\",\n  \"text\": \"{{ticker}} {{interval}}: {{strategy.order.action}} @ {{close}}\"\n}\n\n" +
        "# \"secret\" alanı doğrulanır ve işleyiciye verilmeden silinir. Mesaj izinli ilk sohbete gider." }
    ];
    var seg = h("div", { class: "segmented", role: "tablist", "aria-label": "Örnekler" });
    var body = h("div");
    function show(t, btn) {
      KUI.$all("button", seg).forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
      KUI.clear(body); body.appendChild(A.codeBlock(t.text, t.label));
    }
    tabs.forEach(function (t, i) {
      var b = h("button", { type: "button", "aria-pressed": i === 0 ? "true" : "false", text: t.name });
      b.addEventListener("click", function () { show(t, b); });
      seg.appendChild(b);
      if (i === 0) show(t, b);
    });
    return A.card("Kopyala-yapıştır örnekler", "Token yerine panelde bir kez gösterilen değeri koyun. İlgili yüzey kapalıysa istek 404 alır.",
      [h("div", { class: "seg-scroll" }, seg), body]);
  }

  function auditCard(g) {
    var rows = g.audit || [];
    return A.card("Denetim kaydı", "Kim, ne zaman, ne değiştirdi", rows.length ? h("ul", { class: "list" }, rows.map(function (a) {
      return h("li", { class: "log-line" }, A.badge(a.action, "outline"),
        h("span", { class: "msg", text: a.detail === undefined || a.detail === null ? "" : JSON.stringify(a.detail) }),
        h("span", { class: "meta", text: KUI.timeAgo(a.ts) + " · " + (a.actor || "?") }));
    })) : h("p", { class: "small muted", text: "Henüz değişiklik yok." }));
  }

  /** "Hızlı kurulum" card (stack_ui quick-connect): copy a prompt that lets Claude Code / another agent set
      up the MCP connection. Passive ("önce MCP'yi açın") while MCP is closed; after a token is created the
      card offers a one-shot "token dahil kopyala" (the token lives briefly on the server, not in the page). */
  var quick = null;
  function quickSlot(g) {
    var slot = h("div", { class: "quick-slot", id: "quick-connect-slot", "data-mcp": g.surfaces.mcp ? "on" : "off" });
    if (window.KUI && typeof window.KUI.quickConnect === "function") {
      quick = window.KUI.quickConnect(slot, { enabled: !!g.surfaces.mcp, client: "claude-code" });
    } else {
      slot.hidden = true;
    }
    return slot;
  }

  function render(main) {
    main.appendChild(A.pageHead("Bağlantılar", "Köprü ve entegrasyonlar",
      "Köprü, MCP ve webhook varsayılan olarak KAPALI. Kullanacağınız yüzeyi açın, token üretin, örneği kopyalayın."));
    main.appendChild(h("div", { class: "callout" }, A.icon("info"), h("div", null,
      h("strong", { text: "Komuta girişi açıksa " }),
      "(varsayılan komuta.yaml) n8n, MCP istemcisi ve webhook gibi dış çağıranlar Komuta kapısında kalır. " +
      "Ya konsoldan / Komuta MCP'den /bridge/, /mcp, /hook/ yollarını girişsiz açın, ya Komuta girişini kapatın " +
      "(examples/komuta-giris-kapali.yaml). Yol bazlı kural desteği gelince yalnız /ui korunabilecek.")));
    var host = h("div", { class: "stack gap-lg" }, A.skeleton(5));
    main.appendChild(host);
    function load() {
      return KUI.api("/bridge").then(function (g) {
        KUI.clear(host);
        host.appendChild(quickSlot(g));
        host.appendChild(h("section", { class: "grid grid-2" }, surfacesCard(g, load), secretsCard(g, load)));
        host.appendChild(h("section", { class: "grid grid-3" }, h("div", { class: "span-2" }, examplesCard()), auditCard(g)));
      }).catch(function (e) { KUI.clear(host); host.appendChild(A.errorBox(e)); });
    }
    load();
  }

  A.pages.bridge = { title: "Köprü ve entegrasyonlar", render: render };
})();
