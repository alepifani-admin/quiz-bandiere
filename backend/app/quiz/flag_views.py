"""Persistent tally of how often each flag appears in served questions.

Counts are kept in memory for speed and flushed to a small JSON file with a
short debounce, so question serving never blocks on disk I/O.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from app.core.config import get_settings

_lock = threading.Lock()
_counts: dict[str, int] = {}
_loaded = False
_last_save = 0.0
SAVE_INTERVAL = 5.0


def _path():
    return get_settings().cache_dir / "flag_views.json"


def _load() -> None:
    global _counts, _loaded
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        _counts = {str(k): int(v) for k, v in data.get("views", {}).items()}
    except (OSError, json.JSONDecodeError):
        _counts = {}
    _loaded = True


def _save() -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"views": _counts, "updated": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError:
        pass


def record(codes: list[str]) -> None:
    """Increment the view counter for every flag in the served question."""
    if not codes:
        return
    global _last_save
    with _lock:
        if not _loaded:
            _load()
        for code in codes:
            _counts[code] = _counts.get(code, 0) + 1
        now = time.time()
        if now - _last_save >= SAVE_INTERVAL:
            _save()
            _last_save = now


def top(limit: int = 10) -> list[dict]:
    """The flags most often shown, most-viewed first."""
    with _lock:
        if not _loaded:
            _load()
        ranked = sorted(_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"code": c, "views": n} for c, n in ranked[: max(1, limit)]]


def flush() -> None:
    """Force the counters to disk (used at shutdown/clear)."""
    with _lock:
        if _counts:
            _save()
