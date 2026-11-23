"""Panel API of the channels: write-only secrets, validation, live apply, tests mapped to HTTP 424."""

from __future__ import annotations

import json

import httpx

from tests.test_channels_env import (ALICE, EMAIL_PASSWORD, PHONE_ID, SLACK_SIGNING, SLACK_TOKEN, TEAMS_PASSWORD, WA_APP_SECRET,
                                     WA_TOKEN, WA_VERIFY, ChEnv, chenv, slack_sign, wa_sign)  # noqa: F401
from tests.test_channels_teams import APP_ID, setup_network

ALL_SECRETS = [WA_TOKEN, WA_APP_SECRET, WA_VERIFY, SLACK_TOKEN, SLACK_SIGNING, TEAMS_PASSWORD, EMAIL_PASSWORD]


def panel(tmp_path, **kw):
    env = ChEnv(tmp_path, with_panel=True, **kw)
    c = env.panel_client()
    return env, c


def save_whatsapp(c, **extra):
    body = {"enabled": True,
            "values": {"WHATSAPP_PHONE_NUMBER_ID": PHONE_ID, "WHATSAPP_ALLOWED_NUMBERS": [f"+{ALICE[:2]} {ALICE[2:]}"]},
            "secrets": {"WHATSAPP_TOKEN": WA_TOKEN, "WHATSAPP_APP_SECRET": WA_APP_SECRET, "WHATSAPP_VERIFY_TOKEN": WA_VERIFY}}
    body.update(extra)
    return c.put("/ui/api/channels/whatsapp", json=body)


def test_requires_login_and_csrf(tmp_path):
    env = ChEnv(tmp_path, with_panel=True)
    anon = env.client()
    assert anon.get("/ui/api/channels").status_code == 401
    assert anon.put("/ui/api/channels/whatsapp", json={"enabled": True}).status_code in (401, 403)
    c = env.panel_client()
    c.headers.pop("X-CSRF-Token")
    assert c.put("/ui/api/channels/whatsapp", json={"enabled": True}).status_code == 403


def test_default_state_everything_off_and_fields_listed(tmp_path):
    env, c = panel(tmp_path)
    r = c.get("/ui/api/channels")
    assert r.status_code == 200
    chans = {x["name"]: x for x in r.json()["channels"]}
    assert set(chans) == {"whatsapp", "teams", "slack", "email"}
    for x in chans.values():
        assert x["enabled"] is False and x["status"] == "off" and x["allowed"] == [] and x["last_inbound"] is None
        assert x["fields"] and all("label" in f for f in x["fields"])
    assert chans["whatsapp"]["missing"] == ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET",
                                            "WHATSAPP_VERIFY_TOKEN"]
    assert chans["whatsapp"]["paths"] == {"webhook": "/channels/whatsapp/webhook"}
    assert chans["email"]["can_confirm"] is False and chans["slack"]["can_confirm"] is True
    # inbound endpoints are 404 while off
    assert c.post("/channels/slack/events", content=b"{}").status_code == 404


def test_save_applies_live_and_secrets_are_write_only(tmp_path):
    env, c = panel(tmp_path)
    r = save_whatsapp(c)
    assert r.status_code == 200, r.text
    text = r.text
    for secret in ALL_SECRETS:
        assert secret not in text
    ch = r.json()["channel"]
    assert ch["enabled"] and ch["configured"] and ch["status"] == "ready" and ch["allowed"] == [ALICE]
    sec = {f["name"]: f for f in ch["fields"] if f["kind"] == "secret"}
    assert sec["WHATSAPP_TOKEN"]["set"] is True and sec["WHATSAPP_TOKEN"]["preview"].endswith(WA_TOKEN[-4:])
    assert WA_TOKEN not in json.dumps(sec) and sec["WHATSAPP_TOKEN"]["source"] == "ui"
    for path in ("/ui/api/channels", "/ui/api/config", "/ui/api/secrets", "/ui/api/overview", "/ui/api/setup"):
        body = c.get(path).text
        for secret in ALL_SECRETS:
            assert secret not in body, path
    # live: the webhook answers right away with the saved verify token, no restart
    assert c.get("/channels/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": WA_VERIFY,
                                                        "hub.challenge": "42"}).text == "42"
    # audit trail through Panel._save
    assert any(a["name"] == "WHATSAPP_ENABLED" for a in env.store.audit(50))


def test_empty_secret_box_keeps_the_stored_secret(tmp_path):
    env, c = panel(tmp_path)
    save_whatsapp(c)
    r = c.put("/ui/api/channels/whatsapp", json={"values": {"WHATSAPP_PHONE_NUMBER_ID": "1234567"}, "secrets": {"WHATSAPP_TOKEN": ""}})
    assert r.status_code == 200
    assert env.secrets.get("WHATSAPP_TOKEN") == WA_TOKEN


def test_validation_messages_and_unknown_fields(tmp_path):
    env, c = panel(tmp_path)
    bad = c.put("/ui/api/channels/whatsapp", json={"values": {"WHATSAPP_ALLOWED_NUMBERS": ["abc"]}})
    assert bad.status_code == 422 and "ülke koduyla" in bad.json()["error"]["message"]
    assert c.put("/ui/api/channels/slack", json={"values": {"SLACK_ALLOWED_USERS": "alice"}}).status_code == 422
    assert c.put("/ui/api/channels/email", json={"values": {"EMAIL_ALLOWED_SENDERS": "not-an-address"}}).status_code == 422
    assert c.put("/ui/api/channels/email", json={"values": {"EMAIL_POLL_SECONDS": 5}}).status_code == 422  # minimum 30
    assert c.put("/ui/api/channels/whatsapp", json={"values": {"SLACK_ALLOWED_USERS": "U0ABC1234"}}).status_code == 404
    assert c.put("/ui/api/channels/whatsapp", json={"secrets": {"SLACK_BOT_TOKEN": "xoxb-12345678"}}).status_code == 404
    assert c.put("/ui/api/channels/whatsapp", json={"values": {"LLM_MODEL": "x"}}).status_code == 404  # not a channel setting
    assert c.put("/ui/api/channels/nothing", json={}).status_code == 404
    assert c.put("/ui/api/channels/whatsapp", json={"enabled": "yes"}).status_code == 422
    assert env.secrets.names() == []


def test_allow_lists_are_normalised_deduped_and_never_allow_all(tmp_path):
    env, c = panel(tmp_path)
    r = c.put("/ui/api/channels/slack", json={"values": {"SLACK_ALLOWED_USERS": "u0abc1234, U0ABC1234 ,U0DEF5678"}})
    assert r.json()["channel"]["allowed"] == ["U0ABC1234", "U0DEF5678"]
    r = c.put("/ui/api/channels/slack", json={"values": {"SLACK_ALLOWED_USERS": ""}})
    assert r.json()["channel"]["allowed"] == []
    for wildcard in ("*", "all", "everyone"):
        assert c.put("/ui/api/channels/slack", json={"values": {"SLACK_ALLOWED_USERS": wildcard}}).status_code == 422
    assert c.put("/ui/api/channels/email", json={"values": {"EMAIL_ALLOWED_SENDERS": "*@example.com"}}).status_code == 422
    assert c.put("/ui/api/channels/whatsapp", json={"values": {"WHATSAPP_ALLOWED_NUMBERS": "*"}}).status_code == 422


def test_environment_layer_and_panel_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U0ENVUSER1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", SLACK_TOKEN)
    env, c = panel(tmp_path)
    ch = {x["name"]: x for x in c.get("/ui/api/channels").json()["channels"]}["slack"]
    assert ch["allowed"] == ["U0ENVUSER1"]
    f = {x["name"]: x for x in ch["fields"]}
    assert f["SLACK_ALLOWED_USERS"]["source"] == "env" and f["SLACK_BOT_TOKEN"]["source"] == "env"
    assert f["SLACK_BOT_TOKEN"]["set"] is True and SLACK_TOKEN not in json.dumps(ch)
    c.put("/ui/api/channels/slack", json={"values": {"SLACK_ALLOWED_USERS": "U0PANEL001"}})
    ch = {x["name"]: x for x in c.get("/ui/api/channels").json()["channels"]}["slack"]
    assert ch["allowed"] == ["U0PANEL001"]  # panel > env


def test_webhook_urls_use_the_trusted_public_base(tmp_path):
    env = ChEnv(tmp_path, with_panel=True)
    c = env.panel_client()
    r = c.get("/ui/api/channels", headers={"X-Forwarded-Host": "evil.example.net"})
    data = r.json()
    wa = {x["name"]: x for x in data["channels"]}["whatsapp"]
    assert "evil.example.net" not in json.dumps(data)  # a forwarded host header is never trusted
    assert wa["urls"]["webhook"] == "http://testserver/channels/whatsapp/webhook" and wa["base_verified"] is False

    env.app.state.ui.config.public_url = "https://bot.example.com"  # UI_PUBLIC_URL: the trusted public address
    got = {x["name"]: x for x in c.get("/ui/api/channels", headers={"X-Forwarded-Host": "evil.example.net"}).json()["channels"]}
    assert got["slack"]["urls"]["events"] == "https://bot.example.com/channels/slack/events"
    assert got["teams"]["urls"]["messages"] == "https://bot.example.com/channels/teams/messages"
    assert got["slack"]["base_verified"] is True and got["email"]["urls"] == {}


# ------------------------------------------------------------------ test buttons -> 424


def test_whatsapp_test_ok_and_error_mapping(tmp_path):
    env, c = panel(tmp_path)
    save_whatsapp(c)
    env.net.routes["graph.facebook.com"] = lambda r: httpx.Response(200, json={"display_phone_number": "+90 555 000 11 22", "id": PHONE_ID})
    r = c.post("/ui/api/channels/whatsapp/test")
    assert r.status_code == 200 and r.json()["ok"] is True
    req = env.net.to("graph.facebook.com")[-1]
    assert req.method == "GET" and req.url.path == f"/v21.0/{PHONE_ID}" and req.headers["authorization"] == f"Bearer {WA_TOKEN}"
    assert "+90 555" not in r.text and WA_TOKEN not in r.text  # provider data is not echoed
    count = len(env.net.requests)

    env.net.routes["graph.facebook.com"] = lambda r: httpx.Response(401, json={"error": {"message": f"Invalid {WA_TOKEN}"}})
    bad = c.post("/ui/api/channels/whatsapp/test")
    assert bad.status_code == 424  # never 502
    assert bad.json()["error"]["code"] == "whatsapp_auth" and "reddetti" in bad.json()["error"]["message"]
    assert WA_TOKEN not in bad.text and "Invalid" not in bad.text
    assert len(env.net.requests) == count + 1  # one GET, no message to anybody

    def timeout(r):
        raise httpx.ConnectTimeout("slow", request=r)

    env.net.routes["graph.facebook.com"] = timeout
    t = c.post("/ui/api/channels/whatsapp/test")
    assert t.status_code == 424 and t.json()["error"]["code"] == "whatsapp_timeout"
    env.net.routes["graph.facebook.com"] = lambda r: httpx.Response(500, text="<html>boom</html>")
    s = c.post("/ui/api/channels/whatsapp/test")
    assert s.status_code == 424 and "boom" not in s.text
    # the failure shows on the card
    ch = {x["name"]: x for x in c.get("/ui/api/channels").json()["channels"]}["whatsapp"]
    assert ch["status"] == "error" and ch["last_error"]["code"] == "whatsapp_provider_down"


def test_test_without_settings_is_a_clear_424_not_a_crash(tmp_path):
    env, c = panel(tmp_path)
    for name, code in (("whatsapp", "whatsapp_not_configured"), ("slack", "slack_not_configured"),
                       ("teams", "teams_not_configured"), ("email", "email_not_configured")):
        r = c.post(f"/ui/api/channels/{name}/test")
        assert r.status_code == 424 and r.json()["error"]["code"] == code, name
    assert env.net.requests == []


def test_slack_test_uses_auth_test_only(tmp_path):
    env, c = panel(tmp_path)
    c.put("/ui/api/channels/slack", json={"secrets": {"SLACK_BOT_TOKEN": SLACK_TOKEN, "SLACK_SIGNING_SECRET": SLACK_SIGNING}})
    env.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": True, "team": "Takım", "user": "bot"})
    r = c.post("/ui/api/channels/slack/test")
    assert r.status_code == 200 and "Takım" not in r.text
    assert [x.url.path for x in env.net.to("slack.com")] == ["/api/auth.test"]
    env.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    bad = c.post("/ui/api/channels/slack/test")
    assert bad.status_code == 424 and bad.json()["error"]["code"] == "slack_auth" and "invalid_auth" not in bad.text


def test_teams_test_fetches_a_bot_token(tmp_path):
    env, c = panel(tmp_path)
    c.put("/ui/api/channels/teams", json={"values": {"TEAMS_APP_ID": APP_ID}, "secrets": {"TEAMS_APP_PASSWORD": TEAMS_PASSWORD}})
    setup_network(env)
    r = c.post("/ui/api/channels/teams/test")
    assert r.status_code == 200 and "tok-" not in r.text
    env.net.routes["login.microsoftonline.com"] = lambda r: httpx.Response(401, json={"error": "invalid_client", "secret": TEAMS_PASSWORD})
    env.mgr.channels["teams"]._token = None
    bad = c.post("/ui/api/channels/teams/test")
    assert bad.status_code == 424 and bad.json()["error"]["code"] == "teams_auth"
    assert TEAMS_PASSWORD not in bad.text and "invalid_client" not in bad.text


def test_email_test_logs_in_and_noops_without_sending(tmp_path):
    env, c = panel(tmp_path)
    c.put("/ui/api/channels/email", json={
        "values": {"EMAIL_IMAP_HOST": "imap.example.org", "EMAIL_IMAP_USER": "bot@example.org",
                   "EMAIL_SMTP_HOST": "smtp.example.org", "EMAIL_FROM": "bot@example.org"},
        "secrets": {"EMAIL_PASSWORD": EMAIL_PASSWORD}})
    ch = env.mgr.channels["email"]
    events = []

    class Imap:
        def select(self, box, readonly=False):
            events.append(("select", readonly))

        def logout(self):
            events.append("logout")

    class Smtp:
        def noop(self):
            events.append("noop")

        def send_message(self, m):
            events.append("SEND")

        def quit(self):
            events.append("quit")

    ch._open_imap = lambda: Imap()
    ch._open_smtp = lambda: Smtp()
    r = c.post("/ui/api/channels/email/test")
    assert r.status_code == 200 and "SEND" not in events and ("select", True) in events and "noop" in events

    import smtplib

    def refuse():
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted " + EMAIL_PASSWORD.encode())

    ch._open_smtp = refuse
    bad = c.post("/ui/api/channels/email/test")
    assert bad.status_code == 424 and bad.json()["error"]["code"] == "email_auth"
    assert EMAIL_PASSWORD not in bad.text and "not accepted" not in bad.text


# ------------------------------------------------------------------ generated secret


def test_verify_token_is_generated_and_shown_once(tmp_path):
    env, c = panel(tmp_path)
    r = c.post("/ui/api/channels/whatsapp/secret", json={})
    assert r.status_code == 200
    data = r.json()
    value = data["value"]
    assert data["name"] == "WHATSAPP_VERIFY_TOKEN" and len(value) >= 24
    assert data["secret"]["set"] is True and data["secret"]["preview"].endswith(value[-4:]) and value not in json.dumps(data["secret"])
    assert env.secrets.get("WHATSAPP_VERIFY_TOKEN") == value
    for path in ("/ui/api/channels", "/ui/api/secrets", "/ui/api/config"):
        assert value not in c.get(path).text  # never again
    second = c.post("/ui/api/channels/whatsapp/secret", json={}).json()["value"]
    assert second != value
    assert c.post("/ui/api/channels/slack/secret", json={}).status_code == 404
    assert c.post("/ui/api/channels/whatsapp/secret", json={"name": "WHATSAPP_TOKEN"}).status_code == 404


def test_panel_saved_values_apply_to_the_next_webhook_call(tmp_path):
    env, c = panel(tmp_path)
    c.put("/ui/api/channels/slack", json={"enabled": True, "values": {"SLACK_ALLOWED_USERS": "U0ALICE01"},
                                          "secrets": {"SLACK_BOT_TOKEN": SLACK_TOKEN, "SLACK_SIGNING_SECRET": SLACK_SIGNING}})
    env.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": True})
    body = json.dumps({"type": "event_callback", "event_id": "E9", "event": {
        "type": "message", "channel_type": "im", "user": "U0ALICE01", "text": "selam", "channel": "D1", "ts": "1.1"}}).encode()
    r = c.post("/channels/slack/events", content=body, headers=slack_sign(body))
    assert r.status_code == 200
    env.settle(c)
    assert len(env.net.to("slack.com")) == 1
    c.put("/ui/api/channels/slack", json={"enabled": False})
    assert c.post("/channels/slack/events", content=body, headers=slack_sign(body)).status_code == 404


def test_teams_allow_list_takes_entra_object_ids_only(tmp_path):
    env, c = panel(tmp_path)
    guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    r = c.put("/ui/api/channels/teams", json={"values": {"TEAMS_ALLOWED_USERS": guid.upper()}})
    assert r.status_code == 200 and r.json()["channel"]["allowed"] == [guid]
    for bad in ("29:user-1", "alice@example.com", "1234"):
        r = c.put("/ui/api/channels/teams", json={"values": {"TEAMS_ALLOWED_USERS": bad}})
        assert r.status_code == 422 and "Object ID" in r.json()["error"]["message"], bad


def test_new_hardening_settings_are_exposed_and_validated(tmp_path):
    env, c = panel(tmp_path)
    chans = {x["name"]: x for x in c.get("/ui/api/channels").json()["channels"]}
    assert "SLACK_TEAM_ID" in {f["name"] for f in chans["slack"]["fields"]}
    assert "EMAIL_AUTHSERV_ID" in {f["name"] for f in chans["email"]["fields"]}
    assert c.put("/ui/api/channels/slack", json={"values": {"SLACK_TEAM_ID": "T0MYTEAM1"}}).status_code == 200
    assert c.put("/ui/api/channels/slack", json={"values": {"SLACK_TEAM_ID": "not valid!"}}).status_code == 422
    assert c.put("/ui/api/channels/email", json={"values": {"EMAIL_AUTHSERV_ID": "mx.example.org"}}).status_code == 200
    assert c.put("/ui/api/channels/email", json={"values": {"EMAIL_AUTHSERV_ID": "bad id;x"}}).status_code == 422
    assert env.mgr.config.text("SLACK_TEAM_ID") == "T0MYTEAM1" and env.mgr.config.text("EMAIL_AUTHSERV_ID") == "mx.example.org"


def test_wizard_channel_step_follows_the_channel_panel(tmp_path):
    env, c = panel(tmp_path)
    st = c.post("/ui/api/setup/channel", json={"channel": "slack"}).json()
    step = {x["id"]: x for x in st["steps"]}["channel_setup"]
    assert step["channel"] == "slack" and step["state"] == "todo" and "Kapalı" in step["detail"]
    r = c.put("/ui/api/channels/slack", json={
        "enabled": True,
        "secrets": {"SLACK_BOT_TOKEN": SLACK_TOKEN, "SLACK_SIGNING_SECRET": SLACK_SIGNING}})
    assert r.status_code == 200, r.text
    step = {x["id"]: x for x in c.get("/ui/api/setup").json()["steps"]}["channel_setup"]
    assert step["state"] == "todo" and "listesi boş" in step["detail"]  # on and configured, but nobody is allowed yet
    assert c.put("/ui/api/channels/slack", json={"values": {"SLACK_ALLOWED_USERS": "U0ABC1234"}}).status_code == 200
    step = {x["id"]: x for x in c.get("/ui/api/setup").json()["steps"]}["channel_setup"]
    assert step["state"] == "done" and "1 izinli" in step["detail"]
    assert all(v not in json.dumps(c.get("/ui/api/setup").json()) for v in (SLACK_TOKEN, SLACK_SIGNING))
