"""Normalization (section 3).

Converts raw external data into canonical AssociationRecords. Derives only what
is safe to derive (continent from confederation); anything unknown stays null
with needs_review=True (section 35).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.data.schemas import AssociationRecord
from app.data.sources import CONFEDERATION_CONTINENT

IT_NAME_SOURCE = "manual-translation-it"


@lru_cache(maxsize=1)
def _load_italian_names() -> dict[str, str]:
    """Load FIFA code -> Italian name mapping from the baseline data file.

    The mapping is read once and cached: an update normalizes 211 records,
    each calling back into this function.
    """
    path = Path(get_settings().data_baseline_dir) / "name_it.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def italian_name(code: str) -> Optional[str]:
    return _load_italian_names().get(code.upper())


def normalize_fifa_code(raw: str) -> str:
    return raw.strip().upper()


def derive_continent(confederation: str) -> Optional[str]:
    return CONFEDERATION_CONTINENT.get(confederation)


def normalize_name(raw: str) -> str:
    return " ".join(raw.split())


def to_record(
    *,
    fifa_code: str,
    name: str,
    confederation: str,
    flag_url: str,
    source: str = "fifa-associations",
    retrieved_at: Optional[datetime] = None,
    name_it: Optional[str] = None,
) -> AssociationRecord:
    now = retrieved_at or datetime.now(timezone.utc)
    code = normalize_fifa_code(fifa_code)
    name_clean = normalize_name(name)
    name_it_clean = name_it or italian_name(code)
    confederation = confederation.strip().upper()
    return AssociationRecord(
        fifa_code=code,
        name=name_clean,
        name_it=name_it_clean,
        confederation=confederation,
        continent=derive_continent(confederation),
        status="member",
        flag_url=flag_url,
        needs_review=False,
        source=source,
        retrieved_at=now,
        confidence=1.0,
    )
