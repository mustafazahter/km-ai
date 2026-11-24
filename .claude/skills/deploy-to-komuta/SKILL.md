---
name: deploy-to-komuta
description: Validate, plan and publish this stack on Komuta from komuta.yaml using the Komuta MCP tools, with secrets entered as prompt secrets. Use when the user wants to deploy or update the stack on Komuta.
---

# Publish to Komuta

Prerequisites: the Komuta MCP is connected in the Claude session; the target project **already exists** on Komuta (`apply.createMissingProject: false`); the repo is on a git connection Komuta can reach.

1. Local checks (all must be clean):
   ```bash
   python -m pytest -q tests
   python <stack-spec>/tools/validate_stack.py .
   python -m stack_kit doctor
   ```
2. Fill in the user-specific placeholders in `komuta.yaml` (`target.project`, `target.connections.git`, `source.repository`). **Never write** a secret value: `BRIDGE_TOKEN`, `HOOK_SECRET`, `TELEGRAM_BOT_TOKEN`, `*_TOKEN`, `*_SECRET` are always bound through `secrets` (source `prompt` or `generate`) + `valueFrom.secret`.
3. With the Komuta MCP, in order (show the output of each step to the user):
   1. `komuta_validate_stack`: validate the manifest; fix any error and repeat.
   2. `komuta_plan_stack`: show what will be created/changed; the user enters prompt secrets here / in the Komuta UI.
   3. `komuta_apply_stack`: **only after the user has explicitly approved the plan.**
   4. `komuta_get_stack_run`: follow the run's status.
4. After publishing: `GET https://<service-address>/health` → `{"ok": true, ...}`; for protected endpoints use `Authorization: Bearer <BRIDGE_TOKEN>`.

Rules: never apply without the user's approval; never write secrets into chat history, files or commits; on stacks that move money, do not change the default mode (e.g. `MODE=demo`).

Note: this skill was not tried with a real Komuta apply while the kit was developed (the repos are not published yet); the tool names were taken from the Komuta MCP tool list.
