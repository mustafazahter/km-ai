---
name: add-memory-policy
description: Change what ai-bot remembers or refuses to remember (system prompt rules, secret filters, limits, how many memories go into context) with tests. Use when the user says the bot remembers too much/too little or must never store some kind of data.
---

# Change the memory policy

The AI decides what to remember; the rules live in three places:

| What | Where |
|---|---|
| The text that teaches the AI when to remember and forget | `src/ai_bot/agent.py` → the `HAFIZA / MEMORY` section in `SYSTEM_PROMPT` |
| Hard refusal (never stored even if the AI asks): token, card, password patterns | `src/ai_bot/memory.py` → `BLOCK_PATTERNS` |
| Limits | env: `MEMORY_MAX_PER_CHAT` (500), `MEMORY_CONTEXT_LIMIT` (how many memories are added to each message, 6); code: `MAX_MEMORY_CHARS` |

Steps:
1. To add a forbidden data type, add a regex to `BLOCK_PATTERNS` (e.g. IBAN):
   ```python
   re.compile(r"\bTR\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.IGNORECASE),
   ```
2. For a behaviour change, edit the items in `SYSTEM_PROMPT`; write them short and in the imperative. To give extra instructions without changing code there is also the `SYSTEM_PROMPT_EXTRA` env.
3. Add a test (a new example in the parameter list of `tests/test_memory.py::test_secrets_are_not_stored`) and run:
   ```bash
   .venv/bin/python -m pytest tests/test_memory.py tests/test_agent.py
   ```
4. Update the user-facing description in `docs/MEMORY.md` and `docs/tr/MEMORY.md`.

Never: share memory across chats (every query is filtered by `chat_id`), loosen the secret patterns.
