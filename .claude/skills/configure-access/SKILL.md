---
name: configure-access
description: Manage who can reach this stack — admin panel sign-in (Komuta identity or password, first-time setup code, password change, sign out everywhere, protection switch) and the bridge gate (/bridge, /mcp, /hook on/off, tokens shown once). Use when the user asks to secure, open, close or reset access.
---

# Manage access

Threat model: the service is open to the internet, and anyone who knows the link can reach the address. The panel (`/ui`) and the machine paths (`/bridge/*`, `/mcp`, `/hook/*`) rely on their own protections.

## Panel sign-in (`stack_ui`)

| Situation | What to do |
|---|---|
| Published on Komuta | Default `komuta.yaml`: `access.paths: [{path: /ui, signIn: true}]` + `forwardIdentity: true`; only `/ui` asks for Komuta sign-in, while `/health`, `/bridge/`, `/mcp`, `/hook/`, `/channels/` are open without sign-in and carry their own protections (token, HMAC, JWT). The panel also asks for its own password; with `UI_AUTH=auto` no password is asked when a Komuta identity is present. To pin the address use `UI_PUBLIC_URL` or `UI_ALLOWED_HOSTS=<public host>`. To drop Komuta sign-in entirely: `examples/komuta-giris-kapali.yaml` |
| Password, first-time setup | `UI_AUTH=password` (or `default_auth="password"`) and no `UI_PASSWORD` → `/ui/setup`. The **setup code** is in the service's startup log: Komuta console → service → **Loglar** (Logs) → `Kurulum kodu`. The code is single-use and changes on restart; to supply your own, use `UI_SETUP_CODE`. The password is ≥ 10 characters and is written with scrypt to `DATA_DIR/ui-auth.json` (600). |
| Password from env | If `UI_PASSWORD` is given, setup is skipped; the password changes only through the env. |
| Change the password / sign everyone out | Panel → Erişim (Access) → "Parolayı değiştir" ("Change password", current + new) or "Tüm oturumları kapat" ("Close all sessions"). API: `POST /ui/api/auth/password`, `POST /ui/api/auth/sessions/revoke`. |
| Forgot the password | The **Parolamı unuttum** ("I forgot my password") link on the sign-in page (`/ui/reset`) writes a single-use **reset code**, valid for 15 minutes, to the service log (search the log for `sıfırlama`). Enter the code and the new password; on save all old sessions are closed. If `UI_PASSWORD` comes from the environment there is no reset; the password changes there. |
| Switch protection off (not recommended) | Panel → Erişim → password + `KORUMAYI KAPAT`. While off there is a red banner on every page; one click turns it back on. Strict setup: `UI_FORCE_PROTECTION=1` (switching off is refused). |

## Bridge gate (`stack_kit`)

```bash
python -m stack_kit gate status
python -m stack_kit gate disable mcp       # bridge | mcp | hook | all ; off = 404
python -m stack_kit gate enable bridge
python -m stack_kit gate rotate            # new bridge token, printed ONLY ONCE
python -m stack_kit gate rotate hook-secret
```

From the panel: Erişim → Köprü kapısı (Bridge gate) (when turned on with no token, one is generated automatically and shown once). The env `BRIDGE_TOKEN` / `HOOK_SECRET` are valid together with the gate values. Initial values: `BRIDGE_ENABLED`, `MCP_ENABLED`, `HOOKS_ENABLED`.

Verify:

```bash
python -m pytest -q tests
```

Never: commit a token, password or setup code, or paste one into chat/an issue; leave protection off "temporarily".
