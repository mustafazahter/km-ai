English · [Türkçe](tr/SCHEDULING.md)

# Scheduled tasks

> Persistent scheduler (SQLite) with cron, relative ("in 10 minutes", "10 dakika sonra") and ISO times in `TZ`.
> A task either sends fixed text (`message`) or runs an AI prompt with tools when due (`prompt`).

## Example chats

- "Remind me to turn off the oven in 20 minutes" → `message`, one-off
- "Summarize the tech news every morning at 9" → `prompt`, cron `0 9 * * *`
- "Remind me to drink water on weekdays at 18:30" → `message`, cron `30 18 * * 1-5`
- "Remind me about the invoice on 1 December 2026 at 10" → `message`, ISO `2026-12-01T10:00`

## Time formats (`when`)

| Format | Example | Note |
|---|---|---|
| Cron (5 fields) | `0 9 * * *`, `*/30 8-18 * * 1-5`, `0 9 * * mon`, `@daily` | minute hour day month weekday; `*`, list, range, step, `jan..dec`, `sun..sat`; 0 and 7 = Sunday; if both day and weekday are restricted, either one is enough |
| Relative | `10 dakika sonra`, `2 saat sonra`, `3 gün sonra`, `in 10 minutes`, `+30m` | seconds/minutes/hours/days/weeks |
| ISO | `2026-12-01T09:00`, `2026-12-01T06:00:00Z` | if there is no time zone, `TZ` is assumed; a past time is rejected |

For expressions such as "tomorrow at 9", the AI converts them to ISO by looking at the current time in the system prompt.

## Kinds

| `kind` | When due |
|---|---|
| `message` | The text is sent as is (no emoji is added at the start; write one inside the text if you want) |
| `prompt` | The text is given to the AI as a prompt; the AI works with tools and the answer is sent. It is not written to the chat history. Tools that ask for confirmation are still asked with a button. |

## Behavior

- It is persistent: tasks are kept across restarts.
- A missed one-off task runs once at startup, **late**; a missed cron task runs once and is set to the next time after "now" (missed runs are not repeated one by one).
- A task is set to its next time before it is run; a crash does not run the same task twice.
- Limits: `SCHEDULE_MAX_PER_CHAT` (50) active tasks per chat (including one-offs waiting to run), a repeat interval of at least `SCHEDULE_MIN_INTERVAL_MINUTES` (5) minutes, a one-off task at least 60 s ahead and at most ~5 years ahead, text ≤2000 characters.
- Daylight saving time (DST) safe: the next time is computed as a real instant (a UTC timestamp). On a forward shift the nonexistent hour is skipped; on a backward shift, in the repeated hour an hourly task runs once per real hour and the same task is not triggered again and again. Tests: Europe/Berlin and America/New_York.
- Scheduled `prompt` tasks (and `ai.ask` from the bridge) **cannot create new tasks**: `schedule_add` is absent in these turns and is rejected; so a task cannot multiply itself.
- If web content was read in a turn, `schedule_add` in the same turn asks for user confirmation (a button on Telegram, a code on other channels). The loop wakes every `SCHEDULE_TICK` (20) s and when a new task is added.
- The time zone is `TZ` (default `Europe/Istanbul` in komuta.yaml).

## Management

| Command | What it does |
|---|---|
| `/gorevler` | Lists active tasks |
| `/gorevler iptal 3` | Cancels task number 3 |

In chat: "Cancel the morning summary" (the AI uses `schedule_list` + `schedule_cancel`). Event: `schedule.fired` on every run.
