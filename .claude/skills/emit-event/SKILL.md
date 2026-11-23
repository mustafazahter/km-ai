---
name: emit-event
description: Emit an event from this stack and push it to WEBHOOKS_OUT (n8n or any receiver) with signatures. Use when something that happens in the stack should notify another system.
---

# Olay yayınla

1. Araç, hook ya da komutun içinde:
   ```python
   from stack_kit import emit

   emit("<ad>.done", {"id": 42})          # ad: küçük harf, nokta/alt çizgi/tire; iş parçacığından da güvenli
   ```
2. Hedef ver (Komuta'da secret `valueFrom.secret` ile):
   ```bash
   WEBHOOKS_OUT_URL=https://n8n.example/webhook/<yol>
   WEBHOOKS_OUT_SECRET=<secret>             # ek hedef: WEBHOOKS_OUT_URL_2 / WEBHOOKS_OUT_SECRET_2 ... _9
   # ya da tek değişken: WEBHOOKS_OUT='url|secret,url2|secret2'
   python -m stack_kit doctor               # "WEBHOOKS_OUT ... N target(s)" (secret yazdırılmaz)
   ```
3. Alıcı her istekte `X-Stack-Event`, `X-Stack-Timestamp`, `X-Stack-Signature: sha256=<hmac(secret, timestamp + "." + ham gövde)>` alır. Python'da `stack_kit.verify_signature(secret, body, ts, sig)`; Node/n8n için kit reposunda `komuta-stack-kit reposundaki docs/RECIPES.md` §5.
4. Teslimat: 2xx başarı; ağ hatası/408/429/5xx → en çok 5 deneme (1, 2, 4, 8 sn). Webhook alamayan sistem `GET /bridge/events?since=<id>` ile yoklayabilir.
5. Test — `tests/test_<ad>_event.py`:
   ```python
   import asyncio

   from stack_kit import Stack, load_plugins


   def test_tool_emits_event():
       stack = Stack("test")
       load_plugins(stack, "plugins")
       asyncio.run(stack.call("<ad>.<eylem>", {}))
       assert stack.event_log.since(0)[-1]["event"] == "<ad>.done"
   ```
   ```bash
   python -m pytest -q tests
   ```
