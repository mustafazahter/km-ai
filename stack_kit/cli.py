"""``python -m stack_kit tools|call|doctor|new-plugin``."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .bridges import Bridge, Bridges, bridges_from_env
from .errors import BridgeError
from .events import webhooks_out_from_env
from .plugins import load_plugins
from .redact import install_log_redaction, redact, register_secret
from .registry import get_stack
from .settings import env_bool, env_list, env_str, load_dotenv

PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

PLUGIN_TEMPLATE = '''"""{name} plugin. Loaded automatically from PLUGINS_DIR at startup.

Docs: komuta-stack-kit repository, docs/API.md. Try it:  python -m stack_kit tools
"""

from stack_kit import ToolError, emit, tool


@tool("{name}.hello", "Selam verir / Says hello", scope="read")
def hello(who: str = "dünya") -> dict:
    return {{"message": f"Merhaba {{who}}!"}}


@tool("{name}.reset", "Sayaç sıfırlar (onay ister) / Resets a counter (needs confirmation)",
      confirm=True, scope="admin")
async def reset(counter: str) -> dict:
    if not counter:
        raise ToolError("counter is required", code="invalid_args")
    emit("{name}.reset", {{"counter": counter}})
    return {{"reset": counter}}
'''


def _import_modules(modules: list[str]) -> None:
    sys.path.insert(0, os.getcwd())
    for mod in modules:
        importlib.import_module(mod)


def _local_stack(args: argparse.Namespace):
    _import_modules(args.module or env_list("STACK_MODULES"))
    stack = get_stack()
    load_plugins(stack, args.plugins_dir)
    return stack


def _remote(args: argparse.Namespace) -> Bridges:
    token = args.token or env_str("BRIDGE_TOKEN")
    if not token:
        raise SystemExit("error: --url needs BRIDGE_TOKEN (env) or --token")
    register_secret(token)
    return Bridges([Bridge("remote", args.url.rstrip("/"), token)], timeout=args.timeout)


def cmd_tools(args: argparse.Namespace) -> int:
    async def go() -> list[dict]:
        if args.url:
            async with _remote(args) as b:
                tools = await b.tools()
                if b.errors:
                    raise SystemExit(f"error: {b.errors['remote']['message']}")
                return tools
        stack = _local_stack(args)
        for err in stack.plugin_errors:
            print(f"! plugin {err['plugin']}: {err['error']}", file=sys.stderr)
        return [t.manifest() for t in stack.tools.values()]

    tools = asyncio.run(go())
    if args.json:
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        return 0
    if not tools:
        print("(no tools)")
    for t in tools:
        flags = t["scope"] + (", confirm" if t.get("confirm") else "")
        print(f"{t['name']:<32} [{flags}] {t.get('description', '')}")
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    try:
        call_args = json.loads(args.args) if args.args else {}
    except ValueError:
        print("error: ARGS must be a JSON object, e.g. '{\"who\": \"Ada\"}'", file=sys.stderr)
        return 2
    if not isinstance(call_args, dict):
        print("error: ARGS must be a JSON object", file=sys.stderr)
        return 2

    async def go() -> dict:
        if args.url:
            async with _remote(args) as b:
                return await b.call(args.name, call_args, confirm_id=args.confirm_id, confirmed=args.confirmed)
        stack = _local_stack(args)
        return await stack.call(args.name, call_args, confirm_id=args.confirm_id, confirmed=args.confirmed)

    result = asyncio.run(go())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("needs_confirmation") else 1


def _host(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname or '?'}{':' + str(p.port) if p.port else ''}"


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print environment/plugin status. Never prints secret values (only set/unset + length)."""
    problems = 0
    out: list[str] = []

    def line(status: str, text: str) -> None:
        out.append(f"[{status}] {text}")

    line("ok", f"stack_kit {__version__}, Python {platform.python_version()}")
    if sys.version_info < (3, 12):
        line("warn", "Python 3.12+ is recommended")
    for dep in ("fastapi", "uvicorn", "httpx", "pydantic"):
        try:
            mod = importlib.import_module(dep)
            line("ok", f"{dep} {getattr(mod, '__version__', '?')}")
        except ImportError:
            line("FAIL", f"{dep} is not installed")
            problems += 1

    for name in ("BRIDGE_TOKEN", "HOOK_SECRET", "TELEGRAM_BOT_TOKEN"):
        value = env_str(name)
        register_secret(value)
        if value:
            weak = " (short; use 32+ chars)" if len(value) < 24 and name != "TELEGRAM_BOT_TOKEN" else ""
            line("warn" if weak else "ok", f"{name}: set, {len(value)} chars{weak}")
        else:
            hint = {"BRIDGE_TOKEN": "a random token will be generated at startup (changes every restart)",
                    "HOOK_SECRET": "incoming webhooks accept only the bridge token",
                    "TELEGRAM_BOT_TOKEN": "Telegram bot disabled"}[name]
            line("info", f"{name}: not set; {hint}")

    try:
        line("ok", f"BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP: {env_bool('BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP', False)}")
    except ValueError as exc:
        line("FAIL", str(exc))
        problems += 1
    try:
        chats = env_list("ALLOWED_CHAT_IDS")
        [int(c) for c in chats]
        line("ok" if chats else "info",
             f"ALLOWED_CHAT_IDS: {len(chats)} chat(s)" + ("" if chats else " (only /id and /start answer)"))
    except ValueError:
        line("FAIL", "ALLOWED_CHAT_IDS must be comma separated integers")
        problems += 1
    try:
        targets = webhooks_out_from_env()
        line("ok", f"WEBHOOKS_OUT (+ WEBHOOKS_OUT_URL/_SECRET[_N]): {len(targets)} target(s)" +
             "".join(f"\n       - {t.safe_url()}" for t in targets))
    except ValueError as exc:
        line("FAIL", str(exc))
        problems += 1
    try:
        bridges = bridges_from_env()
        line("ok", f"BRIDGES (+ BRIDGE_<NAME>_URL/_TOKEN): {len(bridges)}" + "".join(
            f"\n       - {b.name} = {'local' if b.is_local else _host(b.url or '') + ', token set'}"
            for b in bridges))
    except BridgeError as exc:
        line("FAIL", f"BRIDGES: {exc.message}")
        problems += 1
    http_host = env_str("HTTP_HOST", "0.0.0.0")
    if http_host == "0.0.0.0":  # nosec B104 - reporting only
        line("warn", f"HTTP: 0.0.0.0:{env_str('HTTP_PORT', '8080')} (all interfaces; fine in a container/Komuta, "
                     "otherwise set HTTP_HOST=127.0.0.1)")
    else:
        line("ok", f"HTTP: {http_host}:{env_str('HTTP_PORT', '8080')}")
    try:
        line("ok", f"BRIDGE_ALLOW_ONESTEP_CONFIRM: {env_bool('BRIDGE_ALLOW_ONESTEP_CONFIRM', False)} "
                   "(trade/admin confirm tools need two steps unless true)")
    except ValueError as exc:
        line("FAIL", str(exc))
        problems += 1

    plugins_dir = Path(args.plugins_dir or env_str("PLUGINS_DIR", "plugins") or "plugins")
    try:
        stack = _local_stack(args)
    except Exception as exc:
        line("FAIL", f"importing STACK_MODULES failed: {type(exc).__name__}: {exc}")
        problems += 1
        stack = get_stack()
    if plugins_dir.is_dir():
        line("ok", f"PLUGINS_DIR {plugins_dir}: loaded {len(stack.loaded_plugins)} "
                   f"({', '.join(stack.loaded_plugins) or '-'})")
    else:
        line("info", f"PLUGINS_DIR {plugins_dir}: not found")
    for err in stack.plugin_errors:
        line("FAIL", f"plugin {err['plugin']}: {err['error']}")
        problems += 1
    confirm_tools = [t.name for t in stack.tools.values() if t.confirm]
    line("ok", f"tools: {len(stack.tools)} (confirm: {', '.join(confirm_tools) or '-'}), "
               f"hooks: {len(stack.hooks)}, commands: {len(stack.commands)}")
    line("ok" if not problems else "FAIL", f"{problems} problem(s)")
    print(redact("\n".join(out)))
    return 1 if problems else 0


def cmd_new_plugin(args: argparse.Namespace) -> int:
    name = args.name.strip().lower()
    if not PLUGIN_NAME_RE.match(name):
        print("error: plugin name must match [a-z][a-z0-9_]*", file=sys.stderr)
        return 2
    directory = Path(args.plugins_dir or env_str("PLUGINS_DIR", "plugins") or "plugins")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    if path.exists():
        print(f"error: {path} already exists", file=sys.stderr)
        return 1
    path.write_text(PLUGIN_TEMPLATE.format(name=name), encoding="utf-8")
    print(f"created {path}\nnext: python -m stack_kit tools")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """Show or change the bridge gate (DATA_DIR/bridge-gate.json). Secrets are printed only when created."""
    from .gate import SURFACES, BridgeGate

    gate = BridgeGate(path=args.file)
    actor = "cli"
    if args.action == "status":
        st = gate.status()
        for name in SURFACES:
            print(f"{name:<7} {'open' if st['surfaces'][name] else 'closed':<7} ({st['sources'][name]})")
        print(f"env BRIDGE_TOKEN: {'set' if env_str('BRIDGE_TOKEN') else 'not set'}")
        print(f"gate token: {'set' if st['panel_token']['set'] else 'not set'}")
        print(f"env HOOK_SECRET: {'set' if env_str('HOOK_SECRET') else 'not set'}")
        print(f"gate hook secret: {'set' if st['panel_hook_secret']['set'] else 'not set'}")
        print(f"state file: {gate.path}")
        return 0
    if args.action in ("enable", "disable"):
        targets = list(SURFACES) if args.target in (None, "all") else [args.target]
        for name in targets:
            if name not in SURFACES:
                print(f"error: surface must be one of {', '.join(SURFACES)} or all", file=sys.stderr)
                return 2
            gate.set(name, args.action == "enable", actor=actor)
        print(f"{args.action}d: {', '.join(targets)}")
        if args.action == "enable" and not env_str("BRIDGE_TOKEN") and not gate.has_panel_token \
                and any(t in ("bridge", "mcp") for t in targets):
            print("no bridge token yet: create one with  python -m stack_kit gate rotate")
        return 0
    if args.action == "rotate":
        if args.target == "hook-secret":
            secret = gate.rotate_hook_secret(actor=actor)
            print("new hook secret (shown once, store it now):")
            print(secret)
        elif args.target in (None, "token"):
            token = gate.rotate_token(actor=actor)
            print("new bridge token (shown once, store it now; the previous gate token stops working):")
            print(token)
        else:
            print("error: rotate token|hook-secret", file=sys.stderr)
            return 2
        return 0
    if args.action == "revoke":
        if args.target == "hook-secret":
            gate.revoke_hook_secret(actor=actor)
        elif args.target in (None, "token"):
            gate.revoke_token(actor=actor)
        else:
            print("error: revoke token|hook-secret", file=sys.stderr)
            return 2
        print("revoked")
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m stack_kit", description="stack_kit command line")
    p.add_argument("--env-file", default=".env", help="read this .env first if it exists (default .env)")
    p.add_argument("--version", action="version", version=f"stack_kit {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def local_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--module", action="append",
                        help="import this module first (repeatable; default STACK_MODULES env)")
        sp.add_argument("--plugins-dir", help="default PLUGINS_DIR env or ./plugins")

    def remote_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--url", help="remote bridge base URL (token from BRIDGE_TOKEN env or --token)")
        sp.add_argument("--token", help="bridge token (prefer the BRIDGE_TOKEN env var)")
        sp.add_argument("--timeout", type=float, default=30.0)

    sp = sub.add_parser("tools", help="list tools (local registry + plugins, or a remote bridge)")
    local_opts(sp)
    remote_opts(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_tools)

    sp = sub.add_parser("call", help="call a tool")
    sp.add_argument("name")
    sp.add_argument("args", nargs="?", help="JSON object, e.g. '{\"who\": \"Ada\"}'")
    sp.add_argument("--confirmed", action="store_true", help="HTTP shortcut for confirm=True tools")
    sp.add_argument("--confirm-id", help="confirm_id from a previous preview")
    local_opts(sp)
    remote_opts(sp)
    sp.set_defaults(func=cmd_call)

    sp = sub.add_parser("doctor", help="check environment and plugins (never prints secrets)")
    local_opts(sp)
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("gate", help="open/close /bridge, /mcp, /hook; create the gate token (shown once)")
    sp.add_argument("action", choices=["status", "enable", "disable", "rotate", "revoke"])
    sp.add_argument("target", nargs="?", help="enable/disable: bridge|mcp|hook|all · rotate/revoke: token|hook-secret")
    sp.add_argument("--file", help="state file (default DATA_DIR/bridge-gate.json)")
    sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("new-plugin", help="create plugins/<name>.py from a template")
    sp.add_argument("name")
    sp.add_argument("--plugins-dir")
    sp.set_defaults(func=cmd_new_plugin)
    return p


def main(argv: list[str] | None = None) -> int:
    install_log_redaction()
    args = build_parser().parse_args(argv)
    if args.env_file:
        load_dotenv(args.env_file)
    return int(args.func(args) or 0)
