"""FastAPI entrypoint (phase 13).

Exposes the quiz REST API:
- GET /                         backend info
- GET /api/health               liveness + data status
- GET /api/flags                list of associations (code, name, asset url)
- GET /api/flags/{code}/asset   local flag image
- GET /api/quiz/question        generate a multiple-choice question
- POST /api/quiz/answer         validate an answer
- GET /api/quiz/similar/{code}  most similar flags (distractor preview)
- GET /api/review/queue         flags needing review (admin)
- GET /api/stats/summary        dataset coverage statistics
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.quiz.engine import QuizEngine

settings = get_settings()
setup_logging(settings.debug)


def _asset_headers() -> dict:
    """Flag PNGs never change while the app runs: let the browser cache them
    (the frontend re-requests the same flags many times per game)."""
    return {"Cache-Control": "public, max-age=3600"}


def _warm_engines() -> None:
    """Pre-load both quiz indexes and their similarity rankings in a
    background thread so the first served question never pays the index load
    or the pairwise score computation."""
    for make in (get_engine, get_world_engine):
        try:
            engine = make()
            engine.index
            engine.precompute()
        except Exception:  # noqa: BLE001 - startup must never crash
            logging.getLogger(__name__).exception("engine warmup failed")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    threading.Thread(target=_warm_engines, daemon=True).start()
    yield


app = FastAPI(
    title="FIFA Flag Quiz API",
    version="0.2.0",
    description="Data-driven engine for FIFA association flag recognition quizzes.",
    lifespan=_lifespan,
)

_engine: Optional[QuizEngine] = None
_world_engine: Optional[QuizEngine] = None


class AnswerPayload(BaseModel):
    question: dict
    selected_index: int


class ScorePayload(BaseModel):
    nickname: str
    difficulty: int
    score: int
    total: int
    language: str = "it"
    flag: str = ""
    scope: str = "fifa"


def get_engine() -> QuizEngine:
    global _engine
    if _engine is None:
        _engine = QuizEngine()
    return _engine


def get_world_engine() -> QuizEngine:
    """Engine over the all-world-flags pool (Quiz Bandiere Mondo)."""
    global _world_engine
    if _world_engine is None:
        _world_engine = QuizEngine(pool="world")
    return _world_engine


def _api_root() -> dict:
    return {
        "app": "FIFA Flag Quiz API",
        "version": app.version,
        "docs": "/docs",
        "health": "/api/health",
        "quiz": "/api/quiz/question",
    }

@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "database": str(settings.db_file),
        "expected_associations": settings.expected_associations,
        "language": settings.default_language,
        "debug": settings.debug,
    }


@app.get("/api/flags")
def list_flags(language: str = "it") -> dict:
    from app.quiz.country_info import _dataset

    engine = get_engine()
    continents = {c: (d.get("continent") or "") for c, d in _dataset().items()}
    payloads = {}
    for code in engine.codes():
        payload = engine._target_payload(code, language)
        payload["continent"] = continents.get(code, "")
        payloads[code] = payload
    return {"count": len(payloads), "flags": payloads}


@app.get("/api/flags/cache")
def flags_cache() -> dict:
    """Local disk usage of the country-info cache."""
    from app.quiz.country_info import cache_status

    return cache_status()


@app.post("/api/flags/cache/clear")
def flags_cache_clear() -> dict:
    """Delete every cached country-info entry instantly."""
    from app.quiz.country_info import clear_cache

    return clear_cache()


@app.post("/api/flags/warmup")
def flags_warmup(force: bool = False) -> dict:
    """Pre-fill the info cache in the background (all flags).

    ``force=true`` re-fetches every country, otherwise only entries older
    than the cache TTL are refreshed.
    """
    from app.quiz.country_info import prewarm

    return prewarm(force=force)


@app.get("/api/flags/warmup/status")
def flags_warmup_status() -> dict:
    """Progress of the background info warm-up."""
    from app.quiz.country_info import warmup_status

    return warmup_status()


@app.get("/api/flags/{code}/asset")
def flag_asset(code: str) -> FileResponse:
    from app.data.assets import FlagAssetStore

    path = FlagAssetStore().path_for(code)
    if path is None:
        raise HTTPException(status_code=404, detail="flag asset not found")
    return FileResponse(path, media_type="image/png", headers=_asset_headers())


@app.get("/api/world/flags")
def world_flags(language: str = "it") -> dict:
    """Every flag of the world pool (Quiz Bandiere Mondo)."""
    from app.quiz.engine import WORLD_MANIFEST
    from app.core.config import get_settings as _get_settings

    engine = get_world_engine()
    manifest_path = _get_settings().baseline_dir / WORLD_MANIFEST
    continents: dict[str, str] = {}
    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        continents = {
            c.get("code"): (c.get("continent") or "") for c in manifest.get("countries", [])
        }
    except (OSError, json.JSONDecodeError):
        continents = {}
    payloads = {}
    for code in engine.codes():
        payload = engine._target_payload(code, language)
        payload["continent"] = continents.get(code, "")
        payloads[code] = payload
    return {"count": len(payloads), "flags": payloads}


@app.get("/api/world/flags/{code}/asset")
def world_flag_asset(code: str) -> FileResponse:
    from app.core.config import get_settings as _get_settings
    from app.data.assets import FlagAssetStore

    path = FlagAssetStore(
        cache_dir=_get_settings().cache_dir / "world-flags"
    ).path_for(code)
    if path is None:
        raise HTTPException(status_code=404, detail="flag asset not found")
    return FileResponse(path, media_type="image/png", headers=_asset_headers())


@app.get("/api/flags/{code}")
def flag_details(code: str, refresh: bool = False) -> dict:
    """Country info for the flag detail panel (Wikidata + Wikipedia).

    Live sources are best-effort: missing fields come back empty rather
    than failing the whole request. Pass ``refresh=true`` to re-query the
    live sources — the cached copy is kept when the online data is not
    newer than what is already stored.
    """
    from app.quiz.country_info import get_flag_details

    try:
        return get_flag_details(code, force=refresh, check_modified=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/quiz/config")
def quiz_config(language: str = "it") -> dict:
    """Quiz setup: supported languages and the 7 difficulty profiles."""
    from app.core.config import DIFFICULTY_LEVELS, get_settings

    settings = get_settings()
    return {
        "languages": ["it", "en"],
        "default_language": settings.default_language,
        "difficulty": [
            {
                "level": d.level,
                "questions": d.questions,
                "flag_to_name_weight": d.flag_to_name_weight,
                "min_similarity": d.min_similarity,
                "max_similarity": d.max_similarity,
                "color_questions_weight": d.color_questions_weight,
                "confederations": list(d.confederations),
            }
            for d in DIFFICULTY_LEVELS
        ],
    }


@app.get("/api/quiz/question")
def quiz_question(
    scope: str = "fifa",
    qtype: Optional[str] = None,
    difficulty: int = 4,
    language: str = "it",
    exclude: str = "",
) -> dict:
    engine = get_world_engine() if scope == "world" else get_engine()
    excluded = {c.strip().upper() for c in exclude.split(",") if c.strip()}
    try:
        return engine.question(
            qtype=qtype,
            difficulty=difficulty,
            language=language,
            exclude=excluded,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/quiz/answer")
def quiz_answer(payload: AnswerPayload) -> dict:
    engine = get_engine()
    question = payload.question
    if not isinstance(question, dict):
        raise HTTPException(status_code=422, detail="invalid question payload")
    options = question.get("options")
    correct_index = question.get("correct_index")
    if not isinstance(options, list) or not isinstance(correct_index, int):
        raise HTTPException(status_code=422, detail="invalid question payload")
    if not (0 <= payload.selected_index < len(options)):
        raise HTTPException(status_code=422, detail="selected_index out of range")
    return engine.answer(question, payload.selected_index)


@app.get("/api/quiz/similar/{code}")
def quiz_similar(code: str, n: int = 3, language: str = "it") -> dict:
    engine = get_engine()
    code = code.upper()
    if code not in engine.index:
        raise HTTPException(status_code=404, detail="unknown fifa code")
    similar_codes = engine.similar(code, n=n)
    return {
        "code": code,
        "name": engine.names_for(language).get(code, code),
        "similar": [
            {"code": c, "name": engine.names_for(language).get(c, c),
             "asset_url": engine._target_payload(c, language)["asset_url"]}
            for c in similar_codes
        ],
    }


@app.get("/api/leaderboard")
def leaderboard(limit: int = 3, scope: str = "fifa") -> dict:
    """Best scores per difficulty level (persistent, file-backed).

    ``scope`` selects the quiz: ``fifa`` or ``world`` (two separate boards).
    """
    from app.quiz.leaderboard import best_per_level

    cap = min(max(limit, 1), 10)
    # load the full top-10 once and derive both the visible rows and the total
    all_levels = best_per_level(limit=10, scope=scope)
    return {
        "levels": {k: v[:cap] for k, v in all_levels.items()},
        "total_scores": sum(len(v) for v in all_levels.values()),
    }


@app.post("/api/leaderboard")
def leaderboard_add(payload: ScorePayload) -> dict:
    """Record a finished game; returns the entry with its level rank."""
    from app.quiz.leaderboard import add_score

    try:
        return add_score(
            nickname=payload.nickname,
            difficulty=payload.difficulty,
            score=payload.score,
            total=payload.total,
            language=payload.language,
            flag=payload.flag,
            scope=payload.scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/leaderboard/flags")
def leaderboard_flags(limit: int = 10) -> dict:
    """The flags most often shown in served questions, most-viewed first."""
    from app.quiz.flag_views import top

    engine = get_engine()
    limit = min(max(limit, 1), 50)
    entries = top(limit=limit)
    for e in entries:
        payload = engine._target_payload(e["code"], "it")
        e["name"] = payload["name"]
        e["asset_url"] = payload["asset_url"]
    return {"count": len(entries), "flags": entries}


@app.get("/api/topscorers")
def topscorers(limit: int = 20, refresh: bool = False) -> dict:
    """All-time national-team top scorers (IFFHS), cached like the flags.

    Pass ``refresh=true`` to re-check the live page; the cache is rewritten
    only when the ranking actually changed.
    """
    from app.quiz import top_scorers

    try:
        payload = top_scorers.get_top_scorers(force=refresh, check_modified=refresh)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    limit = min(max(limit, 1), 50)
    payload["count"] = len(payload["scorers"])
    payload["scorers"] = payload["scorers"][:limit]
    return payload


@app.get("/api/topscorers/cache")
def topscorers_cache() -> dict:
    """Local disk usage of the top-scorers cache."""
    from app.quiz.top_scorers import cache_status

    return cache_status()


@app.post("/api/topscorers/cache/clear")
def topscorers_cache_clear() -> dict:
    """Delete the top-scorers cache instantly."""
    from app.quiz.top_scorers import clear_cache

    return clear_cache()


@app.post("/api/topscorers/warmup")
def topscorers_warmup(force: bool = False) -> dict:
    """Pre-fill the top-scorers cache in the background.

    ``force=true`` re-checks the live page, otherwise only stale/missing
    entries are refreshed (same rules as the flags cache).
    """
    from app.quiz.top_scorers import prewarm

    return prewarm(force=force)


@app.get("/api/topscorers/warmup/status")
def topscorers_warmup_status() -> dict:
    """Progress of the background top-scorers warm-up."""
    from app.quiz.top_scorers import warmup_status

    return warmup_status()


@app.get("/api/review/queue")
def review_queue() -> dict:
    from app.review import queue

    items = queue()
    return {"count": len(items), "items": items}


@app.get("/api/stats/summary")
def stats_summary() -> dict:
    from app.quiz.engine import QuizEngine

    engine = get_engine()
    index = engine.index
    from collections import Counter

    colors = Counter()
    for data in index.values():
        for c in data["color"].get("primary_colors", []):
            colors[c] += 1

    review_path = Path(settings.data_baseline_dir).parent / "review" / "queue.json"
    needs_review = 0
    if review_path.exists():
        import json

        needs_review = json.loads(review_path.read_text()).get("needs_review", 0)

    return {
        "total_flags": len(index),
        "needs_review": needs_review,
        "language": settings.default_language,
        "top_colors": dict(colors.most_common(8)),
    }


# ---- static frontend build (production) ------------------------------------
# Serves the built frontend (frontend/dist) so the whole app lives on one
# port. API routes above take precedence over this catch-all mount.
def _mount_frontend(app: FastAPI) -> None:
    from fastapi.staticfiles import StaticFiles

    from app.core.config import REPO_ROOT

    dist = REPO_ROOT / "frontend" / "dist"
    if not dist.is_dir():
        # no frontend build: keep the API info page at the root
        app.get("/")(_api_root)
        return
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


_mount_frontend(app)
