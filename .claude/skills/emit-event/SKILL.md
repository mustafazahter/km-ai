---
name: emit-event
description: Emit an event from this stack and push it to WEBHOOKS_OUT (n8n or any receiver) with signatures. Use when something that happens in the stack should notify another system.
---

# Emit an event

1. Inside a tool, hook or command:
   ```python
   from stack_kit import emit

   emit("<name>.done", {"id": 42})          # name: lower case, dot/underscore/hyphen; safe from a thread too
   ```
2. Set a target (on Komuta the secret goes through `valueFrom.secret`):
   ```bash
   WEBHOOKS_OUT_URL=https://n8n.example/webhook/<yol>
   WEBHOOKS_OUT_SECRET=<secret>             # extra target: WEBHOOKS_OUT_URL_2 / WEBHOOKS_OUT_SECRET_2 ... _9
   # or a single variable: WEBHOOKS_OUT='url|secret,url2|secret2'
   python -m stack_kit doctor               # "WEBHOOKS_OUT ... N target(s)" (the secret is not printed)
   ```
3. On every request the receiver gets `X-Stack-Event`, `X-Stack-Timestamp`, `X-Stack-Signature: sha256=<hmac(secret, timestamp + "." + raw body)>`. In Python use `stack_kit.verify_signature(secret, body, ts, sig)`; for Node/n8n see `docs/RECIPES.md` §5 in the komuta-stack-kit repo.
4. Delivery: 2xx is success; network error/408/429/5xx → at most 5 attempts (1, 2, 4, 8 s). A system that cannot receive webhooks can poll with `GET /bridge/events?since=<id>`.
5. Test: `tests/test_<name>_event.py`:
   ```python
   import asyncio

   from stack_kit import Stack, load_plugins


   def test_tool_emits_event():
       stack = Stack("test")
       load_plugins(stack, "plugins")
       asyncio.run(stack.call("<name>.<action>", {}))
       assert stack.event_log.since(0)[-1]["event"] == "<name>.done"
   ```
   ```bash
   python -m pytest -q tests
   ```
