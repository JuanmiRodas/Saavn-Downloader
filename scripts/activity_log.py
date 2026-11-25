from __future__ import annotations

import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any


def _default_log_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            "Saavn-Downloader",
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
        )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "studio.log")


class ActivityLogger:
    """Thread-safe live activity logger writing to memory buffer (for Web UI) and persistent studio.log."""

    def __init__(self, max_entries: int = 2000, log_path: str | None = None):
        self._lock = threading.Lock()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._seq = 0
        self.log_path = log_path or _default_log_path()

    def log(self, tag: str, message: str, level: str = "INFO") -> dict[str, Any]:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self._lock:
            self._seq += 1
            entry = {
                "seq": self._seq,
                "ts": ts,
                "level": level.upper(),
                "tag": tag.upper(),
                "message": str(message),
            }
            self._entries.append(entry)
            line = f"[{ts}] [{entry['level']}] [{entry['tag']}] {entry['message']}\n"
            try:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass
            return entry

    def get_since(self, since_seq: int = 0, limit: int = 500) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            items = [e for e in self._entries if e["seq"] > since_seq]
            if len(items) > limit:
                items = items[-limit:]
            return self._seq, items

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


activity_log = ActivityLogger()


def log_event(tag: str, message: str, level: str = "INFO") -> None:
    activity_log.log(tag, message, level=level)
