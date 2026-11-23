"""Panel API for the messaging channels (WhatsApp, Teams, Slack, e-mail).

Routes (all under ``/ui/api``, signed-in + CSRF like every panel route)::

    GET  /channels                 state of every channel (never a secret: only set + …last4)
    PUT  /channels/{name}          enabled + settings + write-only secrets (through ``Panel._save``: audit + live apply)
    POST /channels/{name}/test     provider check that messages nobody (WhatsApp phone info, Slack auth.test,
                                   Teams bot token, e-mail IMAP login + SMTP noop); short safe error, HTTP 424
    POST /channels/{name}/secret   generate a secret (WhatsApp verify token), shown ONCE in the response

Provider problems are returned as HTTP 424 with a Turkish message and a short code (never 502: the hosting
proxy replaces 502 bodies, and never a provider body).
"""

from __future__ import annotations

import secrets as _secrets
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from stack_ui import UIUser, current_user

from .channels.base import Channel, ChannelError
from .channels.config import split_list

if TYPE_CHECKING:
    from .panel import Panel


def build_router(panel: "Panel", err, body_of) -> APIRouter:
    r = APIRouter()
    ai = panel.ai

    def manager():
        mgr = ai.channels
        if mgr is None:
            raise err(404, "not_found", "Kanallar bu serviste kapalı.")
        if not mgr.config.bound:  # panel stores are the source of truth (panel > env > default)
            mgr.bind(panel.live.store, panel.live.secrets)
        return mgr

    def pick(name: str) -> Channel:
        ch = manager().channels.get(name)
        if ch is None:
            raise err(404, "not_found", "Bilinmeyen kanal.")
        return ch

    def field_view(ch: Channel, f: dict) -> dict:
        info = panel.field(f["name"])  # secrets: only set + preview + source
        out = {"name": f["name"], "label": f["label"], "kind": f["kind"], "hint": f.get("hint", ""),
               "required": bool(f.get("required")), "generate": bool(f.get("generate")),
               "source": info["source"], "env_set": info["env_set"]}
        if f["kind"] == "secret":
            out.update({"set": info["set"], "preview": info["preview"]})
        elif f["kind"] == "list":
            out["items"] = ch.allowed()
        else:
            out["value"] = info.get("value")
        return out

    def view(ch: Channel, request: Request) -> dict:
        ui = panel.ui
        base, verified = ui.public_base_url(request) if ui is not None else (None, False)
        v = ch.view()
        v["fields"] = [field_view(ch, f) for f in ch.fields]
        v["enabled_source"] = panel.field(f"{ch.prefix}_ENABLED")["source"]
        v["paths"] = dict(ch.paths)
        v["urls"] = {k: (base.rstrip("/") + p) if base else None for k, p in ch.paths.items()}
        v["base_verified"] = bool(verified)
        return v

    @r.get("/channels")
    async def channels(request: Request, user: UIUser = Depends(current_user)):
        mgr = manager()
        ui = panel.ui
        base, verified = ui.public_base_url(request) if ui is not None else (None, False)
        return {"ok": True, "channels": [view(ch, request) for ch in mgr.channels.values()],
                "base_url": base, "base_verified": bool(verified)}

    def clean_values(ch: Channel, body: dict) -> tuple[dict, dict]:
        values_in = body.get("values") or {}
        secrets_in = body.get("secrets") or {}
        if not isinstance(values_in, dict) or not isinstance(secrets_in, dict):
            raise err(400, "bad_request", "values ve secrets nesne olmalı.")
        plain = {f["name"]: f for f in ch.fields if f["kind"] != "secret"}
        secret_fields = {f["name"] for f in ch.fields if f["kind"] == "secret"}
        enabled_name = f"{ch.prefix}_ENABLED"
        values: dict = {}
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise err(422, "invalid_value", "enabled: true/false bekleniyor.")
            values[enabled_name] = body["enabled"]
        for name, value in values_in.items():
            spec = plain.get(name)
            if spec is None:
                raise err(404, "not_found", f"{name} bu kanalın ayarı değil.")
            if spec["kind"] == "list":
                if not isinstance(value, (list, str)):
                    raise err(422, "invalid_value", f"{name}: liste bekleniyor.")
                items = split_list(value)
                norm: list[str] = []
                for item in items:
                    if not ch.valid_entry(item):
                        raise err(422, "invalid_value", f"{name}: '{item[:40]}' geçersiz. {ch.entry_hint()}")
                    n = ch.normalise(item)
                    if n not in norm:
                        norm.append(n)
                if len(norm) > 50:
                    raise err(422, "invalid_value", f"{name}: en fazla 50 kayıt.")
                values[name] = ", ".join(norm)
            else:
                values[name] = value
        secrets: dict = {}
        for name, value in secrets_in.items():
            if name not in secret_fields:
                raise err(404, "not_found", f"{name} bu kanalın gizli değeri değil.")
            if value in (None, ""):
                continue  # write-only: an empty box means "unchanged"
            secrets[name] = value
        return values, secrets

    @r.put("/channels/{name}")
    async def update(name: str, request: Request, user: UIUser = Depends(current_user)):
        ch = pick(name)
        body = await body_of(request)
        values, secrets = clean_values(ch, body)
        result = await panel._save(values, secrets, user)
        manager().config.invalidate()
        return {"ok": True, "applied": result, "channel": view(ch, request)}

    @r.post("/channels/{name}/test")
    async def test(name: str, request: Request, user: UIUser = Depends(current_user)):
        ch = pick(name)
        manager().config.invalidate()
        try:
            message = await ch.test()
        except ChannelError as exc:
            ch.note_error(exc.code)
            raise err(424, exc.code, exc.message) from None
        return {"ok": True, "message": message, "channel": view(ch, request)}

    @r.post("/channels/{name}/secret")
    async def generate_secret(name: str, request: Request, user: UIUser = Depends(current_user)):
        ch = pick(name)
        body = await body_of(request)
        wanted = body.get("name") or next((f["name"] for f in ch.fields if f.get("generate")), None)
        if wanted not in {f["name"] for f in ch.fields if f.get("generate")}:
            raise err(404, "not_found", "Bu kanal için üretilebilir bir değer yok.")
        value = _secrets.token_urlsafe(24)
        await panel._save({}, {wanted: value}, user)
        manager().config.invalidate()
        # the one and only time the value is returned (like the bridge token); afterwards only …last4
        return {"ok": True, "name": wanted, "value": value, "secret": panel.field(wanted)}

    return r
