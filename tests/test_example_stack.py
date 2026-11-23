"""The documented example scenario really works: an external stack built with stack_kit, bridged into km-ai."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx

from stack_kit import BridgeConfig, Bridges, Stack, create_app, use_stack
from stack_kit.bridges import Bridge

from tests.conftest import CHAT
from tests.fakes import callback_update, message_update, reply, tool_call

TOKEN = "kargo-token-0123456789-abcdef"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ornek-stack" / "kargo_stack.py"


def remote_app():
    """Import the example file into a fresh stack (its module-level @tool/@hook register there)."""
    stack = Stack("kargo-test", "0")
    spec = importlib.util.spec_from_file_location("kargo_stack_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    with use_stack(stack):
        spec.loader.exec_module(module)
    return module, stack, create_app(stack, config=BridgeConfig(token=TOKEN), load_plugins=False)


def bridges_to(app, ai_stack):
    return Bridges([Bridge("kargo", "http://kargo.test", TOKEN)], stack=ai_stack,
                   transport=httpx.ASGITransport(app=app))


def last_tool_result(h):
    return json.loads([m for m in h.llm.requests[-1]["messages"] if m["role"] == "tool"][-1]["content"])


def test_ai_reads_parcel_status_through_the_bridge(make_harness):
    module, stack, app = remote_app()
    ai_stack = Stack("ai-bot", "test")
    h = make_harness([reply(calls=[tool_call("kargo_durum", {"siparis_no": "1042"})]),
                      reply("1042 dağıtımda, bugün 18:00'e kadar gelir.")],
                     stack=ai_stack, bridges=bridges_to(app, ai_stack))
    answer = asyncio.run(h.app.agent.run(CHAT, "1042 numaralı siparişim nerede?"))
    assert "18:00" in answer
    raw = last_tool_result(h)
    result = raw.get("result", raw)
    assert result["durum"] == "dagitimda" and "tutar" not in result  # the amount is not exposed by the read tool


def test_refund_needs_a_button_press(make_harness):
    module, stack, app = remote_app()
    ai_stack = Stack("ai-bot", "test")
    script = lambda: [reply(calls=[tool_call("kargo_iade_baslat", {"siparis_no": "1042", "neden": "hasarlı"})]), reply("Bitti.")]  # noqa: E731
    h = make_harness(script(), stack=ai_stack, bridges=bridges_to(app, ai_stack))

    async def scenario(approve):
        h.tg.calls.clear()
        task = asyncio.create_task(h.bot.process_update(message_update(CHAT, "1042'yi iade et")))
        prompt = await h.tg.wait_for(lambda b: "reply_markup" in b)
        assert "kargo.iade_baslat" in prompt["text"]
        data = prompt["reply_markup"]["inline_keyboard"][0][0 if approve else 1]["callback_data"]
        await h.bot.process_update(callback_update(CHAT, data))
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario(approve=False))
    assert module.SIPARISLER["1042"]["durum"] == "dagitimda"  # declined: nothing happened
    h.llm.script = script()
    asyncio.run(scenario(approve=True))
    assert module.SIPARISLER["1042"]["durum"] == "iade"
    result = asyncio.run(stack.call("kargo.iade_baslat", {"siparis_no": "1042"}))
    assert result.get("needs_confirmation") is True  # a second refund still goes through approval
    again = asyncio.run(stack.call("kargo.iade_baslat", {"siparis_no": "1042"}, confirm_id=result["confirm_id"]))
    assert again["ok"] is False and "zaten iade" in again["error"]["message"]


def test_carrier_webhook_updates_state_and_emits_an_event():
    module, stack, app = remote_app()

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kargo.test") as c:
            r = await c.post("/hook/kargo-guncelle", json={"siparis_no": "1043", "durum": "gecikti"},
                             headers={"Authorization": f"Bearer {TOKEN}"})
            events = await c.get("/bridge/events", headers={"Authorization": f"Bearer {TOKEN}"})
            return r, events

    r, events = asyncio.run(go())
    assert r.status_code == 200, r.text
    assert module.SIPARISLER["1043"]["durum"] == "gecikti"
    assert "kargo.gecikti" in events.text
