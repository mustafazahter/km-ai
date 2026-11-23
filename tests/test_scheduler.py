import asyncio
import time
from datetime import datetime

import pytest

from stack_kit import ToolError

from tests.conftest import CHAT
from tests.fakes import reply


def test_add_variants_and_list_cancel(make_harness):
    h = make_harness()
    sch = h.app.scheduler
    a = sch.add(CHAT, "0 9 * * *", "Günaydın", description="sabah")
    b = sch.add(CHAT, "10 dakika sonra", "Çamaşır")
    c = sch.add(CHAT, "2030-01-01T09:00", "Uzak gelecek")
    assert a["repeating"] and a["cron"] == "0 9 * * *" and a["next_run"].endswith("09:00")
    assert not b["repeating"] and not c["repeating"] and c["next_run"] == "2030-01-01 09:00"
    assert {t["id"] for t in sch.list(CHAT)} == {a["id"], b["id"], c["id"]}
    sch.cancel(CHAT, b["id"])
    assert b["id"] not in {t["id"] for t in sch.list(CHAT)}
    with pytest.raises(ToolError):
        sch.cancel(CHAT, b["id"])


def test_rejects_bad_input(make_harness):
    sch = make_harness(schedule_max_per_chat=1).app.scheduler
    with pytest.raises(ToolError):
        sch.add(CHAT, "* * * * *", "her dakika")  # too frequent (min 5 minutes)
    with pytest.raises(ToolError):
        sch.add(CHAT, "2001-01-01T00:00", "geçmiş")
    with pytest.raises(ToolError):
        sch.add(CHAT, "0 9 * * *", "x", kind="shell")
    sch.add(CHAT, "0 9 * * *", "bir")
    with pytest.raises(ToolError):
        sch.add(CHAT, "0 10 * * *", "iki")  # limit


def test_message_task_runs_once_and_is_persistent(make_harness, tmp_path):
    h = make_harness()
    t = h.app.scheduler.add(CHAT, "5 dakika sonra", "Toplantı!")
    # simulate a restart: a brand-new AiBot on the same database file
    h2 = make_harness()
    assert [x["id"] for x in h2.app.scheduler.list(CHAT)] == [t["id"]]

    async def go():
        assert await h2.app.scheduler.run_due(time.time()) == 0  # not due yet
        assert await h2.app.scheduler.run_due(time.time() + 6 * 60) == 1
        await h2.app.scheduler.drain()
        assert await h2.app.scheduler.run_due(time.time() + 7 * 60) == 0  # one-shot: done

    asyncio.run(go())
    assert any("Toplantı!" in x for x in h2.tg.texts(CHAT))
    assert h2.app.scheduler.list(CHAT) == []


def test_overdue_after_restart_runs_late_once(make_harness):
    h = make_harness()
    sid = h.app.db.add_schedule(CHAT, "message", "Kaçırılan", None, time.time() - 3600)
    cron_id = h.app.db.add_schedule(CHAT, "message", "Her gün", "0 9 * * *", time.time() - 2 * 86400)

    async def go():
        assert await h.app.scheduler.run_due() == 2
        await h.app.scheduler.drain()
        assert await h.app.scheduler.run_due() == 0  # cron missed runs are not replayed

    asyncio.run(go())
    texts = h.tg.texts(CHAT)
    assert any("gecikmeli" in x and "Kaçırılan" in x for x in texts)
    assert h.app.db.get_schedule(sid)["active"] == 0
    row = h.app.db.get_schedule(cron_id)
    assert row["active"] == 1 and row["next_run"] > time.time()
    nxt = datetime.fromtimestamp(row["next_run"], h.app.settings.zone)
    assert (nxt.hour, nxt.minute) == (9, 0)


def test_prompt_task_runs_the_ai_without_touching_history(make_harness):
    h = make_harness([reply("Bugünün özeti: her şey yolunda.")])
    h.app.scheduler.add(CHAT, "0 9 * * *", "Haberleri özetle", kind="prompt", description="Sabah özeti")

    async def go():
        await h.app.scheduler.run_due(time.time() + 2 * 86400)
        await h.app.scheduler.drain()

    asyncio.run(go())
    assert h.llm.requests[0]["messages"][-1]["content"] == "Haberleri özetle"
    assert any("Sabah özeti" in x and "her şey yolunda" in x for x in h.tg.texts(CHAT))
    assert h.app.db.get_history(CHAT, 10) == []
    assert h.app.scheduler.list(CHAT)[0]["repeating"]  # cron re-armed


def test_scheduler_loop_wakes_on_add(make_harness):
    h = make_harness(schedule_tick=30)

    async def go():
        loop_task = asyncio.create_task(h.app.scheduler.run_forever())
        await asyncio.sleep(0.05)
        h.app.db.add_schedule(CHAT, "message", "Hemen", None, time.time() - 1)
        h.app.scheduler._wake.set()
        await h.tg.wait_for(lambda b: "Hemen" in b["text"], timeout=2)
        loop_task.cancel()

    asyncio.run(go())


def test_one_shot_minimum_and_limit_counts_one_shots(make_harness):
    sch = make_harness(schedule_max_per_chat=2).app.scheduler
    with pytest.raises(ToolError):
        sch.add(CHAT, "10 saniye sonra", "çok yakın")  # < 60 s
    sch.add(CHAT, "2 dakika sonra", "bir")
    sch.add(CHAT, "3 dakika sonra", "iki")
    with pytest.raises(ToolError):
        sch.add(CHAT, "4 dakika sonra", "üç")  # pending one-shots count toward the limit


def test_cron_rearm_never_goes_to_the_past(make_harness):
    from zoneinfo import ZoneInfo
    h = make_harness(tz="Europe/Berlin")
    berlin = ZoneInfo("Europe/Berlin")
    second_pass = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1).timestamp()
    sid = h.app.db.add_schedule(CHAT, "message", "saatlik", "30 * * * *", second_pass)

    async def go():
        assert await h.app.scheduler.run_due(second_pass + 5) == 1
        await h.app.scheduler.drain()
        assert await h.app.scheduler.run_due(second_pass + 25) == 0  # not again on the next tick

    asyncio.run(go())
    assert h.app.db.get_schedule(sid)["next_run"] == second_pass + 3600


def test_scheduled_prompt_cannot_reschedule_itself(make_harness):
    from tests.fakes import tool_call
    h = make_harness([reply(calls=[tool_call("schedule_add", {"when": "1 saat sonra", "text": "tekrar",
                                                               "kind": "prompt"})]), reply("bitti")])
    h.app.scheduler.add(CHAT, "0 9 * * *", "özetle", kind="prompt")

    async def go():
        await h.app.scheduler.run_due(time.time() + 2 * 86400)
        await h.app.scheduler.drain()

    asyncio.run(go())
    names = {t["function"]["name"] for t in h.llm.requests[0]["tools"]}
    assert "schedule_add" not in names  # hidden in scheduled turns
    import json
    result = json.loads([m for m in h.llm.requests[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert result["error"]["code"] == "not_found"  # the model cannot call a tool it was not offered
    assert len(h.app.scheduler.list(CHAT)) == 1
    # and the agent refuses it explicitly even if it is looked up directly
    from ai_bot.agent import Turn

    async def direct():
        _, table = await h.app.agent.tool_table()
        return await h.app.agent.call_tool(table["schedule_add"], {"when": "1 saat sonra", "text": "x"}, CHAT,
                                           None, Turn(scheduled=True))

    assert asyncio.run(direct())["error"]["code"] == "not_allowed"
    assert len(h.app.scheduler.list(CHAT)) == 1
