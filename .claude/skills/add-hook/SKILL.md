---
name: add-hook
description: Add an incoming webhook endpoint (/hook/<name>) to this stack with @hook and test it with an HMAC signature. Use for TradingView alerts, n8n flows or any service that POSTs events in.
---

# Add an incoming webhook

1. `plugins/<name>.py`:
   ```python
   from stack_kit import ToolError, emit, hook

   @hook("<name>")                      # TradingView (cannot send headers): @hook("<name>", body_secret_field="secret")
   def on_event(req):                 # req.json(), req.body (raw), req.headers, req.via
       data = req.json()
       if not isinstance(data, dict):
           raise ToolError("JSON object expected", code="bad_payload")
       emit("<name>.received", data)
       return {"accepted": True}
   ```
2. Authentication (either is enough): `X-Signature: sha256=<hmac(HOOK_SECRET, raw body)>` or `Authorization: Bearer <BRIDGE_TOKEN>`. Provide `HOOK_SECRET` as a secret on Komuta. Body limit 256 KiB, handler timeout 10 s.
3. Test: `tests/test_<name>_hook.py`:
   ```python
   from fastapi.testclient import TestClient

   from stack_kit import BridgeConfig, Stack, create_app, hook_signature

   SECRET = "test-hook-secret-0123456789"


   def test_hook_accepts_signed_and_rejects_unsigned():
       stack = Stack("test")
       app = create_app(stack, config=BridgeConfig(token="test-token-0123456789abcdef", hook_secret=SECRET),
                        plugins_dir="plugins")
       body = b'{"hello": "world"}'
       with TestClient(app) as client:
           ok = client.post("/hook/<name>", content=body, headers={"X-Signature": hook_signature(SECRET, body)})
           assert ok.json() == {"ok": True, "result": {"accepted": True}}
           assert client.post("/hook/<name>", content=body).status_code == 401
   ```
   ```bash
   python -m pytest -q tests
   ```
4. Try it by hand on a live service:
   ```bash
   BODY='{"hello":"world"}'
   SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$HOOK_SECRET" | sed 's/^.* //')
   curl -s -X POST https://<service-address>/hook/<name> -H "X-Signature: sha256=$SIG" -H 'Content-Type: application/json' -d "$BODY"
   ```
