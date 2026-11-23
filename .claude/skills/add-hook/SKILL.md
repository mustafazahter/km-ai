---
name: add-hook
description: Add an incoming webhook endpoint (/hook/<name>) to this stack with @hook and test it with an HMAC signature. Use for TradingView alerts, n8n flows or any service that POSTs events in.
---

# Gelen webhook ekle

1. `plugins/<ad>.py`:
   ```python
   from stack_kit import ToolError, emit, hook

   @hook("<ad>")                      # TradingView (başlık atamaz): @hook("<ad>", body_secret_field="secret")
   def on_event(req):                 # req.json(), req.body (ham), req.headers, req.via
       data = req.json()
       if not isinstance(data, dict):
           raise ToolError("JSON nesne bekleniyor", code="bad_payload")
       emit("<ad>.received", data)
       return {"accepted": True}
   ```
2. Kimlik (biri yeterli): `X-Signature: sha256=<hmac(HOOK_SECRET, ham gövde)>` veya `Authorization: Bearer <BRIDGE_TOKEN>`. `HOOK_SECRET`'ı Komuta'da secret olarak ver. Gövde 256 KiB, işleyici 10 sn.
3. Test — `tests/test_<ad>_hook.py`:
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
           ok = client.post("/hook/<ad>", content=body, headers={"X-Signature": hook_signature(SECRET, body)})
           assert ok.json() == {"ok": True, "result": {"accepted": True}}
           assert client.post("/hook/<ad>", content=body).status_code == 401
   ```
   ```bash
   python -m pytest -q tests
   ```
4. Canlı serviste elle deneme:
   ```bash
   BODY='{"hello":"world"}'
   SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$HOOK_SECRET" | sed 's/^.* //')
   curl -s -X POST https://<servis-adresi>/hook/<ad> -H "X-Signature: sha256=$SIG" -H 'Content-Type: application/json' -d "$BODY"
   ```
