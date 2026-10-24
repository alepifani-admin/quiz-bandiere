"""Live country info for the flag detail panel.

Merges data from:
- Wikidata / Wikipedia  (official name, official & other languages, capital,
  government form, current head of state, proclamation date, demonym,
  continent, area, current population, borders)
- a curated football table (World Cup appearances / trophies / best results)

Every country is cached to disk (per-code, with a TTL) so repeated calls
never hammer the external APIs and the app keeps working offline for the
cached portion.  Failures are never fatal: the endpoint returns the data
that could be collected and leaves missing fields empty.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CACHE_TTL = 24 * 3600
CACHE_FAIL_TTL = 3600  # entries fetched without live data retried after 1h
HTTP_TIMEOUT = 15.0
USER_AGENT = "QuiZBandiere/1.0 (educational flag quiz; contact: local)"

WIKIPEDIA_IT_API = "https://it.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_QUERY_URL = "https://query.wikidata.org/sparql"

CONTINENTS_IT = {
    "Asia": "Asia",
    "Europe": "Europa",
    "Africa": "Africa",
    "Oceania": "Oceania",
    "North America": "America del Nord",
    "South America": "America del Sud",
}

#: Curated FIFA World Cup record per national team (through 2022).
#: Keys are FIFA codes; absent codes default to "never qualified".
FOOTBALL: dict[str, dict] = {
    "ALG": {"wc": 5, "trophies": 0, "best": "Ottavi di finale (2014)"},
    "ARG": {"wc": 19, "trophies": 3, "best": "Vincitrice (1978, 1986, 2022)"},
    "AUS": {"wc": 7, "trophies": 0, "best": "Ottavi di finale (2006, 2022)"},
    "AUT": {"wc": 8, "trophies": 0, "best": "Terzo posto (1954)"},
    "BEL": {"wc": 15, "trophies": 0, "best": "Terzo posto (2018)"},
    "BIH": {"wc": 2, "trophies": 0, "best": "Sedicesimi di finale (2026)"},
    "BOL": {"wc": 3, "trophies": 0, "best": "Fase a gironi"},
    "BRA": {"wc": 23, "trophies": 5, "best": "Vincitrice (1958, 1962, 1970, 1994, 2002)"},
    "BUL": {"wc": 7, "trophies": 0, "best": "Quarto posto (1994)"},
    "CAN": {"wc": 3, "trophies": 0, "best": "Ottavi di finale (2026)"},
    "CHI": {"wc": 9, "trophies": 0, "best": "Terzo posto (1962)"},
    "CHN": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2002)"},
    "CIV": {"wc": 4, "trophies": 0, "best": "Sedicesimi di finale (2026)"},
    "CMR": {"wc": 8, "trophies": 0, "best": "Quarti di finale (1990)"},
    "COD": {"wc": 2, "trophies": 0, "best": "Sedicesimi di finale (2026)"},
    "COL": {"wc": 7, "trophies": 0, "best": "Quarti di finale (2014)"},
    "CPV": {"wc": 1, "trophies": 0, "best": "Sedicesimi di finale (2026)"},
    "CRC": {"wc": 6, "trophies": 0, "best": "Quarti di finale (2014)"},
    "CRO": {"wc": 7, "trophies": 0, "best": "Finalista (2018)"},
    "CUW": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2026)"},
    "CUB": {"wc": 1, "trophies": 0, "best": "Quarti di finale (1938)"},
    "CZE": {"wc": 10, "trophies": 0, "best": "Finalista (1934, 1962)"},
    "DEN": {"wc": 6, "trophies": 0, "best": "Quarti di finale (1998)"},
    "ECU": {"wc": 5, "trophies": 0, "best": "Ottavi di finale (2006)"},
    "EGY": {"wc": 4, "trophies": 0, "best": "Ottavi di finale (2026)"},
    "ENG": {"wc": 17, "trophies": 1, "best": "Vincitrice (1966)"},
    "ESP": {"wc": 17, "trophies": 2, "best": "Vincitrice (2010, 2026)"},
    "FRA": {"wc": 17, "trophies": 2, "best": "Vincitrice (1998, 2018)"},
    "GER": {"wc": 21, "trophies": 4, "best": "Vincitrice (1954, 1974, 1990, 2014)"},
    "GHA": {"wc": 5, "trophies": 0, "best": "Quarti di finale (2010)"},
    "GRE": {"wc": 3, "trophies": 0, "best": "Ottavi di finale (2014)"},
    "HAI": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2026)"},
    "HON": {"wc": 3, "trophies": 0, "best": "Fase a gironi"},
    "HUN": {"wc": 9, "trophies": 0, "best": "Finalista (1938, 1954)"},
    "IDN": {"wc": 1, "trophies": 0, "best": "Ottavi di finale (1938)"},
    "IRL": {"wc": 3, "trophies": 0, "best": "Quarti di finale (1990)"},
    "IRN": {"wc": 7, "trophies": 0, "best": "Fase a gironi"},
    "IRQ": {"wc": 2, "trophies": 0, "best": "Fase a gironi (1986)"},
    "ISL": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2018)"},
    "ISR": {"wc": 1, "trophies": 0, "best": "Fase a gironi (1970)"},
    "ITA": {"wc": 18, "trophies": 4, "best": "Vincitrice (1934, 1938, 1982, 2006)"},
    "JAM": {"wc": 1, "trophies": 0, "best": "Fase a gironi (1998)"},
    "JOR": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2026)"},
    "JPN": {"wc": 8, "trophies": 0, "best": "Ottavi di finale (2002, 2010, 2018, 2022)"},
    "KOR": {"wc": 12, "trophies": 0, "best": "Quarto posto (2002)"},
    "KSA": {"wc": 7, "trophies": 0, "best": "Ottavi di finale (1994)"},
    "KUW": {"wc": 1, "trophies": 0, "best": "Fase a gironi (1982)"},
    "MAR": {"wc": 7, "trophies": 0, "best": "Quarto posto (2022)"},
    "MEX": {"wc": 18, "trophies": 0, "best": "Quarti di finale (1970, 1986)"},
    "NED": {"wc": 12, "trophies": 0, "best": "Finalista (1974, 1978, 2010)"},
    "NGA": {"wc": 6, "trophies": 0, "best": "Ottavi di finale (1994, 1998, 2014)"},
    "NIR": {"wc": 3, "trophies": 0, "best": "Quarti di finale (1958)"},
    "NOR": {"wc": 4, "trophies": 0, "best": "Quarti di finale (2026)"},
    "NZL": {"wc": 3, "trophies": 0, "best": "Fase a gironi"},
    "PAN": {"wc": 2, "trophies": 0, "best": "Fase a gironi (2018)"},
    "PAR": {"wc": 9, "trophies": 0, "best": "Quarti di finale (2010)"},
    "PER": {"wc": 5, "trophies": 0, "best": "Quarti di finale (1970)"},
    "POL": {"wc": 9, "trophies": 0, "best": "Terzo posto (1974, 1982)"},
    "POR": {"wc": 9, "trophies": 0, "best": "Terzo posto (1966)"},
    "PRK": {"wc": 2, "trophies": 0, "best": "Quarti di finale (1966)"},
    "QAT": {"wc": 2, "trophies": 0, "best": "Fase a gironi (2022)"},
    "ROU": {"wc": 7, "trophies": 0, "best": "Quarti di finale (1994)"},
    "RSA": {"wc": 4, "trophies": 0, "best": "Sedicesimi di finale (2026)"},
    "RUS": {"wc": 11, "trophies": 0, "best": "Quarto posto (1966)"},
    "SCO": {"wc": 9, "trophies": 0, "best": "Fase a gironi"},
    "SEN": {"wc": 4, "trophies": 0, "best": "Quarti di finale (2002)"},
    "SLV": {"wc": 2, "trophies": 0, "best": "Fase a gironi"},
    "SRB": {"wc": 13, "trophies": 0, "best": "Quarto posto (1930, 1962)"},
    "SUI": {"wc": 13, "trophies": 0, "best": "Quarti di finale (1934, 1938, 1954)"},
    "SVK": {"wc": 1, "trophies": 0, "best": "Ottavi di finale (2010)"},
    "SVN": {"wc": 2, "trophies": 0, "best": "Fase a gironi"},
    "SWE": {"wc": 12, "trophies": 0, "best": "Finalista (1958)"},
    "TOG": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2006)"},
    "TRI": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2006)"},
    "TUN": {"wc": 7, "trophies": 0, "best": "Fase a gironi"},
    "TUR": {"wc": 3, "trophies": 0, "best": "Terzo posto (2002)"},
    "UAE": {"wc": 1, "trophies": 0, "best": "Fase a gironi (1990)"},
    "UKR": {"wc": 1, "trophies": 0, "best": "Quarti di finale (2006)"},
    "URU": {"wc": 14, "trophies": 2, "best": "Vincitrice (1930, 1950)"},
    "USA": {"wc": 12, "trophies": 0, "best": "Terzo posto (1930)"},
    "UZB": {"wc": 1, "trophies": 0, "best": "Fase a gironi (2026)"},
    "WAL": {"wc": 2, "trophies": 0, "best": "Quarti di finale (1958)"},
}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_dir() -> Path:
    return get_settings().cache_dir / "country_info"


def _read_json(path: Path) -> Optional[dict]:
    """Read a cached JSON object, returning None if missing/corrupt."""
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


_client_lock = threading.Lock()
_client_instance: Optional[httpx.Client] = None


def _client() -> httpx.Client:
    """Shared HTTP client: reuses connections across the many Wikidata calls
    (a cold warm-up issues >1000 requests, each previously doing a new
    TCP/TLS handshake). httpx.Client is thread-safe."""
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = httpx.Client(
                    timeout=HTTP_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_connections=16, max_keepalive_connections=8
                    ),
                )
    return _client_instance


#: after a real connection failure, skip network calls for a short while so
#: an offline machine returns cached data instantly instead of waiting for
#: retries/timeouts on every flag the user opens.
OFFLINE_COOLDOWN_SECONDS = 30.0
_offline_until = 0.0
_offline_lock = threading.Lock()


def _trip_offline() -> None:
    global _offline_until
    with _offline_lock:
        _offline_until = time.monotonic() + OFFLINE_COOLDOWN_SECONDS


def _reset_offline() -> None:
    global _offline_until
    with _offline_lock:
        _offline_until = 0.0


def _http_json(url: str, params: Optional[dict] = None, *, attempts: int = 3) -> dict:
    """GET a JSON object from an external API, retrying transient errors."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if time.monotonic() < _offline_until:
        raise RuntimeError("network unavailable (offline cooldown)")
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = _client().get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            last_error = exc
            if status and status != 429 and status < 500:
                raise  # a plain 4xx won't improve with retries
        except (httpx.TransportError, ValueError) as exc:
            # connection/timeout errors and non-JSON payloads are transient
            last_error = exc
        else:
            _reset_offline()
            return data
        if attempt + 1 < attempts:
            time.sleep(0.5 * (2 ** attempt))
    if last_error is None:  # pragma: no cover - defensive
        raise RuntimeError("request failed without an error")
    if isinstance(last_error, httpx.TransportError):
        # all attempts failed on connectivity: skip the next calls briefly so
        # offline flag details return from cache instantly
        _trip_offline()
    raise last_error


# ---------------------------------------------------------------------------
# Local dataset helpers
# ---------------------------------------------------------------------------


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
                "confederation": r.get("confederation", ""),
                "continent": r.get("continent", ""),
            }
    return out


@lru_cache(maxsize=1)
def _names_it() -> dict[str, str]:
    path = get_settings().baseline_dir / "name_it.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _codes_by_normalized_name() -> dict[str, str]:
    dataset = _dataset()
    names_it = _names_it()
    out: dict[str, str] = {}
    for code, info in dataset.items():
        for name in (info.get("name_it"), info.get("name_en"), names_it.get(code)):
            key = _normalize(name)
            if key:
                out.setdefault(key, code)
    return out


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


# ---------------------------------------------------------------------------
# Wikidata
# ---------------------------------------------------------------------------

_QID_CACHE_TTL = 30 * 24 * 3600
WIKIPEDIA_BATCH = 50

# Italian titles that resolve to a wrong Wikidata entity (disambiguation
# pages, saints, historical regions...). Map the dataset name -> correct QID.
QID_OVERRIDES = {
    "Malesia": "Q833",          # disambiguation page -> Malaysia
    "Santa Lucia": "Q760",      # Saint Lucy (saint) -> Saint Lucia
    "Taipei Cinese": "Q865",    # FIFA entity -> Taiwan
    "Palestina": "Q219060",     # historical region -> State of Palestine
    "Montserrat": "Q13353",     # disambiguation -> Montserrat (territory)
    "Anguilla": "Q25228",       # disambiguation -> Anguilla (territory)
    "Congo": "Q971",            # disambiguation -> Republic of the Congo
}

# City-states / territories without a P36 capital statement in Wikidata.
CAPITAL_OVERRIDES = {
    "HKG": "Hong Kong",
    "MAC": "Macao",
}

# Wikidata has no P37 (official language) claim for these.
LANGUAGE_OVERRIDES = {
    "URU": ["spagnolo"],
    "TAH": ["francese"],
}

# Wikidata has no P47 (shares border with) claim for these.
BORDERS_OVERRIDES = {
    "HON": ["GUA", "NCA", "SLV"],  # Honduras: Guatemala, Nicaragua, El Salvador
}
_qid_cache: Optional[dict] = None
_qid_lock = threading.RLock()


def _qid_cache_path() -> Path:
    return _cache_dir() / "qids.json"


def _load_qid_cache() -> dict:
    try:
        data = json.loads(_qid_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and _fresh(data.get("fetched_at", ""), _QID_CACHE_TTL):
            qids = data.get("qids")
            return qids if isinstance(qids, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {}


def _save_qid_cache(qids: dict) -> None:
    try:
        path = _qid_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _qid_lock:
            snapshot = dict(qids)
        tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps({"fetched_at": _now(), "qids": snapshot}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def _qids() -> dict:
    """In-memory QID map (name_it -> Wikidata item id), loaded lazily."""
    global _qid_cache
    with _qid_lock:
        if _qid_cache is None:
            _qid_cache = _load_qid_cache()
        return _qid_cache


def _resolve_qid_batch(names: list[str]) -> dict:
    """Resolve many Italian names to Wikidata QIDs in a few API calls.

    Wikipedia accepts up to 50 pipe-separated titles per request, so the
    whole dataset resolves in ~5 calls instead of 211 (avoiding rate limits).
    """
    results: dict[str, Optional[str]] = {}
    for start in range(0, len(names), WIKIPEDIA_BATCH):
        chunk = names[start : start + WIKIPEDIA_BATCH]
        try:
            params = {
                "action": "query", "format": "json", "prop": "pageprops",
                "ppprop": "wikibase_item", "redirects": "1",
                "titles": "|".join(chunk),
            }
            payload = _http_json(WIKIPEDIA_IT_API, params)
            title_to_qid = {}
            for page in payload.get("query", {}).get("pages", {}).values():
                qid = (page.get("pageprops") or {}).get("wikibase_item")
                title = page.get("title")
                if qid and title:
                    title_to_qid[_normalize(title)] = qid
            query = payload.get("query", {})
            for item in query.get("redirects", []) + query.get("normalized", []):
                frm = item.get("from")
                to = item.get("to")
                if frm and to and _normalize(to) in title_to_qid:
                    title_to_qid[_normalize(frm)] = title_to_qid[_normalize(to)]
            for name in chunk:
                results[name] = QID_OVERRIDES.get(name) or title_to_qid.get(_normalize(name))
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning("batch QID lookup failed: %s", exc)
            for name in chunk:
                results[name] = None
    return results


def _lookup_qid_single(it_name: str) -> Optional[str]:
    """Resolve a single Italian name via the Wikipedia API."""
    params = {
        "action": "query", "format": "json", "prop": "pageprops",
        "ppprop": "wikibase_item", "redirects": "1", "titles": it_name,
    }
    payload = _http_json(WIKIPEDIA_IT_API, params)
    for page in payload.get("query", {}).get("pages", {}).values():
        item = (page.get("pageprops") or {}).get("wikibase_item")
        if item:
            return item
    return None


def _wikidata_qid(it_name: str) -> Optional[str]:
    """Resolve the Wikidata item id, using the on-disk QID cache first."""
    key = (it_name or "").strip()
    if not key:
        return None
    override = QID_OVERRIDES.get(key)
    if override:
        return override
    qids = _qids()
    with _qid_lock:
        if key in qids:
            return qids[key] or None
    try:
        qid = _lookup_qid_single(key)
    except Exception as exc:  # noqa: BLE001 - live sources are best-effort
        logger.warning("Wikidata QID lookup failed for %s: %s", it_name, exc)
        qid = None
    with _qid_lock:
        qids[key] = qid
    _save_qid_cache(qids)
    return qid


def _entity_modified(qid: str) -> Optional[str]:
    """Last-modified timestamp of a Wikidata entity (ISO 8601)."""
    try:
        payload = _http_json(
            WIKIDATA_API,
            {"action": "wbgetentities", "format": "json", "props": "info", "ids": qid},
        )
        return (payload.get("entities", {}).get(qid, {}) or {}).get("modified")
    except Exception as exc:  # noqa: BLE001 - best-effort live source
        logger.warning("Wikidata modified check failed for %s: %s", qid, exc)
        return None


def _fetch_modified_dates(qids: list[str]) -> dict[str, str]:
    """Batched ``modified`` timestamps for many entities (50 ids per call)."""
    dates: dict[str, str] = {}
    for start in range(0, len(qids), 50):
        chunk = [q for q in qids[start : start + 50] if q]
        if not chunk:
            continue
        try:
            payload = _http_json(
                WIKIDATA_API,
                {"action": "wbgetentities", "format": "json", "props": "info",
                 "ids": "|".join(chunk)},
            )
            for eid, ent in payload.get("entities", {}).items():
                if ent.get("modified"):
                    dates[eid] = ent["modified"]
        except Exception as exc:  # noqa: BLE001 - best-effort live source
            logger.warning("Wikidata modified batch failed: %s", exc)
    return dates


_BASIC_QUERY = """
SELECT DISTINCT ?official_it ?official_en ?capital_it ?capital_en
 ?gov_it ?gov_en ?pres_it ?pres_en ?procl ?demon_it ?demon_en
 ?cont_it ?cont_en ?area WHERE {
  BIND(wd:QID AS ?c)
  OPTIONAL { ?c wdt:P1448 ?official_it. FILTER(LANG(?official_it)="it") }
  OPTIONAL { ?c wdt:P1448 ?official_en. FILTER(LANG(?official_en)="en") }
  OPTIONAL { ?c wdt:P36 ?capital.
    OPTIONAL { ?capital rdfs:label ?capital_it. FILTER(LANG(?capital_it)="it") }
    OPTIONAL { ?capital rdfs:label ?capital_en. FILTER(LANG(?capital_en)="en") } }
  OPTIONAL { ?c wdt:P122 ?gov.
    OPTIONAL { ?gov rdfs:label ?gov_it. FILTER(LANG(?gov_it)="it") }
    OPTIONAL { ?gov rdfs:label ?gov_en. FILTER(LANG(?gov_en)="en") } }
  OPTIONAL { ?c wdt:P35 ?head.
    OPTIONAL { ?head wdt:P1308 ?pres. }
    BIND(IF(BOUND(?pres), ?pres, ?head) AS ?holder)
    OPTIONAL { ?holder rdfs:label ?pres_it. FILTER(LANG(?pres_it)="it") }
    OPTIONAL { ?holder rdfs:label ?pres_en. FILTER(LANG(?pres_en)="en") } }
  OPTIONAL { ?c wdt:P571 ?procl. }
  OPTIONAL { ?c wdt:P1549 ?demon.
    OPTIONAL { ?demon rdfs:label ?demon_it. FILTER(LANG(?demon_it)="it") }
    OPTIONAL { ?demon rdfs:label ?demon_en. FILTER(LANG(?demon_en)="en") } }
  OPTIONAL { ?c wdt:P30 ?cont.
    OPTIONAL { ?cont rdfs:label ?cont_it. FILTER(LANG(?cont_it)="it") }
    OPTIONAL { ?cont rdfs:label ?cont_en. FILTER(LANG(?cont_en)="en") } }
  OPTIONAL { ?c wdt:P2046 ?area. }
}
"""

_OFFICIAL_LANGS_QUERY = """
SELECT DISTINCT ?lbl WHERE {
  BIND(wd:QID AS ?c)
  ?c wdt:P37 ?ol.
  OPTIONAL { ?ol rdfs:label ?ol_it. FILTER(LANG(?ol_it)="it") }
  OPTIONAL { ?ol rdfs:label ?ol_en. FILTER(LANG(?ol_en)="en") }
  BIND(COALESCE(?ol_it, ?ol_en) AS ?lbl)
}
"""

_OTHER_LANGS_QUERY = """
SELECT DISTINCT ?lbl WHERE {
  BIND(wd:QID AS ?c)
  ?c wdt:P2936 ?osp.
  OPTIONAL { ?osp rdfs:label ?osp_it. FILTER(LANG(?osp_it)="it") }
  OPTIONAL { ?osp rdfs:label ?osp_en. FILTER(LANG(?osp_en)="en") }
  BIND(COALESCE(?osp_it, ?osp_en) AS ?lbl)
}
"""

_POPULATION_QUERY = """
SELECT ?pop WHERE {
  BIND(wd:QID AS ?c)
  ?c p:P1082 ?s.
  ?s ps:P1082 ?pop.
  ?s pq:P585 ?date.
  FILTER NOT EXISTS { ?s wikibase:rank ?r. FILTER(?r = wikibase:DeprecatedRank) }
} ORDER BY DESC(?date) LIMIT 1
"""

_POPULATION_FALLBACK_QUERY = """
SELECT ?pop WHERE {
  BIND(wd:QID AS ?c)
  ?c wdt:P1082 ?pop.
} ORDER BY DESC(?pop) LIMIT 1
"""

_BORDERS_QUERY = """
SELECT DISTINCT ?neighbor ?nb_it ?nb_en WHERE {
  BIND(wd:QID AS ?c)
  ?c wdt:P47 ?neighbor.
  OPTIONAL { ?neighbor rdfs:label ?nb_it. FILTER(LANG(?nb_it)="it") }
  OPTIONAL { ?neighbor rdfs:label ?nb_en. FILTER(LANG(?nb_en)="en") }
}
"""


def _sparql(qid: str, query: str) -> list[dict]:
    payload = _http_json(
        WIKIDATA_QUERY_URL,
        {"query": query.replace("QID", qid), "format": "json"},
    )
    return payload.get("results", {}).get("bindings", [])


def _safe_result(future) -> Optional[list]:
    """Future.result() that degrades to None instead of raising."""
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001 - best-effort live sources
        logger.warning("Wikidata sub-query failed: %s", exc)
        return None


def _pick_label(it_values, en_values) -> Optional[str]:
    val = _pick(it_values, "it")
    if val:
        return val
    return _pick(en_values, "en")


def _pick(values: list, lang: str) -> Optional[str]:
    """Pick the first non-empty value (labels already filtered by language)."""
    for v in values:
        if isinstance(v, str) and (v or "").strip():
            return v
    return None


def _collect(rows: list[dict], var: str) -> list[str]:
    values = []
    for row in rows:
        binding = row.get(var)
        if binding and binding.get("value"):
            values.append(binding["value"])
    return values


def _wikidata_fields(code: str, it_name: str) -> dict:
    """Wikidata-backed fields for a country. Never raises."""
    try:
        qid = _wikidata_qid(it_name)
        if not qid:
            return {}
        # The queries are independent once the QID is known: run them in
        # parallel so a cold cache loads ~5x faster. Each query is small
        # enough to stay under the server's response-size limit.
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_basic = pool.submit(_sparql, qid, _BASIC_QUERY)
            f_ol = pool.submit(_sparql, qid, _OFFICIAL_LANGS_QUERY)
            f_osp = pool.submit(_sparql, qid, _OTHER_LANGS_QUERY)
            f_pop = pool.submit(_query_population, qid)
            f_borders = pool.submit(_query_borders, qid, code)
            rows = _safe_result(f_basic)
            if not rows:
                return {}
            official_langs = sorted(
                set(_collect(_safe_result(f_ol) or [], "lbl"))
                | set(LANGUAGE_OVERRIDES.get(code, []))
            )
            spoken = {v for v in _collect(_safe_result(f_osp) or [], "lbl") if v}
            other_langs = sorted(spoken - set(official_langs))
            population = _safe_result(f_pop)
            borders = _safe_result(f_borders)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wikidata query failed for %s: %s", code, exc)
        return {}

    official_it = sorted(set(_collect(rows, "official_it")))
    official_en = sorted(set(_collect(rows, "official_en")))
    capital_it, capital_en = _collect(rows, "capital_it"), _collect(rows, "capital_en")
    gov_it, gov_en = _collect(rows, "gov_it"), _collect(rows, "gov_en")
    pres_it, pres_en = _collect(rows, "pres_it"), _collect(rows, "pres_en")
    procl = _collect(rows, "procl")
    demon_it, demon_en = _collect(rows, "demon_it"), _collect(rows, "demon_en")
    cont_it, cont_en = _collect(rows, "cont_it"), _collect(rows, "cont_en")
    area = _collect(rows, "area")

    procl_value = procl[0][:10] if procl else None
    if procl_value and procl_value.endswith("-00-00"):
        procl_value = procl_value[:4]

    capital = _pick_label(capital_it, capital_en) or CAPITAL_OVERRIDES.get(code)
    return {
        "official_name": _pick_label(official_it, official_en),
        "capital": capital,
        "official_languages": official_langs,
        "other_languages": other_langs,
        "government_form": _pick_label(gov_it, gov_en),
        "president": _pick_label(pres_it, pres_en),
        "proclamation": procl_value,
        "demonym": _pick_label(demon_it, demon_en),
        "continent": _pick_label(cont_it, cont_en),
        "area_km2": float(area[0]) if area and area[0].replace(".", "").isdigit() else None,
        "population": population,
        "borders": borders,
    }


def _query_population(qid: str) -> Optional[int]:
    rows = _sparql(qid, _POPULATION_QUERY)
    if not rows:
        rows = _sparql(qid, _POPULATION_FALLBACK_QUERY)
    if not rows:
        return None
    try:
        return int(float(_collect(rows, "pop")[0]))
    except (ValueError, IndexError, OverflowError):
        return None


def _query_borders(qid: str, code: str = "") -> list[dict]:
    """Neighbouring countries mapped back to local FIFA codes by name."""
    rows = _sparql(qid, _BORDERS_QUERY)
    codes_by_name = _codes_by_normalized_name()
    names_it = _names_it()
    dataset = _dataset()

    seen: dict[str, dict] = {}
    for row in rows:
        nb_it = (row.get("nb_it") or {}).get("value")
        nb_en = (row.get("nb_en") or {}).get("value")
        nb_code = None
        for label in (nb_it, nb_en):
            if not label:
                continue
            nb_code = codes_by_name.get(_normalize(label))
            if nb_code:
                break
        if not nb_code:
            continue
        name = names_it.get(nb_code) or dataset.get(nb_code, {}).get("name_it", nb_code)
        seen[nb_code] = {
            "code": nb_code,
            "name": name,
            "asset_url": f"/api/flags/{nb_code}/asset",
        }
    for nb_code in BORDERS_OVERRIDES.get(code, []):
        name = names_it.get(nb_code) or dataset.get(nb_code, {}).get("name_it", nb_code)
        seen[nb_code] = {
            "code": nb_code,
            "name": name,
            "asset_url": f"/api/flags/{nb_code}/asset",
        }
    return sorted(seen.values(), key=lambda b: b["name"])


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_refreshing: set[str] = set()
_refreshing_lock = threading.Lock()


def _spawn_refresh(code: str) -> None:
    """Revalidate one stale entry in the background (single-flight per code)."""
    with _refreshing_lock:
        if code in _refreshing:
            return
        _refreshing.add(code)

    def _run() -> None:
        try:
            get_flag_details(code, force=True)
        except Exception:  # noqa: BLE001 - best-effort background refresh
            logger.debug("background refresh failed for %s", code)
        finally:
            with _refreshing_lock:
                _refreshing.discard(code)

    threading.Thread(target=_run, daemon=True, name=f"flag-refresh-{code}").start()


def get_flag_details(
    code: str,
    force: bool = False,
    check_modified: bool = False,
    source_updated_at: Optional[str] = None,
) -> dict:
    """Full detail payload for a FIFA code, cached per country.

    - ``force=False`` returns fresh cached entries; stale ones are re-fetched.
    - ``force=True`` re-queries the live sources (manual refresh).
    - ``check_modified=True`` keeps the cached entry when the online data is
      not newer than the cached ``source_updated_at`` (no unnecessary writes).
    - ``source_updated_at`` lets the caller stamp the entity's last-modified
      date observed elsewhere, avoiding an extra API call.
    """
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError("unknown flag code")
    path = _cache_dir() / f"{code}.json"
    cached = _read_json(path)
    modified_tried = False

    if check_modified and cached and cached.get("source_updated_at"):
        modified_tried = True
        dataset = _dataset()
        base = dataset.get(code) or {}
        name = base.get("name_it") or base.get("name_en")
        qid = _qids().get(name)
        if qid:
            online = _entity_modified(qid)
            if online and online <= cached["source_updated_at"]:
                return cached
            source_updated_at = source_updated_at or online

    if not force and cached:
        live_ok = bool(cached.get("live_ok"))
        ttl = CACHE_TTL if live_ok else CACHE_FAIL_TTL
        if _fresh(cached.get("fetched_at", ""), ttl):
            return cached
        # Stale but present: serve it right away and revalidate in the
        # background, so opening a flag detail never waits on the network.
        _spawn_refresh(code)
        return cached

    dataset = _dataset()
    base = dataset.get(code)
    if not base:
        raise ValueError("unknown flag code")

    wiki = _wikidata_fields(code, base.get("name_it", "") or base.get("name_en", ""))
    live_ok = bool(wiki)
    football = dict(
        FOOTBALL.get(code, {"wc": 0, "trophies": 0, "best": "Mai qualificata"})
    )

    continent = CONTINENTS_IT.get(wiki.get("continent"), wiki.get("continent"))
    if not continent:
        continent = CONTINENTS_IT.get(base.get("continent"), base.get("continent"))

    area = wiki.get("area_km2")
    population = wiki.get("population")
    density = (
        round(population / area, 1)
        if area and population and area > 0
        else None
    )

    borders = wiki.get("borders")
    if borders is None:
        borders = (cached.get("details") or {}).get("borders") or [] if cached else []

    details = {
        "full_name": base.get("name_it") or base.get("name_en"),
        "official_name": wiki.get("official_name"),
        "capital": wiki.get("capital"),
        "official_languages": wiki.get("official_languages") or [],
        "other_languages": wiki.get("other_languages") or [],
        "government_form": wiki.get("government_form"),
        "president": wiki.get("president"),
        "proclamation": wiki.get("proclamation"),
        "area_km2": area,
        "population": population,
        "density": density,
        "demonym": wiki.get("demonym"),
        "continent": continent,
        "borders": borders,
        "football": football,
    }

    # A live refresh that failed must never wipe a previously good cache:
    # keep the fields we already had and retry later (live_ok stays False, so
    # the short fail-TTL applies).
    if not live_ok and cached:
        prev = cached.get("details") or {}
        for key, value in details.items():
            if value in (None, [], {}) and prev.get(key) not in (None, [], {}):
                details[key] = prev[key]

    # Recompute density when area/population were recovered from the cache.
    if details.get("density") is None and details.get("area_km2") and details.get("population"):
        area_km2 = details["area_km2"]
        if area_km2 > 0:
            details["density"] = round(details["population"] / area_km2, 1)

    # Curated fallbacks that need no network round-trip.
    if not details.get("capital"):
        details["capital"] = CAPITAL_OVERRIDES.get(code)
    if not details.get("official_languages"):
        details["official_languages"] = list(LANGUAGE_OVERRIDES.get(code, []))
    if not details.get("borders"):
        names_it = _names_it()
        base_names = _dataset()
        details["borders"] = [
            {
                "code": nb,
                "name": names_it.get(nb) or base_names.get(nb, {}).get("name_it", nb),
                "asset_url": f"/api/flags/{nb}/asset",
            }
            for nb in BORDERS_OVERRIDES.get(code, [])
        ]

    payload = {
        "code": code,
        "name": base.get("name_it") or base.get("name_en"),
        "name_en": base.get("name_en"),
        "confederation": base.get("confederation"),
        "asset_url": f"/api/flags/{code}/asset",
        "details": details,
        "live_ok": live_ok,
        "fetched_at": _now(),
    }

    if source_updated_at is None and not live_ok:
        source_updated_at = (cached or {}).get("source_updated_at")
    if source_updated_at is None and force and not modified_tried and live_ok:
        qid = _wikidata_qid(payload["name"] or "")
        if qid:
            source_updated_at = _entity_modified(qid)
    if source_updated_at:
        payload["source_updated_at"] = source_updated_at

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return payload


def _fresh(iso: str, ttl: int) -> bool:
    if not isinstance(iso, str) or not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return (datetime.now(timezone.utc) - dt).total_seconds() < ttl
    except (OverflowError, TypeError):  # pragma: no cover - corrupt timestamp
        return False


def cache_status() -> dict:
    """Local disk usage of the country-info cache."""
    cache_dir = _cache_dir()
    entries = 0
    bytes_total = 0
    try:
        for p in cache_dir.glob("*.json"):
            if p.name == "qids.json":
                continue
            entries += 1
            bytes_total += p.stat().st_size
    except OSError:
        pass
    return {"entries": entries, "bytes": bytes_total}


def clear_cache() -> dict:
    """Delete every cached country entry and the QID map instantly."""
    global _qid_cache
    removed = 0
    cache_dir = _cache_dir()
    try:
        for p in cache_dir.glob("*.json"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    except OSError:
        pass
    with _state_lock:
        _state.update(
            running=False, force=False, total=0, done=0, errors=0,
            started_at=None, finished_at=None,
        )
    with _qid_lock:
        _qid_cache = None
    return {"removed": removed}


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
DAILY_REFRESH_INTERVAL = 24 * 3600
WARMUP_WORKERS = 4


def _warm_qids(codes: list[str]) -> None:
    """Resolve any missing Wikidata QIDs in a few batched calls."""
    dataset = _dataset()
    qids = _qids()
    missing = []
    for code in codes:
        base = dataset.get(code, {})
        name = base.get("name_it") or base.get("name_en")
        if name and name not in qids:
            missing.append(name)
    if not missing:
        return
    resolved = _resolve_qid_batch(missing)
    with _qid_lock:
        qids.update(resolved)
    _save_qid_cache(qids)


def warmup_status() -> dict:
    """Snapshot of the current (or last) warm-up run."""
    with _state_lock:
        return dict(_state)


def _online_modified_all(codes: Optional[list[str]] = None) -> dict[str, Optional[str]]:
    """Online Wikidata ``modified`` date for countries (all by default), by code.

    One batched API call for the whole set, so stamping the cache with
    ``source_updated_at`` never triggers per-country network requests.
    """
    dataset = _dataset()
    qids = _qids()
    selected = sorted(dataset) if codes is None else codes
    by_code: dict[str, Optional[str]] = {}
    pairs: list[tuple[str, str]] = []
    for code in selected:
        qid = qids.get(dataset[code].get("name_it") or dataset[code].get("name_en"))
        if qid:
            pairs.append((code, qid))
            by_code[code] = None
    dates = _fetch_modified_dates([qid for _, qid in pairs]) if pairs else {}
    for code, qid in pairs:
        by_code[code] = dates.get(qid)
    return by_code


def _stale_codes(codes: list[str]) -> list[str]:
    """Codes whose cached entry is missing or older than its TTL (local reads)."""
    cache_dir = _cache_dir()
    stale: list[str] = []
    for code in codes:
        cached = _read_json(cache_dir / f"{code}.json")
        if not cached:
            stale.append(code)
            continue
        ttl = CACHE_TTL if cached.get("live_ok") else CACHE_FAIL_TTL
        if not _fresh(cached.get("fetched_at", ""), ttl):
            stale.append(code)
    return stale


def _plan_refresh(online: dict[str, Optional[str]]) -> dict[str, Optional[str]]:
    """Decide which countries need re-fetching.

    Returns ``{code: online_modified_or_None}`` for entries whose Wikidata
    ``modified`` date is newer than the cached ``source_updated_at`` (and for
    cold/legacy entries), so a forced refresh only touches data that actually
    changed online.
    """
    dataset = _dataset()
    qids = _qids()
    cache_dir = _cache_dir()
    plan: dict[str, Optional[str]] = {}
    for code, base in dataset.items():
        qid = qids.get(base.get("name_it") or base.get("name_en"))
        if not qid:
            plan[code] = None  # no QID: re-fetch (it re-resolves internally)
            continue
        cached = _read_json(cache_dir / f"{code}.json")
        cached_ts = (cached or {}).get("source_updated_at")
        new_ts = online.get(code)
        if not cached_ts:
            plan[code] = new_ts  # cold or legacy entry
        elif new_ts and new_ts > cached_ts:
            plan[code] = new_ts  # newer data available
        # else: online unchanged or unknown -> keep the cached entry
    return plan


def prewarm(force: bool = False) -> dict:
    """Start a background pre-fetch. No-op if already running.

    - ``force=False`` only fetches entries older than the cache TTL (no
      network traffic at all when the cache is warm).
    - ``force=True`` re-checks the online data and re-fetches only the
      countries whose Wikidata entity is newer than the cached copy.
    """
    with _state_lock:
        if _state["running"]:
            return dict(_state)
        _state.update(
            running=True,
            force=force,
            total=len(_dataset()),
            done=0,
            errors=0,
            started_at=_now(),
            finished_at=None,
        )
    codes = sorted(_dataset())
    try:
        _warm_qids(codes)
        if force:
            plan = _plan_refresh(_online_modified_all())
        else:
            stale = _stale_codes(codes)
            online = _online_modified_all(stale)
            plan = {c: online.get(c) for c in stale}
    except Exception:  # noqa: BLE001 - never leave the warm-up stuck "running"
        logger.exception("warm-up planning failed")
        with _state_lock:
            _state.update(running=False, finished_at=_now())
        return warmup_status()
    with _state_lock:
        _state["total"] = len(plan)
    threading.Thread(target=_prewarm_worker, args=(plan, force), daemon=True).start()
    return warmup_status()


def _prewarm_worker(plan: dict[str, Optional[str]], force: bool) -> None:
    try:
        with ThreadPoolExecutor(max_workers=WARMUP_WORKERS) as pool:
            futures = [
                pool.submit(_warm_one, code, force, known_modified=modified)
                for code, modified in plan.items()
            ]
            for future in as_completed(futures):
                future.result()  # _warm_one never raises
    finally:
        with _state_lock:
            _state["running"] = False
            _state["finished_at"] = _now()


def _warm_one(code: str, force: bool, known_modified: Optional[str] = None) -> None:
    try:
        get_flag_details(
            code,
            force=force,
            check_modified=False,
            source_updated_at=known_modified,
        )
    except Exception:  # noqa: BLE001 - keep the rest of the run going
        logger.exception("warm-up failed for %s", code)
        with _state_lock:
            _state["errors"] += 1
    finally:
        with _state_lock:
            _state["done"] += 1


def _daily_loop() -> None:
    while True:
        time.sleep(DAILY_REFRESH_INTERVAL)
        try:
            # re-fetch only the entries whose online data changed since last
            # fetch; the batched modified-date check keeps this cheap
            prewarm(force=True)
        except Exception:  # noqa: BLE001
            logger.exception("daily warm-up failed")


def _start_daily_thread() -> None:
    global _daily_thread
    if _daily_thread is None:
        _daily_thread = threading.Thread(target=_daily_loop, daemon=True)
        _daily_thread.start()


_start_daily_thread()
