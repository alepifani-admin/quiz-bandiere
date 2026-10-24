"""Persistent score leaderboard.

Scores are stored in a dedicated JSON file (data/leaderboard.json) so the
best scores per difficulty level survive every app restart. Written
atomically (temp file + rename) to avoid corruption.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.config import get_settings

MAX_NICKNAME_LENGTH = 24
#: how many top entries are kept per difficulty level
MAX_PER_LEVEL = 20
#: characters allowed in a nickname (letters, digits, spaces, separators)
_NICKNAME_RE = re.compile(r"^[\w\s'.\-À-ž]+$")

#: serializes the read-modify-write of the leaderboard file so concurrent
#: score submissions (multi-player games post one score per player) never
#: race on the shared temp file or lose updates.
_write_lock = threading.Lock()

#: the frontend polls the board every few seconds, so the grouped reads are
#: cached in memory and only invalidated when a score is actually recorded.
_cache_lock = threading.Lock()
_cache: dict[tuple[str, int], tuple[float, dict]] = {}
CACHE_TTL_SECONDS = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {"version": 1, "scores": []}


def sanitize_nickname(nickname: str) -> str:
    """Trim, truncate and validate a nickname. Returns None-compatible str
    only; raise ValueError on invalid input."""
    nick = (nickname or "").strip()
    if not nick:
        raise ValueError("nickname is required")
    if len(nick) > MAX_NICKNAME_LENGTH:
        nick = nick[:MAX_NICKNAME_LENGTH].rstrip()
    if not _NICKNAME_RE.fullmatch(nick):
        raise ValueError("nickname contains invalid characters")
    return nick


def sanitize_flag(flag: str) -> str:
    """Normalize a flag country code: uppercase letters only (max 3).
    Returns an empty string when absent or invalid."""
    return re.sub(r"[^A-Za-z]", "", flag or "").upper()[:3]


def sanitize_scope(scope: str) -> str:
    """Normalize the quiz scope (leaderboard board): fifa or world."""
    scope = (scope or "fifa").strip().lower()
    return scope if scope in ("fifa", "world") else "fifa"


def load_scores() -> list[dict]:
    settings = get_settings()
    try:
        data = json.loads(settings.leaderboard.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    scores = data.get("scores") if isinstance(data, dict) else None
    return scores if isinstance(scores, list) else []


def _write_scores(scores: list[dict]) -> None:
    settings = get_settings()
    settings.leaderboard.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "scores": scores}
    tmp = settings.leaderboard.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(settings.leaderboard)


def _invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


def add_score(
    nickname: str,
    difficulty: int,
    score: int,
    total: int,
    language: str = "it",
    flag: str = "",
    scope: str = "fifa",
) -> dict:
    """Record one finished game. Returns the new entry with its rank."""
    nick = sanitize_nickname(nickname)
    flag_code = sanitize_flag(flag)
    scope_code = sanitize_scope(scope)
    difficulty = int(difficulty)
    if not 1 <= difficulty <= 7:
        raise ValueError("difficulty must be 1-7")
    score, total = int(score), int(total)
    if score < 0 or total <= 0 or score > total:
        raise ValueError("invalid score/total")

    entry = {
        "id": uuid.uuid4().hex,
        "nickname": nick,
        "flag": flag_code,
        "language": language,
        "difficulty": difficulty,
        "scope": scope_code,
        "score": score,
        "total": total,
        "accuracy": round(score / total, 4),
        "ts": _now_iso(),
    }

    with _write_lock:
        scores = load_scores()
        scores.append(entry)
        scores.sort(
            key=lambda e: (-e.get("score", 0), -e.get("accuracy", 0), e.get("ts", ""))
        )

        # keep only the best MAX_PER_LEVEL per scope + difficulty
        kept: list[dict] = []
        per_board: dict[tuple[str, int], int] = {}
        for e in scores:
            board = (e.get("scope", "fifa"), int(e.get("difficulty", 0)))
            if per_board.get(board, 0) >= MAX_PER_LEVEL:
                continue
            kept.append(e)
            per_board[board] = per_board.get(board, 0) + 1
        _write_scores(kept)
        _invalidate_cache()

    same_board = [
        e for e in kept
        if e.get("difficulty") == entry["difficulty"]
        and e.get("scope", "fifa") == entry["scope"]
    ]
    rank = next(
        (i + 1 for i, e in enumerate(same_board) if e.get("id") == entry["id"]),
        None,
    )
    return {**entry, "rank": rank}


def best_per_level(limit: int = 3, scope: str = "fifa") -> dict:
    """Top `limit` scores for each difficulty level of a quiz scope, grouped."""
    scope_code = sanitize_scope(scope)
    limit = max(1, int(limit))
    key = (scope_code, limit)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    scores = [e for e in load_scores() if e.get("scope", "fifa") == scope_code]
    grouped: dict[int, list[dict]] = {}
    for e in scores:
        level = int(e.get("difficulty", 0))
        grouped.setdefault(level, []).append(e)
    result: dict[str, list[dict]] = {}
    for level in sorted(grouped):
        result[str(level)] = grouped[level][:limit]
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def rank_of(entry_id: str) -> Optional[int]:
    """Rank (1-based) of a stored entry within its scope + difficulty level."""
    scores = load_scores()
    target: Optional[tuple[str, int]] = None
    for e in scores:
        if e.get("id") == entry_id:
            target = (e.get("scope", "fifa"), int(e.get("difficulty", 0)))
            break
    if target is None:
        return None
    scope_code, level = target
    same_board = [
        e for e in scores
        if int(e.get("difficulty", 0)) == level
        and e.get("scope", "fifa") == scope_code
    ]
    for i, e in enumerate(same_board):
        if e.get("id") == entry_id:
            return i + 1
    return None
