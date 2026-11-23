"""Settings and secrets of the channels, in the shape ``ai_bot.runtime_config`` expects.

Lives here (not in ``runtime_config``) so the channel code owns its names. Precedence is the same as
everywhere else: value saved in the panel > environment variable > default. Security rule: an allow-list
may be empty (= nobody is allowed), there is no "allow everyone" value.
"""

from __future__ import annotations

from stack_ui import Setting

GROUP = "channels"
GROUP_LABEL = "Kanallar (WhatsApp, Teams, Slack, e-posta)"

HOST_PATTERN = r"([A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?)?"
ID_PATTERN = r"[A-Za-z0-9_.\-]{0,64}"
PHONE_LIST = r"[0-9+ ,()\-]{0,2000}"
TEAMS_LIST = r"[0-9A-Fa-f\-, ]{0,2000}"
SLACK_LIST = r"[A-Za-z0-9, ]{0,2000}"
EMAIL_LIST = r"[A-Za-z0-9._%+\-@, ]{0,2000}"
EMAIL_ADDR = r"[A-Za-z0-9._%+\-@<> ]{0,254}"

SCHEMA: list[Setting] = [
    Setting("WHATSAPP_ENABLED", "bool", False, "WhatsApp kanalı açık"),
    Setting("WHATSAPP_PHONE_NUMBER_ID", "str", "", "WhatsApp Cloud API telefon numarası kimliği", pattern=r"[0-9]{0,32}", max_length=32),
    Setting("WHATSAPP_ALLOWED_NUMBERS", "str", "", "Botla konuşabilen WhatsApp numaraları (virgüllü, ülke koduyla)",
            pattern=PHONE_LIST, max_length=2000),
    Setting("TEAMS_ENABLED", "bool", False, "Microsoft Teams kanalı açık"),
    Setting("TEAMS_APP_ID", "str", "", "Azure Bot Microsoft uygulama kimliği", pattern=ID_PATTERN, max_length=64),
    Setting("TEAMS_TENANT_ID", "str", "", "Azure kiracı kimliği (tek kiracılı bot için)", pattern=ID_PATTERN, max_length=64),
    Setting("TEAMS_ALLOWED_USERS", "str", "", "Botla konuşabilen Teams kullanıcıları (Entra Object ID / aadObjectId, GUID, virgüllü)",
            pattern=TEAMS_LIST, max_length=2000),
    Setting("SLACK_ENABLED", "bool", False, "Slack kanalı açık"),
    Setting("SLACK_TEAM_ID", "str", "", "Slack çalışma alanı kimliği (T…; doluysa başka çalışma alanından gelen olaylar reddedilir)",
            pattern=r"([A-Z0-9]{3,20})?", max_length=20),
    Setting("SLACK_ALLOWED_USERS", "str", "", "Botla konuşabilen Slack kullanıcı kimlikleri (U… , virgüllü)",
            pattern=SLACK_LIST, max_length=2000),
    Setting("EMAIL_ENABLED", "bool", False, "E-posta kanalı açık"),
    Setting("EMAIL_IMAP_HOST", "str", "", "IMAP sunucusu (gelen)", pattern=HOST_PATTERN, max_length=253),
    Setting("EMAIL_IMAP_PORT", "int", 993, "IMAP SSL portu", min=1, max=65535),
    Setting("EMAIL_IMAP_USER", "str", "", "Posta kutusu kullanıcı adı", pattern=EMAIL_ADDR, max_length=254),
    Setting("EMAIL_SMTP_HOST", "str", "", "SMTP sunucusu (giden)", pattern=HOST_PATTERN, max_length=253),
    Setting("EMAIL_SMTP_PORT", "int", 587, "SMTP portu (465 = SSL, diğerleri STARTTLS)", min=1, max=65535),
    Setting("EMAIL_FROM", "str", "", "Yanıtların gönderen adresi", pattern=EMAIL_ADDR, max_length=254),
    Setting("EMAIL_ALLOWED_SENDERS", "str", "", "Botla yazışabilen e-posta adresleri (tam adres, virgüllü)",
            pattern=EMAIL_LIST, max_length=2000),
    Setting("EMAIL_POLL_SECONDS", "int", 60, "Posta kutusu yoklama aralığı (sn, en az 30)", min=30, max=3600),
    Setting("EMAIL_AUTHSERV_ID", "str", "", "Posta sunucunuzun Authentication-Results kimliği (boşsa en üstteki başlık güvenilir sayılır)",
            pattern=HOST_PATTERN, max_length=253),
    Setting("EMAIL_REQUIRE_AUTH_RESULTS", "bool", True, "Gönderen sahteciliğine karşı DKIM/SPF geçmeyen postayı düşür"),
]

SECRET_NAMES = ["WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "TEAMS_APP_PASSWORD",
                "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "EMAIL_PASSWORD"]

NAMES = [s.name for s in SCHEMA]
