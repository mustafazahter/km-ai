"""Messaging channels (WhatsApp, Microsoft Teams, Slack, e-mail) for the same agent as Telegram.

Kept import-light on purpose: ``ai_bot.runtime_config`` imports :mod:`ai_bot.channels.schema`, so this
package must not import ``core``/``agent`` at import time. Use ``ai_bot.channels.manager.ChannelManager``.
Documentation: docs/KANALLAR.md.
"""
