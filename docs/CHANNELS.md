English · [Türkçe](tr/CHANNELS.md)

# Messaging channels (WhatsApp, Microsoft Teams, Slack, email)

Besides Telegram, you can talk to the bot from WhatsApp, Microsoft Teams, Slack and email. The same AI, the same tools, the same memory and the same task scheduler run behind every channel. **Every channel is off by default**; while it is off, its inbound addresses return `404`.

In the panel, the `Kanallar` (Channels) page (`/ui/#/kanallar`) holds the setup guide, the settings form, the allow list and the test button for each channel. This document gives the same information from a command-line / environment-variable point of view.

## Summary

| Channel | Inbound path | How messages arrive | Tools that need approval |
|---|---|---|---|
| WhatsApp | `POST/GET /channels/whatsapp/webhook` | Meta webhook (signed) | Yes, with a code (`EVET 123456`) |
| Microsoft Teams | `POST /channels/teams/messages` | Azure Bot Service (JWT) | Yes, with a code |
| Slack | `POST /channels/slack/events` | Events API (signed) | Yes, with a code (from a DM) |
| Email | none (the bot polls) | IMAP, every `EMAIL_POLL_SECONDS` s | **No** (rejected) |

The path of a message: the provider signature is verified → the allow list, duplicate check (dedupe) and rate limit are checked → `ai.run_ai(chat_id, text)` (same chat lock, same tool loop) → the reply is split to the provider's limit and sent back the same way. Webhooks return `200` immediately; the work runs in the background.

## Security model

- **Closed by default.** While `*_ENABLED` is `false`, the matching inbound addresses return `404`; their existence is not even visible.
- **Their own signatures.** These addresses are protected not by the Komuta login but by the provider's signature: WhatsApp `X-Hub-Signature-256` (HMAC-SHA256, raw body), Slack `X-Slack-Signature` (HMAC-SHA256 + 5 min freshness), Teams Bot Framework JWT (RS256). The signature is verified before the body is parsed and with a constant-time comparison; a missing or wrong signature gives `403` (Teams: `401`).
- **Allow list.** Every channel has its own list. **Empty list = nobody can write**; there is no "allow everyone" value. A message from a sender who is not on the list is dropped silently (the ID shows up in the panel under "Reddedilen son gönderenler" (recently rejected senders), where you can allow it with one click).
- **Chat ID.** For channel senders, a stable **negative** integer is derived from the digest `sha256(channel + ":" + external_id)`: `-(2**52 + (first 8 bytes of the digest mod 2**52 - 1))`. The magnitude lies between 2**52 and 2**53-2: it is represented exactly in JavaScript (the panel's Memory/Tasks pages read the JSON number) and does not collide with Telegram IDs (users are positive and below 2**52, groups are roughly -1e12..-1e13). Memory, history and tasks are per sender and are never mixed with Telegram chats. `resolve_chat` accepts these IDs only **inside the turn that user started themselves** (an internal context variable) and only while the sender is **on the allow list of an enabled channel**; callers from the bridge (`/bridge`), MCP, `/hook/notify` and `ai.ask` cannot reach a channel user's chat through memory, tasks or messages. When the list changes or the channel is turned off, the tools close for that user immediately.
- **Replay attacks.** A signed WhatsApp body can be resent if it is captured; so a message whose `timestamp` is older than 10 minutes is not processed (for Teams activities the same rule applies to the `timestamp` field; Slack is already limited to 5 minutes). If the field is missing, the check is skipped. The dedupe memory is in-process: in the 10-minute window after a restart, the same message can be processed once more.
- **Limits.** Input is at most 4000 characters (anything longer is rejected; in email it is truncated), 20 messages per minute per sender, dedupe by the provider's message ID, group/bot/non-text events are ignored, replies are split at the provider's limit (WhatsApp 4000, Slack 3000, Teams 4000, email a single message).
- **Privacy.** Message bodies and secret values are not written to the log; only short safe codes (such as `whatsapp_auth`) are stored as errors. The panel API never returns a secret value (only `…son4`, the last 4 characters). Provider error bodies are not passed through to the panel.
- **Human approval.** For tools that need approval (`confirm=True` or the `trade`/`admin` scope), on WhatsApp, Teams and Slack the bot sends a preview plus a one-time 6-digit code. To approve, **the same sender** has to write `EVET 123456` (within `CONFIRM_TIMEOUT`, default 120 s). A wrong code, `HAYIR`, any other text or a timeout = **no**; there is only one attempt. A code written by another user does not give this approval. On Slack, the approval request goes to the user's DM even if the message came from a channel. If the approval preview is too long to show in full (4000 characters; 3000 on Telegram), **no approval is requested and the tool call is rejected**: nobody approves a truncated preview.
- **Why no approval in email?** The `From` header is easy to forge; anyone who forges it could also give an approval by email. So email turns have no approver: tools that need approval are **rejected**, they do not run. Also, turns that start from email count as "outside content" (tainted): writing to memory and adding a task also need approval, which means they cannot be done from email. In email turns `web.read` is never shown to the model (so a mail with injected instructions cannot embed memory contents in an address and leak them); `web.search` stays.
- **SSRF.** The reply addresses for WhatsApp, Slack and Microsoft are fixed. The Teams reply address (`serviceUrl`) must be https, port 443, not an IP, end in a Bot Framework suffix (`.botframework.com`, `.trafficmanager.net`, `.azure.com`, `smba.*.microsoft.com`) and equal the `serviceurl` value inside the signed JWT; otherwise the request is rejected and the bot token is sent nowhere. The mail server name is resolved; metadata, link-local, `0.0.0.0` and multicast addresses are blocked (a mail server on the local network is allowed).

## Commands

On every channel `/hafiza`, `/gorevler`, `/model`, `/sifirla`, `/help` and the commands you add work (Telegram included). You type `/command` or `!command`; Slack treats a `/...` message as its own command, so use `!hafiza` there. Commands that need approval are approved with a code (`EVET 123456`). **In email**, because the sender cannot be verified, only read commands work; commands that change something get a "this is off on this channel" reply (`"bu kanalda kapalı"`). To add your own command: [DEVELOPER.md](DEVELOPER.md#adding-a-command-on-every-channel). Implementation: `src/ai_bot/commands.py`, tests: `tests/test_channel_commands.py`.

Precedence is the same everywhere: **saved in the panel > environment variable > default**. Values saved from the panel do not need a restart. Secrets are write-only; after saving, the panel shows only `…son4`.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `WHATSAPP_ENABLED` | bool | `false` | WhatsApp channel is on |
| `WHATSAPP_PHONE_NUMBER_ID` | text | — | Cloud API phone number ID |
| `WHATSAPP_ALLOWED_NUMBERS` | list | empty | Allowed numbers (with country code, comma-separated; `+`, spaces and `-` are ignored) |
| `WHATSAPP_TOKEN` | **secret** | — | Access token (a permanent system user token is recommended) |
| `WHATSAPP_APP_SECRET` | **secret** | — | App secret (signature verification) |
| `WHATSAPP_VERIFY_TOKEN` | **secret** | — | Webhook verification code (use "Üret" (Generate) in the panel) |
| `TEAMS_ENABLED` | bool | `false` | Teams channel is on |
| `TEAMS_APP_ID` | text | — | Azure Bot "Microsoft App ID" |
| `TEAMS_TENANT_ID` | text | empty | Tenant ID for a single-tenant bot (if empty, `botframework.com`) |
| `TEAMS_ALLOWED_USERS` | list | empty | Entra Object ID only (`aadObjectId`, GUID); `from.id` is not matched |
| `TEAMS_APP_PASSWORD` | **secret** | — | Client secret |
| `SLACK_ENABLED` | bool | `false` | Slack channel is on |
| `SLACK_TEAM_ID` | text | empty | Workspace ID (`T…`). If set, the event's `team_id`/`authorizations` value must match (recommended) |
| `SLACK_ALLOWED_USERS` | list | empty | Slack member IDs (`U…`) |
| `SLACK_BOT_TOKEN` | **secret** | — | Bot User OAuth Token (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | **secret** | — | Signing secret |
| `EMAIL_ENABLED` | bool | `false` | Email channel is on |
| `EMAIL_IMAP_HOST` / `EMAIL_IMAP_PORT` / `EMAIL_IMAP_USER` | text / number / text | — / `993` / — | IMAP (TLS only) |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | text / number | — / `587` | SMTP; `465` = SSL, others STARTTLS (if STARTTLS is not offered, nothing is sent) |
| `EMAIL_FROM` | text | — | Sender address of the replies |
| `EMAIL_ALLOWED_SENDERS` | list | empty | Full email addresses (case-insensitive) |
| `EMAIL_POLL_SECONDS` | number | `60` | Polling interval (at least 30) |
| `EMAIL_AUTHSERV_ID` | text | empty | The `Authentication-Results` identifier of your mail server (recommended; see below) |
| `EMAIL_REQUIRE_AUTH_RESULTS` | bool | `true` | Drop mail that does not pass aligned DKIM/SPF/DMARC |
| `EMAIL_PASSWORD` | **secret** | — | App password (SMTP login uses it too) |

Lists are comma-separated. These settings live in `runtime_config.SCHEMA`/`SECRET_NAMES` (the "kanallar" (channels) group); the security locks (the approval rule, the SSRF limits, the prompt-injection lock) cannot be loosened with these settings.

## What to do on the Komuta side

- The `/channels/*` paths must be **exempt from the Komuta login** (a path rule); otherwise the Meta/Slack/Microsoft servers run into the login wall. On these paths, security comes from the provider signature.
- The service's public address must be **https** (none of the three accept http). The addresses shown in the panel are built from `UI_PUBLIC_URL` (if set) or the browser's `Host` value; `X-Forwarded-Host` is not trusted. If the public address is different, set `UI_PUBLIC_URL`.
- For email, the service must be able to open outbound IMAP (993) and SMTP (587/465) connections (egress network permission).

## 1) WhatsApp (Meta Cloud API)

1. On developers.facebook.com, create a **Business** app and add **WhatsApp** as a product.
2. **WhatsApp > API Setup**: enter the **Phone number ID** of the sending number as `WHATSAPP_PHONE_NUMBER_ID`.
3. Permanent access token: business.facebook.com > Business Settings > Users > **System Users** > create a system user, assign the app, **Generate token**, permissions `whatsapp_business_messaging` and `whatsapp_business_management` → `WHATSAPP_TOKEN`. (The temporary token in API Setup expires after 24 hours.)
4. **App settings > Basic > App secret** → `WHATSAPP_APP_SECRET`.
5. In the panel, **Doğrulama kodu > Üret** (Verification code > Generate): `WHATSAPP_VERIFY_TOKEN` is created and shown **once**; copy it. (If you want to supply it from the environment, pick a random value yourself.)
6. Turn the channel on (`WHATSAPP_ENABLED=true`).
7. **WhatsApp > Configuration > Webhook > Edit**: Callback URL `https://<your-domain>/channels/whatsapp/webhook`, Verify token = the value from step 5 → **Verify and save**. Then, under **Webhook fields**, **Subscribe** to the `messages` field.
8. Add your number to the `WHATSAPP_ALLOWED_NUMBERS` list (`905551112233`). With the test number, only the recipients you added to the "To" field in API Setup can exchange messages.
9. In the panel, **Test et** (Test): checks the token and the number ID with `GET /v21.0/{phone_number_id}`; it sends a message to nobody.

Notes: free text can only be sent within **24 hours** after the user's last message (reminders too); template messages are not supported.

## 2) Microsoft Teams (Azure Bot Service)

1. portal.azure.com > **Create a resource > Azure Bot** (type: Single Tenant is recommended).
2. **Configuration**: `Microsoft App ID` → `TEAMS_APP_ID`, `App Tenant ID` → `TEAMS_TENANT_ID`.
3. **Manage Password > Certificates & secrets > New client secret**: **Value** → `TEAMS_APP_PASSWORD` (visible only once).
4. **Messaging endpoint**: `https://<your-domain>/channels/teams/messages` → Apply.
5. Add the **Channels > Microsoft Teams** channel.
6. To make it appear in Teams, the app package: dev.teams.microsoft.com > Apps > New app > App features > **Bot** (Bot ID = Microsoft App ID, scope **Personal**) > Publish > Preview in Teams.
7. Add the user's **Object ID** (GUID; Entra ID > Users) to the `TEAMS_ALLOWED_USERS` list (`29:…` IDs are not accepted). If you do not know it, write to the bot; the ID appears in the panel under "Reddedilen son gönderenler" (recently rejected senders).
8. `TEAMS_ENABLED=true`, **Test et** (Test) in the panel (gets a bot token from Microsoft), then write from Teams.

Activity checks: `channelId` must be `msteams`; if `TEAMS_TENANT_ID` is set, `channelData.tenant.id` must equal it (another tenant's bot cannot write to yours); only the `personal` conversation type; the user is matched only by `from.aadObjectId`; a `timestamp` older than 10 minutes is ignored.

JWT checks: `Authorization: Bearer`, RS256, key from `https://login.botframework.com/v1/.well-known/openidconfiguration` → `jwks_uri` (cached; an unknown `kid` triggers a refresh at most once every 30 s), `iss = https://api.botframework.com`, `aud = TEAMS_APP_ID`, `exp`/`nbf` (60 s tolerance), `serviceurl` claim = the activity's `serviceUrl` value. v1: only **one-to-one (personal)** chats; channels and group chats are ignored. For messages the bot starts itself (reminders), the user must have written to it before (the conversation reference is stored in `DATA_DIR/channel-refs.json`; only IDs and the service address, no secrets).

## 3) Slack

1. api.slack.com/apps > **Create New App > From scratch**.
2. **OAuth & Permissions > Bot Token Scopes**: `chat:write`, `im:history`, `app_mentions:read`.
3. **App Home > Messages Tab** on, and "Allow users to send … messages from the messages tab" checked.
4. **Install to Workspace** → **Bot User OAuth Token** → `SLACK_BOT_TOKEN`.
5. **Basic Information > Signing Secret** → `SLACK_SIGNING_SECRET`.
6. Set `SLACK_ENABLED=true` and save, then **Event Subscriptions > Enable Events**: Request URL `https://<your-domain>/channels/slack/events` (once the signature passes, it shows `Verified`).
7. **Subscribe to bot events**: `message.im` and `app_mention` → Save Changes (reinstall the app if needed).
8. From your profile, **Copy member ID** (`U…`) → `SLACK_ALLOWED_USERS`. It is recommended to enter the workspace ID (`T…`) as `SLACK_TEAM_ID`. Send the bot a DM; you can also mention it in a channel with `@bot`: the reply (because it may contain memory or tool output) goes **to your DM, not the channel**, and only a "yanıtı DM'den gönderdim" (I sent the reply by DM) note is posted in the channel.

Slack retries (`X-Slack-Retry-Num`) are answered with `200` without processing; bot/subtype messages are ignored.

## 4) Email

1. Open a **separate mailbox** for the bot. Enable IMAP access; if two-step verification is on, generate an **app password** (Gmail: Google Account > Security > 2-Step Verification > App passwords; Microsoft 365: account security > App passwords, and on the admin side IMAP + Authenticated SMTP; Yandex/Zoho: Security > App passwords).
2. `EMAIL_IMAP_HOST` (e.g. `imap.gmail.com`), `EMAIL_IMAP_PORT=993`, `EMAIL_IMAP_USER` (usually the full address), `EMAIL_PASSWORD`.
3. `EMAIL_SMTP_HOST` (e.g. `smtp.gmail.com`), `EMAIL_SMTP_PORT` (`587` STARTTLS or `465` SSL), `EMAIL_FROM`.
4. `EMAIL_ALLOWED_SENDERS` = the full addresses you will exchange mail with the bot from.
5. Keep `EMAIL_REQUIRE_AUTH_RESULTS=true` (see below), `EMAIL_ENABLED=true`.
6. In the panel, **Test et** (Test): IMAP login + SMTP `EHLO/STARTTLS/login/NOOP`; it sends no email.

Behavior: it reads the **unread** mail in `INBOX` every `EMAIL_POLL_SECONDS` (at most 10 mails per poll, anything over 1 MB is skipped). Only mail from allowed senders is read and marked "seen"; other mail is left untouched. The body is the `text/plain` part (if there is none, text from the HTML); quoted lines (`>`), the "On … wrote:" section, the signature and attachments are dropped; at most 4000 characters. The reply goes out from the `EMAIL_FROM` address with a `Re:` subject, `In-Reply-To`/`References` and `Auto-Submitted: auto-replied` headers.

**Spoofing defense.** The `From` header must be single and must contain a single address (two `From` headers or two addresses = forged). When `EMAIL_REQUIRE_AUTH_RESULTS` is on, an **aligned** pass is looked for in the `Authentication-Results` header written by **your own mail server**; a bare "pass" is not enough, the identifying property is required:

- `spf=pass` counts only if the `smtp.mailfrom` field is present and its domain is **the same as** the `From` domain (`smtp.helo` never counts; without `smtp.mailfrom` it fails);
- `dkim=pass` counts only if `header.d` is present and equal to the `From` domain or a parent domain of it (on a dot boundary; public suffixes such as `com` or `co.uk` never);
- `dmarc=pass` counts only if `header.from` is the same as the `From` domain.

When `EMAIL_AUTHSERV_ID` (recommended) is empty, the **topmost** header is trusted: if your server writes its own header at the top of every mail, this is safe; if it does not, the sender can carry their own forged header at the top. When it is set, only a header that carries this identifier **and is at the top** counts; a second header carrying the same identifier (a forged position) drops the mail. Read the identifier from the raw headers of a mail (if it is `Authentication-Results: mx.google.com; …`, it is `mx.google.com`). If the header is missing or fails, the mail is dropped and (for privacy) stays unread. If your provider does not add this header, we recommend switching providers instead of turning the option off: if you turn it off, someone can give the bot commands by spoofing an allowed address.

Cost: the sender and identity check is done **by downloading only the headers**; the body is downloaded only for mail that passes the check. At most 30 headers and 10 bodies are processed per poll (the rest wait for the next poll); skipped mails are remembered by `(UIDVALIDITY, UID)`.

Skipped: `Auto-Submitted` (if its value is not `no`), `Precedence: bulk/junk/list`, `List-Id`/`List-Unsubscribe`, `no-reply`/`mailer-daemon` senders, your own address.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| The provider cannot verify the webhook (Meta/Slack) | The channel is off (404), the address is http, or the Komuta login is in the way | Turn on `*_ENABLED`, use an https address, exempt the `/channels/*` path from the Komuta login |
| WhatsApp verification `403` | The verify token is different | Use "Üret" (Generate) again in the panel, enter the new value in Meta |
| `403` on WhatsApp messages | `WHATSAPP_APP_SECRET` is wrong | Copy it again from App settings > Basic |
| Slack `403` | The signing secret is wrong or the server clock is off by more than 5 min | Check the Signing Secret, NTP |
| Teams `401` | `TEAMS_APP_ID` is not the same as in Azure, or an expiry/clock problem | Check the App ID; the server clock |
| Teams `403 service url` | The activity's `serviceUrl` value is not in the Bot Framework suffixes | Use only Azure Bot Service |
| Status "Hata" (Error) in the panel | The last send failed | The code on the badge: `*_auth` token/password, `*_not_found` ID, `*_timeout`/`*_unreachable` network, `email_tls` certificate |
| The bot does not reply to anyone | The allow list is empty or the ID is wrong | Add the ID from the "Reddedilen son gönderenler" (recently rejected senders) list |
| The WhatsApp reminder is not delivered | The 24-hour window has passed | Ask the user to write a message to the bot |
| The Teams reminder is not delivered | There is no conversation reference | Ask the user to write to the bot once |
| No email arrives at all | The sender is not on the list, DKIM/SPF does not pass, IMAP is off | The test button; the "message dropped" lines in the server log; `EMAIL_ALLOWED_SENDERS` |
| `email_auth` | A normal password is being used | Generate an app password |
| `email_tls` | The certificate name does not match the server | Enter the correct server name; self-signed certificates are not accepted |
| `email_starttls` | The SMTP port is plain text | Use `587` (STARTTLS) or `465` (SSL) |
| An approval was requested but the action did not run | The code is wrong/late, or it is the email channel | `EVET 123456` exactly, within 120 s, from the same account; there is no approval in email |
| Panel API `424` | The provider rejected or was unreachable | A Turkish message and code are shown; details are in the provider's panel |

## Not supported in v1

- WhatsApp: template messages, media (audio/image/document), groups, read receipts.
- Teams: channel and group chats, Adaptive Card/button approval (approval is done with a code in text), file sharing, emulator/local test tokens (only Bot Framework Service tokens).
- Slack: listening to channels outside threads (only DMs and `@bot`), slash commands, Block Kit, files, Socket Mode.
- Email: tools that need approval, attachments, multiple mailboxes, OAuth2 login (only password/app password), IMAP IDLE (polling is used), quoting in the reply.
- All: commands work on every channel (`/hafiza`, `/gorevler`…) but in email only read commands; `/start` and `/id` are Telegram-only. One bot account per channel. Inbound media is silently ignored. There is no "allow everyone" in the channel settings, and there will not be.

## Known limits and privacy notes (not fixed)

- **Messages go to the AI provider.** Text from the channel, memory and tool outputs are passed to the LLM provider you configured (and, for web search, to the search provider) in the same way as for Telegram; turning on a channel opens this data flow for that channel's users too.
- **History and memory are stored.** The chat history and memories of channel users are kept in the SQLite database (`DATA_DIR`) unencrypted, per sender (under the derived negative chat ID); they may show up on the `/ui/#/hafiza` page with an "izinli değil" (not allowed) label (the label only looks at the Telegram allow list). Use that page to delete them.
- **Recently rejected senders in the panel** (at most 5 IDs, including phone number/email) are kept in memory, are wiped on restart and are shown only to a signed-in admin.
- **Teams conversation reference** (`DATA_DIR/channel-refs.json`): IDs and the service address; no secrets, file mode 600.
- **`web.search` stays on in email.** `web.read` is off, but the search query goes to the search provider; an injected mail can make the query carry memory text (nobody but the provider sees it). To turn it off completely, do not use the email channel.
- **When `EMAIL_AUTHSERV_ID` is empty**, the topmost `Authentication-Results` header is trusted (see above).
- **The dedupe memory is in-process** (reset on restart); the replay window is 10 minutes.
- **Slack:** when `SLACK_TEAM_ID` is empty, no workspace check is made; for replying to a DM the bot's `chat:write` permission is enough.
- Channel users talk only in their own channels, not in the `/ui` panel; a channel gives no panel authority.
