import pytest

from stack_kit import ToolError

from ai_bot.db import fts_query
from tests.conftest import CHAT, OTHER_CHAT


def test_remember_recall_forget(make_harness):
    m = make_harness().app.memory
    a = m.remember(CHAT, "Kullanıcının kedisinin adı Pamuk.")
    m.remember(CHAT, "Kullanıcı Python ile çalışıyor.", tags="iş yazılım")
    m.remember(OTHER_CHAT, "Başka sohbetin kedisi Tekir.")
    assert a["stored"] and m.remember(CHAT, "kullanıcının kedisinin adı pamuk.")["stored"] is False  # duplicate
    hits = m.recall(CHAT, "kedi adı")
    assert [h["content"] for h in hits] == ["Kullanıcının kedisinin adı Pamuk."]  # prefix match, per chat
    assert m.recall(CHAT, "yazılım")[0]["tags"] == "iş yazılım"  # tags are searchable
    assert m.recall(CHAT, "kedisinin")[0]["id"] == a["id"]
    assert m.forget(CHAT, a["id"])["forgotten"]
    assert m.recall(CHAT, "kedi") == []
    with pytest.raises(ToolError):
        m.forget(CHAT, a["id"])
    with pytest.raises(ToolError):
        m.forget(CHAT, m.recall(OTHER_CHAT, "Tekir")[0]["id"])  # cannot delete another chat's memory


def test_diacritics_insensitive_search(make_harness):
    m = make_harness().app.memory
    m.remember(CHAT, "Kullanıcı İzmir'de doğdu.")
    assert m.recall(CHAT, "izmir")


def test_fts_query_is_injection_safe(make_harness):
    m = make_harness().app.memory
    m.remember(CHAT, "Bir anı.")
    for q in ['"', "NEAR(", "a OR", "*", "content:x", ")) DROP TABLE memories;--"]:
        m.recall(CHAT, q)  # must not raise
    assert fts_query("!!!") is None


@pytest.mark.parametrize("secretish", [
    "Şifresi: hunter2xyz", "password=abc12345", "Kart 4111 1111 1111 1111",
    "token: 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  # fake value; stack-spec: ignore-secret
])
def test_secrets_are_not_stored(make_harness, secretish):
    m = make_harness().app.memory
    with pytest.raises(ToolError):
        m.remember(CHAT, secretish)
    assert m.list(CHAT)["total"] == 0


def test_limits(make_harness):
    m = make_harness(memory_max_per_chat=2).app.memory
    m.remember(CHAT, "bir")
    m.remember(CHAT, "iki")
    with pytest.raises(ToolError):
        m.remember(CHAT, "üç")
    with pytest.raises(ToolError):
        m.remember(CHAT, "x" * 600)


def test_context_prefers_matches_then_recent(make_harness):
    m = make_harness(memory_context_limit=2).app.memory
    m.remember(CHAT, "Kullanıcının doğum günü 3 Mart.")
    m.remember(CHAT, "Kullanıcı çay sever.")
    m.remember(CHAT, "Kullanıcı Ankara'da yaşıyor.")
    ctx = [c["content"] for c in m.context(CHAT, "doğum günüm ne zaman?")]
    assert ctx[0] == "Kullanıcının doğum günü 3 Mart." and len(ctx) == 2


@pytest.mark.parametrize("secretish", [
    "Kullanıcının şifresi hunter2",
    "password is hunter2",
    "GitHub token ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # fake; stack-spec: ignore-secret
    "AWS anahtarı AKIAIOSFODNN7EXAMPLE",  # fake; stack-spec: ignore-secret
    "secret is wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "OpenRouter sk-or-v1-abcdefghijklmnopqrstuvwx",  # fake; stack-spec: ignore-secret
    "slack xoxb-1234567890-abcdefghij",  # fake; stack-spec: ignore-secret
    "anahtar 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "api key = abc123",
])
def test_more_secrets_are_refused(make_harness, secretish):
    with pytest.raises(ToolError):
        make_harness().app.memory.remember(CHAT, secretish)


@pytest.mark.parametrize("ok", ["Kullanıcının kedisinin adı Pamuk.", "Kullanıcı İstanbul'da yaşıyor.",
                                "Kullanıcının doğum günü 3 Mart.",
                                "Favori sitesi https://example.com/articles/2026/11/some-long-article"])
def test_normal_facts_are_stored(make_harness, ok):
    assert make_harness().app.memory.remember(CHAT, ok)["stored"]
