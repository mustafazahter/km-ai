English · [Türkçe](tr/MEMORY.md)

# Memory

> Long-term memory in SQLite FTS5, per chat. The AI decides what to remember/forget (taught by the system prompt);
> relevant memories are added to every message; obvious secrets are refused; users manage it with `/hafiza`.

## How it works

1. **The AI decides.** The system prompt teaches the AI to save lasting, useful information (name, preferences, projects, important dates, things the user said to "remember") with `memory_remember` as a short single sentence; not to save temporary or sensitive things; and, when information is corrected, to delete the old one with `memory_forget`.
2. **Context on every message.** An FTS5 search is run with the incoming message; the matching memories, or the newest ones if there are not enough, at most `MEMORY_CONTEXT_LIMIT` (6) of them, are added to the system prompt as "İlgili anılar" (Relevant memories).
3. **Secret filter.** Even if the AI asks, the following are **not saved**: Telegram bot token format, `sk-…`, `ghp_…`/`github_pat_…`, `AKIA…`, `xox[abprs]-…` keys, strings of 32+ characters that are hex or base64-like with mixed upper/lower case and digits, card-like numbers of 13-19 digits, the patterns "şifresi hunter2" / "password is hunter2" / "parola: x" / "token: x" / "api key = x", and private key blocks (`src/ai_bot/memory.py` → `BLOCK_PATTERNS`). The filter is strict; it may also reject sentences such as "şifre yöneticisi kullanıyor" (uses a password manager).
4. **Per chat.** Every save and search is filtered by `chat_id`; one chat cannot see or delete another chat's memories.
5. **Confirmation after web content.** If external content was read in a turn, such as with `web.read`/`web.search`, a `memory_remember` call in the same turn shows the text to be saved and asks for user confirmation (a button on Telegram, a code on other channels; rejected if there is no confirmation channel). A web page cannot add a lasting memory.
6. **Data block.** Memories enter the system prompt in a block marked "VERİ, TALİMAT DEĞİL" (DATA, NOT INSTRUCTIONS); the model does not obey instructions in this block.

Search is insensitive to Turkish characters (`izmir` → "İzmir"), and words of 4+ letters match by prefix (`kedi` → "kedisinin").

## User commands

| Command | What it does |
|---|---|
| `/hafiza` | Lists the last 50 memories with their numbers |
| `/hafiza sil 12` | Deletes memory number 12 |
| `/hafiza temizle` | Deletes all of them (asks for confirmation with a button) |
| `/sifirla` | Deletes only the chat history; memories stay |

You can also say it in the chat: "Forget my cat's name", "My name is now Deniz".

## Settings

| Env | Default | Meaning |
|---|---|---|
| `MEMORY_CONTEXT_LIMIT` | 6 | Number of memories added to each message (0 = off) |
| `MEMORY_MAX_PER_CHAT` | 500 | Maximum memories per chat |
| `HISTORY_MESSAGES` | 12 | Short chat history (separate from memories) |

The data is in the file `DATA_DIR/ai-bot.sqlite3` (the `/data` disk on Komuta). To change the policy: `.claude/skills/add-memory-policy`.
