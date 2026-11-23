"""Secret registry and log redaction.

Every value passed to :func:`register_secret` (and every value read with
:func:`stack_kit.settings.secret`) is replaced with ``***`` in log messages
emitted through the standard :mod:`logging` module, once
:func:`install_log_redaction` has run (``create_app`` and ``TelegramBot`` call it).
"""

from __future__ import annotations

import logging
import threading
import traceback

_MIN_LEN = 6  # very short values would cause false-positive redaction
_secrets: set[str] = set()
_lock = threading.Lock()
_installed = False

REVEAL_ATTR = "stack_kit_reveal"  # LogRecord extra: skip redaction (used once for the generated token)


def register_secret(value: str | None) -> None:
    """Remember ``value`` so it never appears in logs or error texts produced by stack_kit."""
    if value and len(value) >= _MIN_LEN:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    """Return ``text`` with every registered secret replaced by ``***``."""
    if not text:
        return text
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for s in secrets:
        if s in text:
            text = text.replace(s, "***")
    return text


def install_log_redaction() -> None:
    """Wrap the global LogRecord factory so formatted messages are redacted. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        record = old_factory(*args, **kwargs)
        original = record.getMessage

        def get_message() -> str:
            msg = original()
            if getattr(record, REVEAL_ATTR, False):
                return msg
            return redact(msg)

        record.getMessage = get_message  # type: ignore[method-assign]
        if record.exc_info and record.exc_info[0] is not None and not getattr(record, REVEAL_ATTR, False):
            # pre-format the traceback so formatters print a redacted copy
            text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            record.exc_text = redact(text)
        return record

    logging.setLogRecordFactory(factory)
    # httpx logs full request URLs at INFO; Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
