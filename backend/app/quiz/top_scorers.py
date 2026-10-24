"""All-time national-team top scorers, cached to disk.

Source: Wikipedia — "List of men's footballers with 50 or more
international goals" (https://en.wikipedia.org/wiki/...), a continuously
updated list (rev. as of today) that includes every World Cup played so far.
Data: player, nation, confederation, goals, caps, goals/match, career span.

Cache rules mirror the per-country info cache (``country_info``):

- one persistent JSON file stamped with ``fetched_at`` + the Wikipedia
  revision timestamp (``source_updated_at``);
- entries are fresh for ``CACHE_TTL``; a run that fails to fetch live data
  keeps ``live_ok=False`` so it is retried after ``CACHE_FAIL_TTL``;
- ``force=False`` only fetches when the cached copy is stale (no network
  traffic at all while warm);
- ``force=True`` re-fetches the page and only rewrites the file when the
  parsed content actually changed (checksum), keeping the cached copy
  otherwise — like the ``check_modified`` path for country info;
- a 24h background thread refreshes automatically.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
PAGE_TITLE = "List of men's footballers with 50 or more international goals"
SOURCE_URL = "https://en.wikipedia.org/wiki/" + PAGE_TITLE.replace(" ", "_")
SOURCE_NAME = "wikipedia-international-goals-50plus"

CACHE_TTL = 24 * 3600
CACHE_FAIL_TTL = 3600  # entries fetched without live data retried after 1h
HTTP_TIMEOUT = 20.0
USER_AGENT = "QuiZBandiere/1.0 (educational flag quiz; contact: local)"

DAILY_REFRESH_INTERVAL = 24 * 3600

#: Wikipedia nation names -> baseline name_en, for FIFA-code resolution.
_TEAM_ALIASES = {
    "czech republic": "Czechia",
    "ivory coast": "Côte d'Ivoire",
    "south korea": "Korea Republic",
    "turkey": "Türkiye",
    "united states": "USA",
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "iran": "IR Iran",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path() -> Path:
    return get_settings().cache_dir / "top_scorers.json"


def _read_cache() -> Optional[dict]:
    try:
        if _cache_path().exists():
            return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_cache(payload: dict) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:  # pragma: no cover - best effort
        logger.exception("could not write top-scorers cache")


def _checksum(scorers: list[dict]) -> str:
    raw = json.dumps(scorers, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Wikipedia page parsing
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    """Collects the text of every <tr> / <td> pair from a rendered table."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: Optional[list[str]] = None
        self._td = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:  # noqa: N802
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._td = True
            self._buf = []
        elif tag == "br" and self._td:
            self._buf.append(" | ")  # multi-team cells

    def handle_endtag(self, tag) -> None:  # noqa: N802
        if tag == "td" and self._td:
            self._row.append("".join(self._buf))
            self._td = False
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data) -> None:  # noqa: N802
        if self._td:
            self._buf.append(data)


def _first_scorers_table(html: str) -> str:
    """The by-player wikitable (rank, player, nation, goals, ...)."""
    idx = html.find("sortable plainrowheaders")
    if idx == -1:
        raise RuntimeError("Wikipedia scorers table not found")
    start = html.rfind("<table", 0, idx)
    end = html.find("</table>", idx)
    if start == -1 or end == -1:
        raise RuntimeError("Wikipedia scorers table not found")
    return html[start:end + len("</table>")]


def _clean_text(raw: str) -> str:
    return " ".join(unescape(raw).split())


def _clean_int(value: str) -> Optional[int]:
    value = re.sub(r"\s*\[[^\]]*\]", "", value).replace(",", "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:  # pragma: no cover - unexpected cell
        return None


def _parse_page(html: str) -> list[dict]:
    """Parse the all-time national-team scorers table from the rendered HTML."""
    table = _first_scorers_table(html)
    parser = _TableParser()
    parser.feed(table)

    scorers: list[dict] = []
    rank: Optional[int] = None
    for row in parser.rows:
        cells = [_clean_text(c) for c in row]
        if not cells or not cells[0]:
            continue
        if cells[0].isdigit():
            rank = int(cells[0])
            cells = cells[1:]
        # rows without a rank share the previous one (tied totals)
        if rank is None or len(cells) < 8:
            continue
        team = ", ".join(t.strip() for t in cells[1].split(" | ") if t.strip())
        scorers.append(
            {
                "rank": rank,
                "player": cells[0],
                "team": team,
                "confederation": cells[2],
                "goals": _clean_int(cells[3]),
                "caps": _clean_int(cells[4]),
                "ratio": cells[5],
                "years": cells[6],
                "date50": cells[7],
            }
        )
    if not scorers:
        raise RuntimeError("no scorers parsed from Wikipedia page")
    return scorers


def _fetch_page() -> tuple[list[dict], Optional[str]]:
    """GET the Wikipedia list and return (scorers, revision timestamp)."""
    params = {
        "action": "parse",
        "page": PAGE_TITLE,
        "prop": "text",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }
    with _client() as client:
        resp = client.get(WIKI_API, params=params)
        resp.raise_for_status()
        payload = resp.json()
        html = payload["parse"]["text"]
        rev = client.get(
            WIKI_API,
            params={
                "action": "query",
                "prop": "revisions",
                "rvprop": "timestamp",
                "rvlimit": "1",
                "titles": PAGE_TITLE,
                "format": "json",
                "formatversion": "2",
            },
        )
        rev.raise_for_status()
        pages = rev.json()["query"]["pages"]
        page = pages[0] if isinstance(pages, list) else list(pages.values())[0]
        updated = (page.get("revisions") or [{}])[0].get("timestamp")
    return _parse_page(html), updated


# ---------------------------------------------------------------------------
# FIFA-code resolution (team -> code, Italian name, flag)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _team_index() -> dict[str, str]:
    """Normalized baseline team name -> FIFA code."""
    manifest_path = get_settings().baseline_dir / "associations_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    index: dict[str, str] = {}
    for r in manifest.get("associations", []):
        code = r.get("fifa_code", "")
        for name in (r.get("name", ""), r.get("name_it", "")):
            key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
            if key:
                index[key] = code
    return index


def _normalize(team: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", team.lower())
    team = _TEAM_ALIASES.get(key, team)
    return re.sub(r"[^a-z0-9]", "", team.lower())


def _team_payload(team: str) -> dict:
    """Resolve a nation name to {team, code, name_it, asset_url}."""
    entry = {"team": team, "code": None, "name_it": None, "asset_url": None}
    if "," in team:
        return entry  # multiple nations (e.g. Puskás: Hungary, Spain)
    index = _team_index()
    code = index.get(_normalize(team))
    if not code:
        return entry
    entry["code"] = code
    dataset = _dataset()
    base = dataset.get(code, {})
    entry["name_it"] = base.get("name_it") or team
    entry["asset_url"] = f"/api/flags/{code}/asset"
    return entry


@lru_cache(maxsize=1)
def _dataset() -> dict[str, dict]:
    manifest_path = get_settings().baseline_dir / "associations_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for r in manifest.get("associations", []):
        code = r.get("fifa_code", "")
        if code:
            out[code] = {
                "name_en": r.get("name", code),
                "name_it": r.get("name_it", r.get("name", code)),
            }
    return out


def _enrich(scorers: list[dict]) -> list[dict]:
    enriched = []
    for entry in scorers:
        out = dict(entry)
        out.update(_team_payload(out["team"]))
        enriched.append(out)
    return enriched


# ---------------------------------------------------------------------------
# Public accessors (mirror country_info.get_flag_details)
# ---------------------------------------------------------------------------


def _fresh(iso: str, ttl: int) -> bool:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() < ttl


def get_top_scorers(
    force: bool = False,
    check_modified: bool = False,
) -> dict:
    """All-time national-team top scorers, cached with a TTL.

    - ``force=False`` returns the cached entry while fresh.
    - ``force=True``/``check_modified=True`` re-fetches Wikipedia and only
      rewrites the cache when the parsed content changed.
    - On network failure the last valid cache is returned; without a cache
      the error propagates.
    """
    cached = _read_cache()
    if check_modified and cached:
        try:
            scorers, updated = _fetch_page()
        except Exception:  # noqa: BLE001 - offline fallback below
            return _fallback(cached)
        if _checksum(scorers) == cached.get("checksum"):
            return dict(cached, scorers=_enrich(cached["scorers"]))
        return _commit(scorers, updated)
    if not force and cached:
        ttl = CACHE_TTL if cached.get("live_ok") else CACHE_FAIL_TTL
        if _fresh(cached.get("fetched_at", ""), ttl):
            return dict(cached, scorers=_enrich(cached["scorers"]))

    try:
        scorers, updated = _fetch_page()
    except Exception:  # noqa: BLE001 - keep serving the last valid cache
        logger.exception("Wikipedia fetch failed; using cached top scorers")
        return _fallback(cached)
    return _commit(scorers, updated)


def _fallback(cached: Optional[dict]) -> dict:
    if cached:
        return dict(cached, scorers=_enrich(cached.get("scorers", [])))
    raise RuntimeError("no cached top scorers and source unreachable")


def _commit(scorers: list[dict], updated: Optional[str]) -> dict:
    payload = {
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "source_updated_at": updated,
        "fetched_at": _now(),
        "live_ok": True,
        "checksum": _checksum(scorers),
        "scorers": scorers,
    }
    _write_cache(payload)
    return dict(payload, scorers=_enrich(scorers))


def cache_status() -> dict:
    """Local disk usage of the top-scorers cache."""
    try:
        size = _cache_path().stat().st_size if _cache_path().exists() else 0
    except OSError:  # pragma: no cover
        size = 0
    return {"entries": 1 if size else 0, "bytes": size}


def clear_cache() -> dict:
    """Delete the top-scorers cache instantly."""
    try:
        _cache_path().unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        pass
    with _state_lock:
        _state.update(
            running=False, force=False, total=0, done=0, errors=0,
            started_at=None, finished_at=None,
        )
    _team_index.cache_clear()
    _dataset.cache_clear()
    return {"removed": 1 if not _cache_path().exists() else 0}


# ---------------------------------------------------------------------------
# Background warm-up + daily refresh
# ---------------------------------------------------------------------------

_state: dict = {
    "running": False,
    "force": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()
_daily_thread: Optional[threading.Thread] = None


def warmup_status() -> dict:
    """Snapshot of the current (or last) warm-up run."""
    with _state_lock:
        return dict(_state)


def prewarm(force: bool = False) -> dict:
    """Pre-fill the cache in the background. No-op if already running.

    - ``force=False`` only fetches when the cache is stale or missing.
    - ``force=True`` re-checks the live page and rewrites only on change.
    """
    with _state_lock:
        if _state["running"]:
            return dict(_state)
        _state.update(
            running=True,
            force=force,
            total=1,
            done=0,
            errors=0,
            started_at=_now(),
            finished_at=None,
        )
    threading.Thread(target=_prewarm_worker, args=(force,), daemon=True).start()
    return warmup_status()


def _prewarm_worker(force: bool) -> None:
    try:
        get_top_scorers(force=force, check_modified=force)
    except Exception:  # noqa: BLE001 - record and move on
        logger.exception("top-scorers warm-up failed")
        with _state_lock:
            _state["errors"] += 1
    finally:
        with _state_lock:
            _state["done"] += 1
            _state["running"] = False
            _state["finished_at"] = _now()


def _daily_loop() -> None:
    while True:
        time.sleep(DAILY_REFRESH_INTERVAL)
        try:
            prewarm(force=True)
        except Exception:  # noqa: BLE001
            logger.exception("daily top-scorers refresh failed")


def _start_daily_thread() -> None:
    global _daily_thread
    if _daily_thread is None:
        _daily_thread = threading.Thread(target=_daily_loop, daemon=True)
        _daily_thread.start()


_start_daily_thread()
