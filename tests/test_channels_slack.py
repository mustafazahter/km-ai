"""Slack Events API: signature + replay window, url_verification, retries, bots/subtypes, allow-list, replies."""

from __future__ import annotations

import json
import time

import httpx

from tests.fakes import reply
from tests.test_channels_env import SLACK_SIGNING, SLACK_TOKEN, chenv, slack_sign  # noqa: F401

URL = "/channels/slack/events"
ALICE = "U0ALICE01"


def event(text="selam", user=ALICE, kind="message", channel="D111", event_id="Ev1", **extra) -> bytes:
    ev = {"type": kind, "user": user, "text": text, "channel": channel, "ts": "1700000000.000100"}
    if kind == "message":
        ev["channel_type"] = "im"
    ev.update(extra)
    return json.dumps({"type": "event_callback", "event_id": event_id, "event": ev}).encode()


def post(c, body: bytes, headers: dict | None = None):
    return c.post(URL, content=body, headers=headers if headers is not None else slack_sign(body))


def ok_slack(chenv):
    chenv.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": True, "ts": "1"})


def test_disabled_is_404(chenv):
    c = chenv.client()
    assert post(c, event()).status_code == 404
    chenv.slack(enabled=False)
    assert post(c, event()).status_code == 404


def test_signature_accepted_and_reply_goes_to_same_channel(chenv):
    chenv.slack()
    ok_slack(chenv)
    c = chenv.client()
    assert post(c, event("Merhaba")).status_code == 200
    chenv.settle(c)
    assert "Merhaba" in chenv.llm_texts()
    req = chenv.net.to("slack.com")[0]
    assert req.url.path == "/api/chat.postMessage" and req.headers["authorization"] == f"Bearer {SLACK_TOKEN}"
    assert json.loads(req.content) == {"channel": "D111", "text": "tamam"}


def test_signature_rejections(chenv):
    chenv.slack()
    c = chenv.client()
    body = event()
    good = slack_sign(body)
    assert post(c, body, {"Content-Type": "application/json"}).status_code == 403  # no headers at all
    no_sig = {k: v for k, v in good.items() if k != "X-Slack-Signature"}
    assert post(c, body, no_sig).status_code == 403
    no_ts = {k: v for k, v in good.items() if k != "X-Slack-Request-Timestamp"}
    assert post(c, body, no_ts).status_code == 403
    assert post(c, body, slack_sign(body, secret="other-secret-0123456789")).status_code == 403
    assert post(c, body + b" ", good).status_code == 403  # body changed after signing
    # replay window: 301 s old is refused, 299 s old is fine
    assert post(c, body, slack_sign(body, ts=int(time.time()) - 301)).status_code == 403
    assert post(c, body, slack_sign(body, ts=int(time.time()) + 400)).status_code == 403
    assert post(c, body, slack_sign(body, ts=int(time.time()) - 299)).status_code == 200
    assert post(c, body, {**good, "X-Slack-Request-Timestamp": "abc"}).status_code == 403
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1


def test_url_verification_only_after_a_valid_signature(chenv):
    chenv.slack()
    c = chenv.client()
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    assert post(c, body, {"Content-Type": "application/json"}).status_code == 403
    r = post(c, body)
    assert r.status_code == 200 and r.text == "abc123"


def test_retries_are_acknowledged_without_reprocessing(chenv):
    chenv.slack()
    ok_slack(chenv)
    c = chenv.client()
    body = event(event_id="Ev-retry")
    headers = {**slack_sign(body), "X-Slack-Retry-Num": "1", "X-Slack-Retry-Reason": "http_timeout"}
    assert post(c, body, headers).status_code == 200
    chenv.settle(c)
    assert chenv.llm.requests == []


def test_bots_subtypes_and_channel_messages_are_ignored(chenv):
    chenv.slack()
    ok_slack(chenv)
    c = chenv.client()
    post(c, event(event_id="e1", bot_id="B1"))
    post(c, event(event_id="e2", subtype="message_changed"))
    post(c, event(event_id="e3", subtype="bot_message"))
    ch_msg = json.loads(event(event_id="e4"))
    ch_msg["event"]["channel_type"] = "channel"
    post(c, json.dumps(ch_msg).encode())
    chenv.settle(c)
    assert chenv.llm.requests == []


def test_allow_list_dedupe_and_empty_list(chenv):
    chenv.slack()
    ok_slack(chenv)
    c = chenv.client()
    post(c, event(user="U0STRANGER", event_id="s1"))
    post(c, event(event_id="d1"))
    post(c, event(event_id="d1"))  # same event id
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1
    assert chenv.mgr.channels["slack"].view()["denied"][0]["id"] == "U0STRANGER"
    chenv.slack(allowed=())
    post(c, event(event_id="d2"))
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1


def test_app_mention_answers_in_the_dm_not_in_the_public_channel(chenv):
    chenv.slack()
    ok_slack(chenv)
    c = chenv.client()
    post(c, event("<@U0BOT> hava nasıl?", kind="app_mention", channel="C9", event_id="am1"))
    chenv.settle(c)
    assert "hava nasıl?" in chenv.llm_texts()
    sent = chenv.net.bodies("slack.com")
    answers = [b for b in sent if b["text"] == "tamam"]
    assert len(answers) == 1 and answers[0]["channel"] == ALICE and "thread_ts" not in answers[0]  # DM, never C9
    notes = [b for b in sent if b["channel"] == "C9"]
    assert len(notes) == 1 and notes[0]["thread_ts"] == "1700000000.000100" and "DM" in notes[0]["text"]
    assert notes[0]["text"] != "tamam"  # the channel only learns that an answer was sent


def test_dm_reply_keeps_the_thread(chenv):
    chenv.slack()
    ok_slack(chenv)
    c = chenv.client()
    post(c, event("devam", thread_ts="1699999999.000001", event_id="am2"))
    chenv.settle(c)
    assert json.loads(chenv.net.to("slack.com")[0].content)["thread_ts"] == "1699999999.000001"


def test_team_id_must_match_when_configured(chenv):
    chenv.slack()
    ok_slack(chenv)
    chenv.set(SLACK_TEAM_ID="T0MYTEAM1")
    c = chenv.client()

    def with_team(event_id, team, auths=None):
        data = json.loads(event(event_id=event_id))
        if team is not None:
            data["team_id"] = team
        if auths is not None:
            data["authorizations"] = auths
        return json.dumps(data).encode()

    post(c, with_team("t1", "T0OTHERTEAM"))
    post(c, with_team("t2", None))
    post(c, with_team("t3", "T0MYTEAM1", auths=[{"team_id": "T0OTHERTEAM"}]))
    chenv.settle(c)
    assert chenv.llm.requests == []
    post(c, with_team("t4", "T0MYTEAM1", auths=[{"team_id": "T0MYTEAM1"}]))
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1
    chenv.set(SLACK_TEAM_ID="")  # not configured: not required
    post(c, with_team("t5", None))
    chenv.settle(c)
    assert len(chenv.llm.requests) == 2


def test_long_reply_is_chunked_at_3000(chenv):
    chenv.slack()
    ok_slack(chenv)
    chenv.llm.default = reply(("kelime " * 1500).strip())
    c = chenv.client()
    post(c, event())
    chenv.settle(c)
    parts = [b["text"] for b in chenv.net.bodies("slack.com")]
    assert len(parts) >= 3 and all(len(p) <= 3000 for p in parts)


def test_slack_api_error_maps_to_safe_code(chenv):
    chenv.slack()
    chenv.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": False, "error": "invalid_auth", "x": SLACK_TOKEN})
    c = chenv.client()
    post(c, event())
    chenv.settle(c)
    assert chenv.mgr.channels["slack"].last_error["code"] == "slack_auth"
