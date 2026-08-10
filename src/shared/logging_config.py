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


def setup_logging(logfile: str = "logs/heartbug.log", keep_days: int = 14) -> logging.Logger:
    os.makedirs(os.path.dirname(logfile), exist_ok=True)   # ensure logs/ exists

    logger = logging.getLogger("heartbug")
    logger.setLevel(logging.INFO)

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