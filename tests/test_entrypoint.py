"""scripts/entrypoint.sh: warn-only, never changes ownership or privileges (Komuta disk must stay untouched).

Simulated with fake id/chown/setpriv on PATH: none of them may ever be called except id.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "entrypoint.sh"
pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")


def fake_bin(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def run(tmp_path, uid: str, data_writable: bool = True):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    fake_bin(bindir, "id", f"echo {uid}")
    for tool in ("chown", "setpriv", "gosu", "su", "chmod"):
        fake_bin(bindir, tool, f'echo "{tool} $*" >> {log}')
    data = tmp_path / "data"
    if data_writable:
        data.mkdir()
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "DATA_DIR": str(data)}
    res = subprocess.run(["sh", str(SCRIPT), "echo", "app-started"], env=env, capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return res, calls


def test_syntax_and_no_privilege_tools_in_script():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    import re
    cmd = re.compile(r"^\s*(?:if\s+!?\s*)?(?:exec\s+)?(?:chown|setpriv|gosu|su-exec|su)\b")
    assert not [l for l in SCRIPT.read_text().splitlines() if cmd.match(l)]
    assert "setpriv" not in SCRIPT.read_text()


@pytest.mark.parametrize("uid", ["1000", "0"])
def test_runs_app_without_touching_ownership(tmp_path, uid):
    res, calls = run(tmp_path, uid)
    assert res.returncode == 0 and "app-started" in res.stdout and res.stderr == ""
    assert calls == []  # no chown / setpriv even when started as root


@pytest.mark.parametrize("uid", ["1000", "0"])
def test_unwritable_data_only_warns(tmp_path, uid):
    res, calls = run(tmp_path, uid, data_writable=False)
    assert res.returncode == 0 and "app-started" in res.stdout
    assert "not writable" in res.stderr and "chown 1000:1000 ./data" in res.stderr
    assert calls == []


def test_dockerfile_runs_as_uid_1000_without_root_tricks():
    text = (ROOT / "Dockerfile").read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "\nUSER 1000" in code
    assert "--uid 1000" in code and "chown 1000:1000 /data" in code
    assert "setpriv" not in code and "chown -R" not in code and "gosu" not in code
