"""Persistent cache for the per-flag analysis (color/geometry/symbols).

Running the full analysis for all 211 flags takes tens of seconds, so the
resulting as_dict payloads are cached to disk as a single JSON file and reused
by the quiz engine and the review report generator.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

#: per-pool analysis cache files (FIFA + world flags)
ANALYSIS_FILES = {
    "fifa": "flag_analysis.json",
    "world": "world_analysis.json",
}


def cache_path(name: str = ANALYSIS_FILES["fifa"]) -> Path:
    return Path(get_settings().data_cache_dir) / name


def load_cached(name: str = ANALYSIS_FILES["fifa"]) -> Optional[dict[str, dict]]:
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save(analyses: dict[str, dict], name: str = ANALYSIS_FILES["fifa"]) -> Path:
    path = cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # atomic replace: a crash/concurrent writer can never leave a truncated cache
    tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
    tmp.write_text(
        json.dumps(analyses, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path
