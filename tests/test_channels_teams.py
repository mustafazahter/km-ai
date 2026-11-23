"""Microsoft Teams: Bot Framework JWT (RS256, JWKS, iss/aud/exp/nbf, serviceUrl binding), SSRF guard, replies."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ai_bot.channels import teams as T
from tests.test_channels_env import TEAMS_PASSWORD, chenv  # noqa: F401

URL = "/channels/teams/messages"
APP_ID = "11111111-2222-3333-4444-555555555555"
AAD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SERVICE = "https://smba.trafficmanager.net/emea/"
KID = "kid-1"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key, kid=KID) -> dict:
    nums = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "use": "sig", "n": b64(nums.n.to_bytes(256, "big")),
            "e": b64(nums.e.to_bytes(3, "big"))}


def make_token(*, key=KEY, kid=KID, alg="RS256", iss=T.ISSUER, aud=APP_ID, exp=None, nbf=None, service=SERVICE,
               claims=None) -> str:
    now = int(time.time())
    payload = {"iss": iss, "aud": aud, "exp": now + 3600 if exp is None else exp,
               "nbf": now - 60 if nbf is None else nbf, "serviceurl": service}
    payload.update(claims or {})
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    signing = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}"
    if alg == "RS256":
        sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    else:
        sig = b"x" * 32
    return f"{signing}.{b64(sig)}"


def activity(text="merhaba", service=SERVICE, aad=AAD, conv_type="personal", act_id="act-1", **extra) -> dict:
    a = {"type": "message", "id": act_id, "serviceUrl": service, "text": text, "channelId": "msteams",
         "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
         "channelData": {"tenant": {"id": "tenant-guid-1"}},
         "from": {"id": "29:user-1", "aadObjectId": aad, "name": "Ali"},
         "recipient": {"id": "28:" + APP_ID, "name": "bot"},
         "conversation": {"id": "a:conv-1", "conversationType": conv_type}}
    a.update(extra)
    return a


def setup_network(chenv, keys=None, counter=None):
    """Fake login.botframework.com (OpenID + JWKS), login.microsoftonline.com (token) and the Bot Connector."""
    keys = [jwk(KEY)] if keys is None else keys
    counter = counter if counter is not None else {"jwks": 0, "token": 0}

    def botframework(r: httpx.Request):
        if r.url.path.endswith("openidconfiguration"):
            return httpx.Response(200, json={"jwks_uri": "https://login.botframework.com/v1/.well-known/keys"})
        counter["jwks"] += 1
        return httpx.Response(200, json={"keys": keys})

    def aad(r: httpx.Request):
        counter["token"] += 1
        return httpx.Response(200, json={"access_token": "tok-" + "x" * 30, "expires_in": 3600})

    chenv.net.routes["login.botframework.com"] = botframework
    chenv.net.routes["login.microsoftonline.com"] = aad
    chenv.net.routes["smba.trafficmanager.net"] = lambda r: httpx.Response(201, json={"id": "1"})
    return counter


def teams_on(chenv, allowed=(AAD,), tenant="tenant-guid-1") -> None:
    chenv.set(TEAMS_ENABLED=True, TEAMS_APP_ID=APP_ID, TEAMS_APP_PASSWORD=TEAMS_PASSWORD, TEAMS_TENANT_ID=tenant,
              TEAMS_ALLOWED_USERS=", ".join(allowed))


def post(c, act: dict, token: str | None = "auto"):
    headers = {}
    if token == "auto":
        token = make_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.post(URL, json=act, headers=headers)


def test_disabled_is_404(chenv):
    c = chenv.client()
    assert post(c, activity()).status_code == 404


def test_valid_token_runs_the_agent_and_replies_to_service_url(chenv):
    teams_on(chenv, tenant="tenant-guid-1")
    setup_network(chenv)
    c = chenv.client()
    assert post(c, activity("<at>bot</at> merhaba  ")).status_code == 200
    chenv.settle(c)
    assert "merhaba" in chenv.llm_texts()  # <at> markup stripped
    token_req = chenv.net.to("login.microsoftonline.com")[0]
    assert token_req.url.path == "/tenant-guid-1/oauth2/v2.0/token"
    form = dict(x.split("=", 1) for x in token_req.content.decode().split("&"))
    assert form["grant_type"] == "client_credentials" and form["client_id"] == APP_ID
    assert form["scope"] == "https%3A%2F%2Fapi.botframework.com%2F.default"
    req = chenv.net.to("smba.trafficmanager.net")[0]
    assert req.url.raw_path.decode() == "/emea/v3/conversations/a%3Aconv-1/activities"
    assert req.headers["authorization"] == "Bearer tok-" + "x" * 30
    body = json.loads(req.content)
    assert body["type"] == "message" and body["text"] == "tamam" and body["replyToId"] == "act-1"
    assert body["from"]["id"] == "28:" + APP_ID and body["recipient"]["id"] == "29:user-1"


def test_default_tenant_is_botframework_com_and_token_is_cached(chenv):
    teams_on(chenv, tenant="")
    counter = setup_network(chenv)
    c = chenv.client()
    post(c, activity(act_id="a1"))
    post(c, activity(act_id="a2"))
    chenv.settle(c)
    assert chenv.net.to("login.microsoftonline.com")[0].url.path == "/botframework.com/oauth2/v2.0/token"
    assert counter["token"] == 1 and counter["jwks"] == 1


@pytest.mark.parametrize("name,kwargs", [
    ("bad aud", {"aud": "someone-else"}),
    ("bad iss", {"iss": "https://sts.windows.net/x/"}),
    ("expired", {"exp": int(time.time()) - 600}),
    ("not yet valid", {"nbf": int(time.time()) + 3600}),
    ("wrong signature", {"key": OTHER_KEY}),
    ("unknown kid", {"kid": "kid-unknown"}),
    ("service url mismatch", {"service": "https://smba.trafficmanager.net/amer/"}),
    ("no service url claim", {"service": ""}),
    ("hs256", {"alg": "HS256"}),
    ("none", {"alg": "none"}),
])
def test_token_rejections(chenv, name, kwargs):
    teams_on(chenv)
    setup_network(chenv)
    c = chenv.client()
    assert post(c, activity(), token=make_token(**kwargs)).status_code == 401, name
    chenv.settle(c)
    assert chenv.llm.requests == [] and chenv.net.to("smba.trafficmanager.net") == []


def test_missing_and_malformed_bearer(chenv):
    teams_on(chenv)
    setup_network(chenv)
    c = chenv.client()
    assert post(c, activity(), token=None).status_code == 401
    assert post(c, activity(), token="not.a.jwt").status_code == 401
    assert post(c, activity(), token="garbage").status_code == 401
    r = c.post(URL, json=activity(), headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


@pytest.mark.parametrize("url", ["http://smba.trafficmanager.net/emea/", "https://evil.example.com/",
                                 "https://169.254.169.254/", "https://smba.trafficmanager.net.evil.com/",
                                 "https://user:pw@smba.trafficmanager.net/", "https://smba.trafficmanager.net:8443/",
                                 "https://botframework.com.evil.io/"])
def test_service_url_that_is_not_bot_framework_is_refused_even_with_a_valid_token(chenv, url):
    teams_on(chenv)
    setup_network(chenv)
    c = chenv.client()
    r = post(c, activity(service=url), token=make_token(service=url))
    assert r.status_code == 403
    chenv.settle(c)
    assert chenv.llm.requests == [] and chenv.net.to("login.microsoftonline.com") == []


def test_service_url_rules():
    ok = ["https://smba.trafficmanager.net/emea/", "https://directline.botframework.com/", "https://x.azure.com/a",
          "https://smba.infra.gcc.teams.microsoft.com/"]
    bad = ["", "ftp://smba.trafficmanager.net/", "https://localhost/", "https://evil.microsoft.com/",
           "https://smba.trafficmanager.net/?x=1", "https://[::1]/"]
    assert all(T.service_url_ok(u) for u in ok)
    assert not any(T.service_url_ok(u) for u in bad)


def test_unknown_kid_refreshes_keys_once_then_accepts(chenv):
    teams_on(chenv)
    keys = [jwk(OTHER_KEY, "old")]
    counter = setup_network(chenv, keys=keys)
    c = chenv.client()
    assert post(c, activity(), token=make_token(kid="kid-1")).status_code == 401  # not in the first fetch
    keys.append(jwk(KEY, "kid-1"))
    chenv.mgr.channels["teams"].keys.attempted = 0  # skip the 30 s refresh gap
    assert post(c, activity(act_id="a9"), token=make_token(kid="kid-1")).status_code == 200
    assert counter["jwks"] == 2


def test_flood_of_unknown_kids_does_not_refetch_keys_every_time(chenv):
    teams_on(chenv)
    counter = setup_network(chenv)
    c = chenv.client()
    for i in range(6):
        assert post(c, activity(), token=make_token(kid=f"random-{i}")).status_code == 401
    assert counter["jwks"] == 1


OTHER_AAD = "99999999-8888-7777-6666-555555555555"


def test_allow_list_matches_aad_object_id_only_and_groups_are_ignored(chenv):
    teams_on(chenv, allowed=(OTHER_AAD,))
    setup_network(chenv)
    c = chenv.client()
    post(c, activity(act_id="s1"))  # aad id not allowed
    # from.id is channel scoped and never matches, even if the allow-list somehow contained it
    no_aad = activity(act_id="s2", aad="")
    post(c, no_aad)
    allowed_act = activity(act_id="ok1", aad=OTHER_AAD)
    post(c, allowed_act)
    group = activity(act_id="g1", conv_type="channel", aad=OTHER_AAD)
    post(c, group)
    group_chat = activity(act_id="g2", conv_type="groupChat", aad=OTHER_AAD)
    post(c, group_chat)
    no_type = activity(act_id="g3", aad=OTHER_AAD)
    del no_type["conversation"]["conversationType"]
    post(c, no_type)
    typing = activity(act_id="t1", type="typing")
    post(c, typing)
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1
    assert chenv.mgr.channels["teams"].view()["denied"]


def test_channel_id_must_be_msteams(chenv):
    teams_on(chenv)
    setup_network(chenv)
    c = chenv.client()
    for i, cid in enumerate(("webchat", "directline", "slack", None)):
        act = activity(act_id=f"c{i}")
        if cid is None:
            del act["channelId"]
        else:
            act["channelId"] = cid
        post(c, act)
    chenv.settle(c)
    assert chenv.llm.requests == []
    post(c, activity(act_id="good"))
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1


def test_tenant_must_match_when_configured(chenv):
    teams_on(chenv, tenant="tenant-guid-1")
    setup_network(chenv)
    c = chenv.client()
    other = activity(act_id="t-other")
    other["channelData"]["tenant"]["id"] = "some-other-tenant"
    post(c, other)
    missing = activity(act_id="t-missing")
    del missing["channelData"]
    post(c, missing)
    chenv.settle(c)
    assert chenv.llm.requests == []
    mixed = activity(act_id="t-case")
    mixed["channelData"]["tenant"]["id"] = "TENANT-GUID-1"
    post(c, mixed)
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1
    # no tenant configured: the field is not required
    teams_on(chenv, tenant="")
    bare = activity(act_id="t-bare")
    del bare["channelData"]
    post(c, bare)
    chenv.settle(c)
    assert len(chenv.llm.requests) == 2


def test_old_activities_are_replays(chenv):
    teams_on(chenv)
    setup_network(chenv)
    c = chenv.client()
    old = activity(act_id="old")
    old["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")
    assert post(c, old).status_code == 200  # acknowledged, not processed
    fresh = activity(act_id="fresh")
    fresh["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=9)).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")
    post(c, fresh)
    notimestamp = activity(act_id="nots")
    del notimestamp["timestamp"]
    post(c, notimestamp)
    chenv.settle(c)
    assert len(chenv.llm.requests) == 2  # fresh + no timestamp; the 11 minute old one was dropped


def test_empty_allow_list_and_dedupe(chenv):
    teams_on(chenv, allowed=())
    setup_network(chenv)
    c = chenv.client()
    post(c, activity())
    chenv.settle(c)
    assert chenv.llm.requests == []
    teams_on(chenv)
    post(c, activity(act_id="same"))
    post(c, activity(act_id="same"))
    chenv.settle(c)
    assert len(chenv.llm.requests) == 1


def test_token_endpoint_error_is_a_safe_code(chenv):
    teams_on(chenv)
    setup_network(chenv)
    chenv.net.routes["login.microsoftonline.com"] = lambda r: httpx.Response(401, json={"error": TEAMS_PASSWORD})
    c = chenv.client()
    post(c, activity())
    chenv.settle(c)
    ch = chenv.mgr.channels["teams"]
    assert ch.last_error["code"] == "teams_auth" and TEAMS_PASSWORD not in json.dumps(ch.view())
