"""chat_id mapping, resolve_chat gating, deliver() dispatch, human confirmation by one-time code."""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from stack_kit import Stack
from ai_bot.channels.base import CHAT_BASE, CHAT_SPAN, Inbound, chat_id_for, chunk_text, is_channel_chat
from ai_bot.core import CHANNEL_TURN
from tests.conftest import CHAT
from tests.fakes import reply, tool_call
from tests.test_channels_env import ALICE, BOB, ChEnv, chenv, wa_sign  # noqa: F401
from tests.test_channels_whatsapp import ok_graph, payload, post


def forbidden(env, chat_id, *, in_turn=True) -> bool:
    """resolve_chat refused? ``in_turn``: inside the turn that channel user started (CHANNEL_TURN set)."""
    token = CHANNEL_TURN.set(chat_id) if in_turn else None
    try:
        env.ai.resolve_chat(chat_id)
    except Exception as exc:
        return getattr(exc, "code", "") == "forbidden"
    finally:
        if token is not None:
            CHANNEL_TURN.reset(token)
    return False


def resolves(env, chat_id) -> int:
    token = CHANNEL_TURN.set(chat_id)
    try:
        return env.ai.resolve_chat(chat_id)
    finally:
        CHANNEL_TURN.reset(token)


# ------------------------------------------------------------------ mapping


def test_chat_id_mapping_is_stable_negative_and_far_from_telegram_ids():
    a = chat_id_for("whatsapp", ALICE)
    assert a == chat_id_for("whatsapp", ALICE) and a < 0
    assert a != chat_id_for("whatsapp", BOB) and a != chat_id_for("slack", ALICE) and a != chat_id_for("teams", ALICE)
    for ch in ("whatsapp", "teams", "slack", "email"):
        for i in range(200):
            cid = chat_id_for(ch, f"user{i}")
            assert -(2 ** 53 - 1) <= cid <= -(2 ** 52) and is_channel_chat(cid)
            assert abs(cid) <= 2 ** 53 - 1 and cid == int(float(cid))  # JS-safe integer: the panel pages parse JSON numbers
    assert not is_channel_chat(-1001234567890) and not is_channel_chat(123456) and not is_channel_chat(True)
    assert chat_id_for("whatsapp", ALICE) == -(CHAT_BASE + int.from_bytes(
        __import__("hashlib").sha256(b"whatsapp:" + ALICE.encode()).digest()[:8], "big") % CHAT_SPAN)
    # no collision with Telegram: users are positive and below 2**52, groups are around -1e12 .. -1e13
    assert not is_channel_chat(2 ** 52 - 1) and not is_channel_chat(-10 ** 13) and not is_channel_chat(-(2 ** 52) + 1)
    assert not is_channel_chat(-(2 ** 53))


def test_chunk_text():
    assert chunk_text("kısa", 10) == ["kısa"] and chunk_text("", 20) == ["(boş yanıt)"]
    parts = chunk_text("a" * 25, 10)
    assert parts == ["a" * 10, "a" * 10, "a" * 5]
    text = "birinci paragraf\n\nikinci paragraf burada devam eder"
    assert all(len(p) <= 30 for p in chunk_text(text, 30)) and "".join(chunk_text(text, 30)).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


# ------------------------------------------------------------------ resolve_chat gating


def test_resolve_chat_accepts_only_allow_listed_enabled_channel_users(tmp_path):
    env = ChEnv(tmp_path)
    alice, bob = chat_id_for("whatsapp", ALICE), chat_id_for("whatsapp", BOB)
    assert forbidden(env, alice)  # channel off by default
    env.whatsapp(allowed=(ALICE,))
    assert resolves(env, alice) == alice
    assert forbidden(env, bob) and forbidden(env, chat_id_for("slack", ALICE))
    env.whatsapp(allowed=(ALICE,), enabled=False)
    assert forbidden(env, alice)  # disabled again
    env.whatsapp(allowed=())
    assert forbidden(env, alice)  # empty list = nobody, never "everybody"


def test_channel_chat_ids_are_not_reachable_from_outside_their_own_turn(tmp_path):
    """Bridge / MCP / hook / ai.ask callers carry no CHANNEL_TURN: they cannot address a channel user's chat."""
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    alice, bob = chat_id_for("whatsapp", ALICE), chat_id_for("whatsapp", BOB)
    assert forbidden(env, alice, in_turn=False)
    token = CHANNEL_TURN.set(bob)  # a turn of ANOTHER user does not open Alice's chat either
    try:
        assert forbidden(env, alice, in_turn=False)
    finally:
        CHANNEL_TURN.reset(token)
    for tool, args in (("memory.list", {"chat_id": alice}), ("memory.recall", {"chat_id": alice}),
                       ("schedule.list", {"chat_id": alice}), ("ai.ask", {"prompt": "x", "chat_id": alice, "notify": True})):
        out = asyncio.run(env.stack.call(tool, args, allow_shortcut=False))
        assert out.get("ok") is False and out["error"]["code"] == "forbidden", tool
    assert env.llm.requests == [] and env.net.requests == []


def test_channel_turn_variable_is_reset_after_the_turn(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    cid = chat_id_for("whatsapp", ALICE)
    asyncio.run(env.ai.run_ai(cid, "merhaba"))
    assert CHANNEL_TURN.get() is None


def test_tools_inside_a_channel_turn_work_for_that_user(tmp_path):
    env = ChEnv(tmp_path, script=[reply(calls=[tool_call("memory_remember", {"content": "Kullanıcı çay sever"})]), reply("tamam")])
    env.whatsapp(allowed=(ALICE,))
    cid = chat_id_for("whatsapp", ALICE)
    # remember after outside content needs a human: here the turn is clean, so it just runs
    asyncio.run(env.ai.run_ai(cid, "çay sevdiğimi hatırla"))
    assert env.ai.db.count_memories(cid) == 1 and env.ai.db.count_memories(CHAT) == 0


def test_telegram_behaviour_is_unchanged(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    assert env.ai.resolve_chat(CHAT) == CHAT and env.ai.resolve_chat(None) == CHAT
    assert forbidden(env, 999, in_turn=False) and forbidden(env, -1001234567890, in_turn=False)
    env.ai.settings.allowed_chat_ids = []
    assert forbidden(env, CHAT, in_turn=False)  # empty ALLOWED_CHAT_IDS still disables Telegram chat tools
    assert resolves(env, chat_id_for("whatsapp", ALICE)) == chat_id_for("whatsapp", ALICE)  # in their own turn
    try:
        env.ai.resolve_chat(None)
        raise AssertionError("None must not default to a channel user")
    except Exception as exc:
        assert getattr(exc, "code", "") == "forbidden"


def test_memory_is_per_channel_user_and_separate_from_telegram(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    alice, bob = chat_id_for("whatsapp", ALICE), chat_id_for("whatsapp", BOB)

    async def go(chat):
        token = CHANNEL_TURN.set(chat)
        try:
            return await env.stack.call("memory.remember", {"content": "Kullanıcı çay sever", "chat_id": chat}, allow_shortcut=False)
        finally:
            CHANNEL_TURN.reset(token)

    assert asyncio.run(go(alice)).get("ok") is True
    no = asyncio.run(go(bob))
    assert no.get("ok") is False and no["error"]["code"] == "forbidden"
    assert env.ai.db.count_memories(alice) == 1 and env.ai.db.count_memories(CHAT) == 0


# ------------------------------------------------------------------ deliver dispatch


def test_deliver_dispatches_to_the_right_channel(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    asyncio.run(env.ai.deliver(chat_id_for("whatsapp", ALICE), "hatırlatma: çamaşır"))
    assert [b["to"] for b in env.net.bodies("graph.facebook.com")] == [ALICE]
    assert env.net.bodies("graph.facebook.com")[0]["text"]["body"] == "hatırlatma: çamaşır"
    assert env.tg.sent() == []  # not Telegram
    asyncio.run(env.ai.deliver(CHAT, "telegram mesajı"))  # Telegram path untouched
    assert env.tg.texts(CHAT) == ["telegram mesajı"]


def test_deliver_drops_messages_for_disabled_or_removed_users(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    cid = chat_id_for("whatsapp", ALICE)
    env.whatsapp(allowed=(ALICE,), enabled=False)
    asyncio.run(env.ai.deliver(cid, "x"))
    env.whatsapp(allowed=(BOB,))
    asyncio.run(env.ai.deliver(cid, "x"))
    assert env.net.requests == [] and env.tg.sent() == []


def test_deliver_slack_user_dm_and_email_address(tmp_path):
    env = ChEnv(tmp_path)
    env.slack(allowed=("U0ALICE01",))
    env.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": True})
    asyncio.run(env.ai.deliver(chat_id_for("slack", "U0ALICE01"), "selam"))
    assert env.net.bodies("slack.com") == [{"channel": "U0ALICE01", "text": "selam"}]
    env.set(EMAIL_ENABLED=True, EMAIL_IMAP_HOST="i.example.org", EMAIL_IMAP_USER="b@example.org", EMAIL_SMTP_HOST="s.example.org",
            EMAIL_FROM="b@example.org", EMAIL_PASSWORD="mail-password-0123", EMAIL_ALLOWED_SENDERS="ali@example.com")
    sent = []

    class Smtp:
        def send_message(self, m):
            sent.append(m)

        def quit(self):
            pass

    env.mgr.channels["email"]._open_smtp = lambda: Smtp()
    asyncio.run(env.ai.deliver(chat_id_for("email", "ali@example.com"), "e-posta hatırlatma"))
    assert sent[0]["To"] == "ali@example.com" and sent[0]["Subject"] == "Re: ai-bot"


# ------------------------------------------------------------------ confirmation by one-time code


async def wait_pending(ch, cid):
    for _ in range(300):
        if cid in ch.pending:
            return ch.pending[cid]
        await asyncio.sleep(0.01)
    raise AssertionError("no confirmation was requested")


def reply_in(ch, sender, text):
    return ch.process(Inbound(sender=sender, text=text, msg_id=None, ctx={"to": sender}))


def prompt_texts(env):
    return [b["text"]["body"] for b in env.net.bodies("graph.facebook.com")]


def start_confirmation(env, ch, sender=ALICE):
    cid = chat_id_for("whatsapp", sender)
    env.mgr.remember(cid, "whatsapp", sender, {"to": sender})
    return cid, asyncio.ensure_future(ch.confirm(cid, "x.buy", {"symbol": "XAU"}))


def test_confirmation_correct_code_from_same_sender_approves(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE, BOB))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]

    async def scenario():
        cid, task = start_confirmation(env, ch)
        pend = await wait_pending(ch, cid)
        text = prompt_texts(env)[0]
        assert "x.buy" in text and f"EVET {pend.code}" in text and "HAYIR" in text and "XAU" in text
        await reply_in(ch, ALICE, f"evet {pend.code}")
        return await task

    assert asyncio.run(scenario()) is True
    assert any("Onaylandı" in t for t in prompt_texts(env))


def test_confirmation_code_from_another_sender_is_refused(tmp_path):
    env = ChEnv(tmp_path, script=[reply("bob'a normal cevap")])
    env.whatsapp(allowed=(ALICE, BOB))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]
    ch.min_confirm_timeout = 0.2
    env.ai.settings.confirm_timeout = 0.3

    async def scenario():
        cid, task = start_confirmation(env, ch)
        pend = await wait_pending(ch, cid)
        await reply_in(ch, BOB, f"EVET {pend.code}")  # Bob is allowed, but it is Alice's confirmation
        assert not pend.future.done() and cid in ch.pending
        return await task  # nobody else can approve: it times out

    assert asyncio.run(scenario()) is False
    assert any(t.startswith("EVET ") for t in env.llm_texts())  # Bob's text just went to the agent as a normal message


def test_confirmation_timeout_means_no(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]
    ch.min_confirm_timeout = 0.1
    env.ai.settings.confirm_timeout = 0.2

    async def scenario():
        cid, task = start_confirmation(env, ch)
        result = await task
        return result, cid

    result, cid = asyncio.run(scenario())
    assert result is False and cid not in ch.pending
    assert any("Süre doldu" in t for t in prompt_texts(env))


def test_confirmation_wrong_code_hayir_and_any_other_text_are_a_no(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]

    async def attempt(text_for):
        cid, task = start_confirmation(env, ch)
        pend = await wait_pending(ch, cid)
        await reply_in(ch, ALICE, text_for(pend.code))
        return await task

    assert asyncio.run(attempt(lambda code: "EVET 000000" if code != "000000" else "EVET 111111")) is False
    assert asyncio.run(attempt(lambda code: "HAYIR")) is False
    assert asyncio.run(attempt(lambda code: "evet")) is False  # no code
    assert asyncio.run(attempt(lambda code: "tamam yap")) is False
    assert asyncio.run(attempt(lambda code: f"EVET {code} lütfen")) is False  # code must stand alone


def test_late_reply_after_the_decision_is_a_normal_message(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]

    async def scenario():
        cid, task = start_confirmation(env, ch)
        pend = await wait_pending(ch, cid)
        await reply_in(ch, ALICE, f"EVET {pend.code}")
        assert await task is True
        await reply_in(ch, ALICE, f"EVET {pend.code}")  # replay of the same code

    asyncio.run(scenario())
    assert len(env.llm.requests) == 1  # the replay went to the agent as ordinary text, it approves nothing


def test_codes_are_random_six_digits(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]
    codes = set()

    async def scenario():
        for _ in range(5):
            cid, task = start_confirmation(env, ch)
            pend = await wait_pending(ch, cid)
            codes.add(pend.code)
            await reply_in(ch, ALICE, "HAYIR")
            await task

    asyncio.run(scenario())
    assert len(codes) >= 4 and all(re.fullmatch(r"\d{6}", c) for c in codes)


def test_full_flow_through_the_webhook_runs_the_tool_only_after_confirmation(tmp_path):
    stack = Stack("ai-bot", "test")
    executed: list = []

    @stack.tool("x.buy", "Buy", confirm=True, scope="trade")
    def buy(symbol: str) -> dict:
        executed.append(symbol)
        return {"ticket": 1}

    env = ChEnv(tmp_path, stack=stack, script=[reply(calls=[tool_call("x_buy", {"symbol": "XAU"})]), reply("Alım yapıldı.")])
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    c = env.client()
    try:
        post(c, payload("altın al", msg_id="w1"))
        code = None
        for _ in range(300):
            texts = prompt_texts(env)
            found = [re.search(r"EVET (\d{6})", t) for t in texts]
            found = [m for m in found if m]
            if found:
                code = found[0].group(1)
                break
            asyncio.run(asyncio.sleep(0.01))
        assert code and executed == []  # waiting for the human
        post(c, payload(f"EVET {code}", msg_id="w2"))
        env.settle(c)
        assert executed == ["XAU"]
        assert "Alım yapıldı." in prompt_texts(env)
    finally:
        c.__exit__(None, None, None)


def test_full_flow_denied_never_runs_the_tool(tmp_path):
    stack = Stack("ai-bot", "test")
    executed: list = []

    @stack.tool("x.buy", "Buy", confirm=True, scope="trade")
    def buy(symbol: str) -> dict:
        executed.append(symbol)
        return {"ticket": 1}

    env = ChEnv(tmp_path, stack=stack, script=[reply(calls=[tool_call("x_buy", {"symbol": "XAU"})]), reply("İptal edildi.")])
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    c = env.client()
    try:
        post(c, payload("altın al", msg_id="w1"))
        for _ in range(300):
            if any("EVET" in t for t in prompt_texts(env)):
                break
            asyncio.run(asyncio.sleep(0.01))
        post(c, payload("HAYIR", msg_id="w2"))
        env.settle(c)
        assert executed == []
        tool_msgs = [json.loads(m["content"]) for b in env.llm.requests for m in b["messages"] if m["role"] == "tool"]
        assert tool_msgs[0]["error"]["code"] == "cancelled"
    finally:
        c.__exit__(None, None, None)


def test_slack_confirmation_goes_to_the_dm_not_the_channel(tmp_path):
    env = ChEnv(tmp_path)
    env.slack(allowed=("U0ALICE01",))
    env.net.routes["slack.com"] = lambda r: httpx.Response(200, json={"ok": True})
    ch = env.mgr.channels["slack"]
    ch.min_confirm_timeout = 0.1
    env.ai.settings.confirm_timeout = 0.2
    cid = chat_id_for("slack", "U0ALICE01")
    env.mgr.remember(cid, "slack", "U0ALICE01", {"channel": "C999", "thread_ts": "1.2"})

    async def scenario():
        return await ch.confirm(cid, "x.buy", {"a": 1})

    assert asyncio.run(scenario()) is False
    first = env.net.bodies("slack.com")[0]
    assert first["channel"] == "U0ALICE01" and "thread_ts" not in first and "EVET" in first["text"]


def test_aclose_cancels_background_work_and_pending_confirmations(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]

    async def scenario():
        await env.mgr.start()
        cid, task = start_confirmation(env, ch)
        await wait_pending(ch, cid)

        async def forever():
            await asyncio.sleep(60)

        env.mgr.spawn(forever())
        await env.ai.aclose()
        assert not env.mgr._tasks
        results = await asyncio.gather(task, return_exceptions=True)
        assert results[0] is False or isinstance(results[0], asyncio.CancelledError)

    asyncio.run(scenario())


# ------------------------------------------------------------------ bridge / hook callers and the delivered flag

BRIDGE = {"Authorization": "Bearer bridge-token-0123456789abcdef"}


def test_hook_and_bridge_cannot_address_channel_users(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    alice = chat_id_for("whatsapp", ALICE)
    c = env.client()
    try:
        r = c.post("/hook/notify", headers=BRIDGE, json={"text": "selam", "chat_id": alice})
        assert r.status_code >= 400, r.text  # not "delivered": the chat id is refused
        r = c.post("/bridge/call/ai.ask", headers=BRIDGE, json={"args": {"prompt": "x", "chat_id": alice, "notify": True}})
        assert r.json().get("ok") is False and r.json()["error"]["code"] == "forbidden"
        r = c.post("/bridge/call/memory.list", headers=BRIDGE, json={"args": {"chat_id": alice}})
        assert r.json().get("ok") is False
    finally:
        c.__exit__(None, None, None)
    assert env.net.requests == [] and env.llm.requests == []


def test_hook_delivered_flag_reflects_the_real_delivery(tmp_path):
    env = ChEnv(tmp_path)
    c = env.client()
    try:
        r = c.post("/hook/notify", headers=BRIDGE, json={"text": "BTC 100k"})
        assert r.status_code == 200 and r.json()["result"]["delivered"] is True
        assert any(b["text"] == "BTC 100k" for b in env.tg.sent())
        env.ai.bot.token = ""  # no Telegram any more: nothing is delivered, and the answer says so
        r = c.post("/hook/notify", headers=BRIDGE, json={"text": "again"})
        assert r.status_code == 200 and r.json()["result"]["delivered"] is False
    finally:
        c.__exit__(None, None, None)


def test_deliver_returns_whether_the_message_went_out(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    alice = chat_id_for("whatsapp", ALICE)
    assert asyncio.run(env.ai.deliver(alice, "x")) is True
    assert asyncio.run(env.ai.deliver(chat_id_for("whatsapp", BOB), "x")) is False  # not allowed any more / never was
    env.net.routes["graph.facebook.com"] = lambda r: httpx.Response(401, json={})
    assert asyncio.run(env.ai.deliver(alice, "x")) is False
    assert asyncio.run(env.ai.deliver(CHAT, "x")) is True


# ------------------------------------------------------------------ truncated previews are refused, never asked


def test_channel_confirmation_with_a_long_preview_is_refused(tmp_path):
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    ch = env.mgr.channels["whatsapp"]
    cid = chat_id_for("whatsapp", ALICE)
    env.mgr.remember(cid, "whatsapp", ALICE, {"to": ALICE})
    big = {"tool": "x.buy", "args": {"note": "a" * 5000}}
    assert asyncio.run(ch.confirm(cid, "x.buy", big)) is False
    assert cid not in ch.pending
    texts = prompt_texts(env)
    assert len(texts) == 1 and "çok uzun" in texts[0] and "EVET" not in texts[0] and "aaaa" not in texts[0]
    # a short preview is still asked in full (and answered below)

    async def scenario():
        cid2, task = start_confirmation(env, ch)
        await wait_pending(ch, cid2)
        await reply_in(ch, ALICE, "HAYIR")
        return await task

    assert asyncio.run(scenario()) is False


def test_long_preview_refuses_the_tool_call_end_to_end(tmp_path):
    stack = Stack("ai-bot", "test")
    executed: list = []

    @stack.tool("x.buy", "Buy", confirm=True, scope="trade")
    def buy(note: str) -> dict:
        executed.append(note)
        return {"ok": True}

    env = ChEnv(tmp_path, stack=stack, script=[reply(calls=[tool_call("x_buy", {"note": "z" * 5000})]), reply("bitti")])
    env.whatsapp(allowed=(ALICE,))
    ok_graph(env)
    cid = chat_id_for("whatsapp", ALICE)
    env.mgr.remember(cid, "whatsapp", ALICE, {"to": ALICE})
    asyncio.run(env.ai.run_ai(cid, "al"))
    assert executed == []
    tool_msgs = [json.loads(m["content"]) for b in env.llm.requests for m in b["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["error"]["code"] == "cancelled"
    assert not any("EVET" in t for t in prompt_texts(env))


def test_telegram_confirmation_with_a_long_preview_is_refused(make_harness):
    h = make_harness()
    asked = []

    async def fake_ask(chat_id, text, **kw):
        asked.append(text)
        return True

    h.bot.ask_confirm = fake_ask
    assert asyncio.run(h.app.confirm_in_telegram(CHAT, "mt5.buy", {"note": "a" * 4000})) is False
    assert asked == []  # no button was ever shown
    assert any("çok uzun" in t for t in h.tg.texts(CHAT))
    # a normal preview still asks, in full
    assert asyncio.run(h.app.confirm_in_telegram(CHAT, "mt5.buy", {"symbol": "XAU"})) is True
    assert len(asked) == 1 and "XAU" in asked[0] and "…" not in asked[0]


# ------------------------------------------------------------------ outbound http hygiene


def test_outbound_client_ignores_proxy_env_and_never_follows_redirects(tmp_path, monkeypatch):
    import ai_bot.channels.base as base
    env = ChEnv(tmp_path)
    env.whatsapp(allowed=(ALICE,))
    seen = {}
    real = httpx.AsyncClient

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(base.httpx, "AsyncClient", spy)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    env.net.routes["graph.facebook.com"] = lambda r: httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    ch = env.mgr.channels["whatsapp"]
    try:
        asyncio.run(ch.send({"to": ALICE}, "x"))
        raise AssertionError("a redirect must be an error, not a success")
    except base.ChannelError as exc:
        assert exc.code == "whatsapp_redirect"
    assert seen["trust_env"] is False and seen["follow_redirects"] is False
    assert [r.url.host for r in env.net.requests] == ["graph.facebook.com"]  # evil.example was never contacted
