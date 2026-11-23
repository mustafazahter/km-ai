"""Slash commands work on every channel (not only Telegram): one registry, same handlers, safe on e-mail."""

from __future__ import annotations

import asyncio

from stack_kit import use_stack

from ai_bot import commands
from ai_bot.channels.base import Inbound, chat_id_for
from tests.test_channels_env import ALICE, ChEnv, chenv  # noqa: F401


class Spy:
    """Collects what a channel would have sent."""

    def __init__(self, ch):
        self.sent: list[str] = []

        async def send(ctx, text):
            self.sent.append(text)

        ch.send = send  # type: ignore[method-assign]


def inbound(text: str, sender: str = ALICE) -> Inbound:
    return Inbound(sender=sender, text=text, msg_id=None, ctx={"to": sender})


def run_one(ch, text: str, sender: str = ALICE) -> None:
    asyncio.run(ch.process(inbound(text, sender)))


def test_builtin_commands_run_on_whatsapp_without_the_ai(chenv):
    chenv.whatsapp()
    ch = chenv.mgr.channels["whatsapp"]
    spy = Spy(ch)
    chat = chat_id_for("whatsapp", ALICE)
    mid = chenv.ai.memory.remember(chat, "Kahveyi sütsüz içer.")["id"]
    run_one(ch, "/hafiza")
    assert "Kahveyi sütsüz içer." in spy.sent[-1] and f"#{mid}" in spy.sent[-1]
    assert chenv.llm.requests == []  # a command never reaches the AI
    run_one(ch, f"!hafiza sil {mid}")  # "!" works too (Slack swallows "/...")
    assert chenv.ai.db.count_memories(chat) == 0 and "Silindi" in spy.sent[-1]
    run_one(ch, "/help")
    assert "/hafiza" in spy.sent[-1] and "/id" not in spy.sent[-1]  # /id is Telegram-only


def test_unknown_slash_text_still_goes_to_the_ai(chenv):
    chenv.whatsapp()
    ch = chenv.mgr.channels["whatsapp"]
    spy = Spy(ch)
    run_one(ch, "/bilinmeyen komut gibi görünen bir cümle")
    assert chenv.llm.requests and spy.sent[-1] == "tamam"


def test_destructive_command_needs_the_confirmation_code(chenv):
    chenv.whatsapp()
    ch = chenv.mgr.channels["whatsapp"]
    spy = Spy(ch)
    chat = chat_id_for("whatsapp", ALICE)
    chenv.ai.memory.remember(chat, "Birinci anı.")
    chenv.ai.memory.remember(chat, "İkinci anı.")

    async def scenario(answer_with_code: bool):
        task = asyncio.create_task(ch.process(inbound("/hafiza temizle")))
        for _ in range(500):
            if chat in ch.pending:
                break
            await asyncio.sleep(0.01)
        code = ch.pending[chat].code
        await ch.process(inbound(f"EVET {code}" if answer_with_code else "HAYIR"))
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario(False))
    assert chenv.ai.db.count_memories(chat) == 2  # declined: nothing was deleted
    assert any("Onay gerekiyor: /hafiza" in m for m in spy.sent) and not any("AI şu işlemi" in m for m in spy.sent)
    asyncio.run(scenario(True))
    assert chenv.ai.db.count_memories(chat) == 0


def test_email_runs_read_only_commands_only(chenv):
    chenv.set(EMAIL_ENABLED=True)
    ch = chenv.mgr.channels["email"]
    assert ch.can_confirm is False
    spy = Spy(ch)
    chat = chat_id_for("email", ALICE)
    chenv.ai.memory.remember(chat, "Önemli bir not.")
    run_one(ch, "/hafiza")                      # reading is fine
    assert "Önemli bir not." in spy.sent[-1]
    for text in ("/hafiza temizle", "/hafiza sil 1", "/sifirla", "/model baska/model", "/gorevler iptal 1"):
        run_one(ch, text)
        assert "bu kanalda kapalı" in spy.sent[-1], text
    assert chenv.ai.db.count_memories(chat) == 1  # a spoofable sender can never delete anything
    assert chenv.llm.requests == []


def test_custom_command_is_registered_once_and_runs_everywhere(chenv):
    chenv.whatsapp()
    chenv.set(EMAIL_ENABLED=True)

    with use_stack(chenv.stack):
        @commands.command("ping", "Canlılık testi / liveness")
        async def ping(ctx):
            return f"pong {ctx.arg_text} via {getattr(ctx, 'channel', None) and ctx.channel.name}"

        @commands.command("surum", "Sürüm / version", readonly=True)
        def surum(ctx):
            return "km-ai test"

    wa, mail = chenv.mgr.channels["whatsapp"], chenv.mgr.channels["email"]
    wa_spy, mail_spy = Spy(wa), Spy(mail)
    run_one(wa, "/ping merhaba")
    assert wa_spy.sent[-1] == "pong merhaba via whatsapp"
    run_one(mail, "/ping merhaba")                 # not marked readonly: refused on e-mail
    assert "bu kanalda kapalı" in mail_spy.sent[-1]
    run_one(mail, "/surum")                        # readonly: allowed on e-mail too
    assert mail_spy.sent[-1] == "km-ai test"
    run_one(wa, "/help")
    assert "/ping" in wa_spy.sent[-1] and "/surum" in wa_spy.sent[-1]


def test_broken_command_does_not_stop_the_channel(chenv):
    chenv.whatsapp()
    with use_stack(chenv.stack):
        @commands.command("bozuk", "hata verir", readonly=True)
        def bozuk(ctx):
            raise RuntimeError("secret-internal-detail")

    ch = chenv.mgr.channels["whatsapp"]
    spy = Spy(ch)
    run_one(ch, "/bozuk")
    assert "Komut çalışırken hata oluştu" in spy.sent[-1] and "secret-internal-detail" not in spy.sent[-1]
    run_one(ch, "normal bir mesaj")
    assert spy.sent[-1] == "tamam"


def test_run_tool_from_a_command_follows_the_human_approval_rule(chenv):
    chenv.whatsapp()
    chenv.set(EMAIL_ENABLED=True)
    done: list[str] = []

    @chenv.stack.tool("islem.sil", "Siler (scope admin, confirm bayrağı yok)", scope="admin")
    def sil() -> dict:
        done.append("sil")
        return {"silindi": True}

    @chenv.stack.tool("islem.oku", "Okur", scope="read")
    def oku() -> dict:
        return {"deger": 1}

    with use_stack(chenv.stack):
        @commands.command("temizle", "tehlikeli", readonly=True)   # even a wrongly flagged command cannot skip approval
        async def temizle(ctx):
            r = await ctx.run_tool("islem.sil")
            return "tamam" if r.get("ok") else "reddedildi"

        @commands.command("deger", "okuma", readonly=True)
        async def deger(ctx):
            return str((await ctx.run_tool("islem.oku"))["result"]["deger"])

    wa, mail = chenv.mgr.channels["whatsapp"], chenv.mgr.channels["email"]
    wa_spy, mail_spy = Spy(wa), Spy(mail)
    chat = chat_id_for("whatsapp", ALICE)

    async def scenario(code_answer: bool):
        task = asyncio.create_task(wa.process(inbound("/temizle")))
        for _ in range(500):
            if chat in wa.pending:
                break
            await asyncio.sleep(0.01)
        await wa.process(inbound(f"EVET {wa.pending[chat].code}" if code_answer else "HAYIR"))
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario(False))
    assert done == [] and wa_spy.sent[-1] == "reddedildi"      # the admin tool did not run without approval
    asyncio.run(scenario(True))
    assert done == ["sil"] and wa_spy.sent[-1] == "tamam"
    run_one(wa, "/deger")
    assert wa_spy.sent[-1] == "1"                              # read tools need no approval
    run_one(mail, "/temizle")                                  # e-mail: nobody can approve
    assert done == ["sil"] and mail_spy.sent[-1] == "reddedildi"


def test_readonly_flag_belongs_to_one_stack(tmp_path):
    from tests.test_channels_env import ChEnv
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = ChEnv(tmp_path / "a"), ChEnv(tmp_path / "b")
    with use_stack(first.stack):
        @commands.command("sadece_ilk", "x", readonly=True)
        def sadece_ilk(ctx):
            return "x"
    assert "sadece_ilk" in commands.readonly_of(first.stack)
    assert "sadece_ilk" not in commands.readonly_of(second.stack)
