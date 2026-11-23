import asyncio

from tests.conftest import CHAT
from tests.fakes import callback_update, message_update, reply, tool_call

STRANGER = 555555


def feed(h, *updates):
    async def go():
        for u in updates:
            await h.bot.process_update(u)
    asyncio.run(go())


def test_not_allowed_chat_only_gets_id_and_start(make_harness):
    h = make_harness()
    feed(h, message_update(STRANGER, "/id"), message_update(STRANGER, "/start"),
         message_update(STRANGER, "/help"), message_update(STRANGER, "/hafiza"), message_update(STRANGER, "merhaba"))
    texts = h.tg.texts(STRANGER)
    assert len(texts) == 2
    assert str(STRANGER) in texts[0] and "yetkili değil" in texts[1]
    assert h.llm.requests == []  # the AI was never called for a stranger


def test_empty_allow_list_answers_only_id_and_start(make_harness):
    h = make_harness(allowed_chat_ids=[])
    feed(h, message_update(CHAT, "/id"), message_update(CHAT, "/start"), message_update(CHAT, "selam"),
         message_update(CHAT, "/gorevler"))
    assert len(h.tg.texts(CHAT)) == 2 and h.llm.requests == []


def test_free_text_goes_to_ai(make_harness):
    h = make_harness([reply("Merhaba, nasıl yardımcı olabilirim?")])
    feed(h, message_update(CHAT, "selam"))
    assert h.tg.texts(CHAT)[-1] == "Merhaba, nasıl yardımcı olabilirim?"


def test_commands(make_harness):
    h = make_harness()
    h.app.memory.remember(CHAT, "Kullanıcı kahve sever.")
    h.app.scheduler.add(CHAT, "0 9 * * *", "Günaydın", description="sabah")
    h.app.db.add_history(CHAT, "user", "eski", 10)
    feed(h, message_update(CHAT, "/help"), message_update(CHAT, "/hafiza"), message_update(CHAT, "/gorevler"),
         message_update(CHAT, "/model"), message_update(CHAT, "/model other/model"), message_update(CHAT, "/sifirla"))
    t = h.tg.texts(CHAT)
    assert "/hafiza" in t[0] and "/gorevler" in t[0]
    assert "kahve" in t[1]
    assert "sabah" in t[2] and "0 9 * * *" in t[2]
    assert "test/model" in t[3]
    assert "other/model" in t[4] and h.app.agent.model_for(CHAT) == "other/model"
    assert "temizlendi" in t[5] and h.app.db.get_history(CHAT, 10) == []
    feed(h, message_update(CHAT, "/model varsayilan"))
    assert h.app.agent.model_for(CHAT) == "test/model"


def test_model_choices_are_enforced(make_harness):
    h = make_harness(llm_model_choices=["a/one", "b/two"])
    feed(h, message_update(CHAT, "/model evil/model"), message_update(CHAT, "/model b/two"))
    assert h.app.agent.model_for(CHAT) == "b/two" and "izinli değil" in h.tg.texts(CHAT)[0]


def test_hafiza_delete_and_clear_with_button(make_harness):
    h = make_harness()
    mid = h.app.memory.remember(CHAT, "Silinecek anı.")["id"]
    h.app.memory.remember(CHAT, "İkinci anı.")
    feed(h, message_update(CHAT, f"/hafiza sil {mid}"))
    assert h.app.db.get_memory(CHAT, mid) is None

    async def clear():
        task = asyncio.create_task(h.bot.process_update(message_update(CHAT, "/hafiza temizle")))
        prompt = await h.tg.wait_for(lambda b: "reply_markup" in b)
        await h.bot.process_update(callback_update(CHAT, prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]))
        await asyncio.wait_for(task, 3)

    asyncio.run(clear())
    assert h.app.db.count_memories(CHAT) == 0


def test_gorevler_cancel(make_harness):
    h = make_harness()
    t = h.app.scheduler.add(CHAT, "0 9 * * *", "x")
    feed(h, message_update(CHAT, f"/gorevler iptal {t['id']}"))
    assert h.app.scheduler.list(CHAT) == []


def test_confirmation_button_shown_for_confirm_tool_from_plugin(make_harness):
    from stack_kit import load_plugins
    h = make_harness([reply(calls=[tool_call("ornek_bagis", {"miktar": 5})]), reply("İptal edildi, bağış yapılmadı.")])
    load_plugins(h.stack, "plugins")

    async def go():
        task = asyncio.create_task(h.bot.process_update(message_update(CHAT, "5 lira bağış yap")))
        prompt = await h.tg.wait_for(lambda b: "reply_markup" in b)
        assert "ornek.bagis" in prompt["text"]
        no = prompt["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
        await h.bot.process_update(callback_update(CHAT, no))
        await asyncio.wait_for(task, 3)

    asyncio.run(go())
    assert h.tg.texts(CHAT)[-1] == "İptal edildi, bağış yapılmadı."
