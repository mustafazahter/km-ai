"""WhatsApp Cloud API: verify handshake, signature, allow-list, dedupe, rate limit, replies, disabled -> 404."""

from __future__ import annotations

import json
import time

import httpx

from tests.fakes import reply
from tests.test_channels_env import (ALICE, BOB, PHONE_ID, WA_APP_SECRET, WA_TOKEN, WA_VERIFY, chenv,  # noqa: F401
                                     wa_sign)

URL = "/channels/whatsapp/webhook"


def payload(text="merhaba", sender=ALICE, msg_id="wamid.1", phone_id=PHONE_ID, mtype="text", age=0) -> bytes:
    msg = {"from": sender, "id": msg_id, "type": mtype, "timestamp": str(int(time.time()) - age)}
    if mtype == "text":
        msg["text"] = {"body": text}
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_id},
                                       "messages": [msg]}}]}]}).encode()


def post(c, body: bytes, sig: str | None = "auto"):
    headers = {"Content-Type": "application/json"}
    if sig == "auto":
        sig = wa_sign(body)
    if sig:
        headers["X-Hub-Signature-256"] = sig
    return c.post(URL, content=body, headers=headers)


def ok_graph(chenv):
    chenv.net.routes["graph.facebook.com"] = lambda r: httpx.Response(200, json={"messages": [{"id": "x"}]})


def test_disabled_returns_404_everywhere(chenv):
    c = chenv.client()
    assert c.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": WA_VERIFY, "hub.challenge": "1"}).status_code == 404
    assert post(c, payload()).status_code == 404
    chenv.whatsapp(enabled=False)
    assert post(c, payload()).status_code == 404


def test_verify_handshake(chenv):
    chenv.whatsapp()
    c = chenv.client()
    r = c.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": WA_VERIFY, "hub.challenge": "1158201444"})
    assert r.status_code == 200 and r.text == "1158201444" and r.headers["content-type"].startswith("text/plain")
    bad = c.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"})
    assert bad.status_code == 403
    assert c.get(URL, params={"hub.mode": "subscribe", "hub.challenge": "1"}).status_code == 403
    assert c.get(URL, params={"hub.mode": "unsubscribe", "hub.verify_token": WA_VERIFY, "hub.challenge": "1"}).status_code == 403
    # a challenge with markup is not echoed
    assert c.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": WA_VERIFY,
                              "hub.challenge": "<script>"}).status_code == 400


def test_verify_without_configured_token_is_refused(chenv):
    chenv.set(WHATSAPP_ENABLED=True)
    c = chenv.client()
    assert c.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}).status_code == 403


def test_signature_rejected_before_parsing(chenv):
    chenv.whatsapp()
    c = chenv.client()
    body = payload()
    assert post(c, body, sig=None).status_code == 403  # missing header
    assert post(c, body, sig="sha256=" + "0" * 64).status_code == 403  # wrong
    assert post(c, body, sig=wa_sign(body, "another-secret-0123456789")).status_code == 403
    assert post(c, body, sig=wa_sign(body)[7:]).status_code == 403  # no sha256= prefix
    # invalid JSON with a bad signature is 403, not 400: nothing is parsed before the check
    assert post(c, b"{not json", sig="sha256=" + "0" * 64).status_code == 403
    assert post(c, b"{not json").status_code == 400  # valid signature, broken body
    chenv.settle(c)
    assert chenv.llm.requests == []


def test_no_app_secret_means_nothing_is_accepted(chenv):
    chenv.whatsapp()
    chenv.secrets.delete("WHATSAPP_APP_SECRET")
    chenv.mgr.config.invalidate()
    c = chenv.client()
    body = payload()
    assert post(c, body, sig=wa_sign(body, "")).status_code == 403


def test_message_runs_the_agent_and_replies(chenv):
    chenv.whatsapp()
    ok_graph(chenv)
    c = chenv.client()
    r = post(c, payload("Merhaba bot"))
    assert r.status_code == 200
    chenv.settle(c)
    assert "Merhaba bot" in chenv.llm_texts()
    sent = chenv.net.to("graph.facebook.com")
    assert len(sent) == 1 and sent[0].url.path == f"/v21.0/{PHONE_ID}/messages"
    assert sent[0].headers["authorization"] == f"Bearer {WA_TOKEN}"
    body = json.loads(sent[0].content)
    assert body["messaging_product"] == "whatsapp" and body["to"] == ALICE and body["type"] == "text"
    assert body["text"]["body"] == "tamam"
    assert chenv.mgr.channels["whatsapp"].status() == "ready"


def test_allow_list_enforced_and_normalised(chenv):
    chenv.whatsapp(allowed=("+90 555 111 22 33",))  # stored as typed by the user, matched by digits
    ok_graph(chenv)
    c = chenv.client()
    assert post(c, payload(sender=BOB, msg_id="m-bob")).status_code == 200
    assert post(c, payload(sender=ALICE, msg_id="m-alice")).status_code == 200
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1
    assert [b["to"] for b in chenv.net.bodies("graph.facebook.com")] == [ALICE]
    denied = chenv.mgr.channels["whatsapp"].view()["denied"]
    assert denied and denied[0]["id"] == BOB


def test_empty_allow_list_means_nobody(chenv):
    chenv.whatsapp(allowed=())
    c = chenv.client()
    assert post(c, payload()).status_code == 200
    chenv.settle(c)
    assert chenv.llm.requests == [] and chenv.net.requests == []


def test_duplicate_message_id_is_processed_once(chenv):
    chenv.whatsapp()
    ok_graph(chenv)
    c = chenv.client()
    body = payload(msg_id="wamid.same")
    post(c, body)
    post(c, body)
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1 and len(chenv.net.to("graph.facebook.com")) == 1


def test_rate_limit_per_sender(chenv):
    chenv.whatsapp(allowed=(ALICE, BOB))
    ok_graph(chenv)
    c = chenv.client()
    for i in range(25):
        post(c, payload(msg_id=f"a{i}"))
    post(c, payload(sender=BOB, msg_id="b0"))  # another sender is not affected
    chenv.settle(c)
    assert len(chenv.llm.requests) == 21


def test_non_text_other_number_and_group_noise_ignored(chenv):
    chenv.whatsapp()
    ok_graph(chenv)
    c = chenv.client()
    post(c, payload(mtype="image", msg_id="img1"))
    post(c, payload(phone_id="999", msg_id="other-number"))
    status_only = json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": PHONE_ID},
                                                               "statuses": [{"id": "s", "status": "delivered"}]}}]}]}).encode()
    assert post(c, status_only).status_code == 200
    chenv.settle(c)
    assert chenv.llm.requests == []


def test_long_reply_is_chunked_within_provider_limit(chenv):
    chenv.whatsapp()
    ok_graph(chenv)
    chenv.llm.default = reply(("kelime " * 1500).strip())
    c = chenv.client()
    post(c, payload())
    chenv.settle(c)
    parts = [b["text"]["body"] for b in chenv.net.bodies("graph.facebook.com")]
    assert len(parts) >= 3 and all(len(p) <= 4096 for p in parts)


def test_overlong_input_is_refused_without_calling_the_model(chenv):
    chenv.whatsapp()
    ok_graph(chenv)
    c = chenv.client()
    post(c, payload("x" * 4001))
    chenv.settle(c)
    assert chenv.llm.requests == []
    assert "4000" in chenv.net.bodies("graph.facebook.com")[0]["text"]["body"]


def test_provider_error_is_recorded_as_safe_code(chenv):
    chenv.whatsapp()
    chenv.net.routes["graph.facebook.com"] = lambda r: httpx.Response(401, json={"error": {"message": WA_TOKEN}})
    c = chenv.client()
    post(c, payload())
    chenv.settle(c)
    ch = chenv.mgr.channels["whatsapp"]
    assert ch.status() == "error" and ch.last_error["code"] == "whatsapp_auth"
    assert WA_TOKEN not in json.dumps(ch.view())


def test_secrets_never_logged(chenv, caplog):
    chenv.whatsapp()
    ok_graph(chenv)
    c = chenv.client()
    with caplog.at_level("DEBUG"):
        post(c, payload("gizli mesaj metni"))
        post(c, b"{}", sig="sha256=" + "0" * 64)
        chenv.settle(c)
    logged = caplog.text
    assert "gizli mesaj metni" not in logged
    for secret in (WA_TOKEN, WA_APP_SECRET, WA_VERIFY):
        assert secret not in logged


def test_old_deliveries_are_replays(chenv):
    chenv.whatsapp()
    ok_graph(chenv)
    c = chenv.client()
    post(c, payload(msg_id="old", age=601))  # validly signed, but captured and re-sent later
    post(c, payload(msg_id="old2", age=3600))
    chenv.settle(c)
    assert chenv.llm.requests == []
    post(c, payload(msg_id="fresh", age=590))
    no_ts = json.loads(payload(msg_id="nots"))
    del no_ts["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"]
    post(c, json.dumps(no_ts).encode())
    bad_ts = json.loads(payload(msg_id="badts"))
    bad_ts["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "yesterday"
    post(c, json.dumps(bad_ts).encode())
    chenv.settle(c)
    assert len(chenv.llm.requests) == 3
