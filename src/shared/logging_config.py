"""
Logging setup — writes to logs/heartbug.log, rotating DAILY so each date gets its
own file. Today's log is logs/heartbug.log; at midnight it's renamed to
logs/heartbug.log.YYYY-MM-DD and a fresh file starts.

Observe:   tail -f logs/heartbug.log
Filter:    grep ROUTER logs/heartbug.log   |   grep CLAIM ...
A past day: cat logs/heartbug.log.2026-06-23
Reset dev: > logs/heartbug.log

Timezone: log timestamps follow the process's local time. In a container that is
set via TZ=Australia/Sydney (docker-compose) with tzdata installed (Dockerfile).
The setdefault below is a safety-net so timestamps are also correct when run
OUTSIDE docker (e.g. directly on the Mac), where TZ may be unset.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler

# single-timezone deployment: make sure the process clock is Sydney even if TZ
# wasn't exported (honours an explicit TZ if one is already set).
os.environ.setdefault("TZ", "Australia/Sydney")
try:
    time.tzset()          # apply TZ to time.localtime (POSIX)
except AttributeError:
    pass                  # Windows has no tzset


# ── per-turn call_id, auto-prefixed onto every log line ────────────────────────
# Set once at the top of a turn (set_call_id) and EVERY log record in that turn
# carries [call_id] — no need to thread call_id through provider/service
# signatures. Lines logged outside a turn (startup, infra) get no prefix, which is
# correct — there's no call then. Note: contextvars are per-process, so this tags
# host-process logs; separate server processes (KB, etc.) still receive call_id as
# an argument where their logs need it.
_call_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("call_id", default="")


def set_call_id(call_id: str) -> None:
    """Bind the current turn's call_id for auto-prefixing. Call at turn start."""
    _call_id_var.set(call_id or "")


class _CallIdFilter(logging.Filter):
    """Prefix each record's message with [call_id] when one is bound and the message
    doesn't already start with a [tag] (so lines that pass call_id explicitly aren't
    double-tagged)."""
    def filter(self, record: logging.LogRecord) -> bool:
        cid = _call_id_var.get()
        if cid:
            msg = record.getMessage()
            if not msg.startswith("["):
                record.msg = f"[{cid}] {msg}"
                record.args = ()
        return True


def setup_logging(logfile: str | None = None, keep_days: int = 14) -> logging.Logger:
    # Anchor the log file to an ABSOLUTE directory, not a path relative to the
    # process's working directory. The host and each MCP server (KB, etc.) are
    # SEPARATE processes; if the path were relative ("logs/heartbug.log"), each
    # process would resolve it against its own CWD and write to a DIFFERENT file —
    # so e.g. KB's "KB RETRIEVE" lines would land in a file separate from the host's
    # ROUTER/SYSTEM lines. Resolving against a fixed HEARTBUG_LOG_DIR makes every
    # process converge on ONE file. In docker set HEARTBUG_LOG_DIR=/app/logs (the
    # shared volume); locally point it at the repo's logs/ dir.
    if logfile is None:
        log_dir = os.environ.get("HEARTBUG_LOG_DIR", "/app/logs")
        logfile = os.path.join(log_dir, "heartbug.log")
    os.makedirs(os.path.dirname(logfile), exist_ok=True)   # ensure the dir exists

    logger = logging.getLogger("heartbug")
    logger.setLevel(logging.INFO)

    # attach the call_id filter once
    if not any(isinstance(f, _CallIdFilter) for f in logger.filters):
        logger.addFilter(_CallIdFilter())

    # avoid duplicate handlers if setup runs more than once (e.g. reload)
    if not any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers):
        handler = TimedRotatingFileHandler(
            logfile,
            when="midnight",      # rotate at midnight → one file per day
            interval=1,
            backupCount=keep_days,  # keep this many days, older auto-deleted
            encoding="utf-8",
        )
        # rotated files get suffixed with the date, e.g. heartbug.log.2026-06-23
        handler.suffix = "%Y-%m-%d"
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    return logger