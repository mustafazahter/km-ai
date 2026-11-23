"""English dictionary of the panel: well-formed, no empty or identical-looking entries, rules compile."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "src/ai_bot/ui/i18n-en.js"


def _dict() -> dict:
    body = FILE.read_text(encoding="utf-8")
    m = re.search(r"AIB_I18N_EN\s*=\s*(\{.*?\n\});", body, re.S)
    assert m, "dictionary block not found"
    return json.loads(m.group(1))


def _rules() -> list:
    body = FILE.read_text(encoding="utf-8")
    m = re.search(r"AIB_I18N_RULES\s*=\s*(\[.*\]);", body, re.S)
    assert m, "rules block not found"
    return json.loads(m.group(1))


def test_dictionary_is_well_formed():
    d = _dict()
    assert len(d) > 500
    for k, v in d.items():
        assert isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip(), k
        assert "\n\n\n" not in v


def test_rules_compile_and_use_valid_groups():
    for pattern, repl in _rules():
        rx = re.compile(pattern)
        for g in re.findall(r"\$(\d)", repl):
            assert int(g) <= rx.groups, (pattern, repl)


def test_key_screens_are_translated():
    d = _dict()
    for tr in ("Genel bakış", "Kurulum", "Ayarlar", "Telegram botu", "Sohbet", "Kaydet ve bağlan", "Anladım", "Güncelleniyor"):
        assert tr in d, tr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_accepts_the_file():
    out = subprocess.run(["node", "-e", f"global.window={{}};require('{FILE}');"
                          "console.log(Object.keys(window.AIB_I18N_EN).length, window.AIB_I18N_RULES.length)"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
