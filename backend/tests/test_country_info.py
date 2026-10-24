"""Flag detail tests: route behaviour and live-source fallback with mocks."""
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.quiz import country_info

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path):
    """Point the cache/baseline at temp dirs so real files are never touched."""
    old = {
        k: os.environ.get(k)
        for k in ("FIFAQUIZ_DATA_CACHE_DIR", "FIFAQUIZ_DATA_BASELINE_DIR")
    }
    os.environ["FIFAQUIZ_DATA_CACHE_DIR"] = str(tmp_path / "cache")
    os.environ["FIFAQUIZ_DATA_BASELINE_DIR"] = str(tmp_path / "baseline")
    get_settings.cache_clear()
    # seed a minimal baseline so the dataset resolves
    import json

    (tmp_path / "baseline").mkdir(parents=True, exist_ok=True)
    (tmp_path / "baseline" / "associations_manifest.json").write_text(
        json.dumps({
            "associations": [
                {
                    "fifa_code": "ITA",
                    "name": "Italy",
                    "name_it": "Italia",
                    "confederation": "UEFA",
                    "continent": "Europe",
                },
                {
                    "fifa_code": "FRA",
                    "name": "France",
                    "name_it": "Francia",
                    "confederation": "UEFA",
                    "continent": "Europe",
                },
                {
                    "fifa_code": "AUT",
                    "name": "Austria",
                    "name_it": "Austria",
                    "confederation": "UEFA",
                    "continent": "Europe",
                },
            ]
        }),
        encoding="utf-8",
    )
    (tmp_path / "baseline" / "name_it.json").write_text(
        json.dumps({"ITA": "Italia", "FRA": "Francia", "AUT": "Austria"}),
        encoding="utf-8",
    )
    yield
    country_info._dataset.cache_clear()
    country_info._names_it.cache_clear()
    country_info._codes_by_normalized_name.cache_clear()
    country_info._qid_cache = None
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def _wikidata_json(query: str) -> dict:
    def bindings(rows):
        return {"results": {"bindings": rows}}

    if "wdt:P47" in query:  # borders
        return bindings([
            {"neighbor": {"type": "uri", "value": "http://www.wikidata.org/entity/Q142"},
             "nb_it": {"type": "literal", "value": "Francia"},
             "nb_en": {"type": "literal", "value": "France"}},
            {"neighbor": {"type": "uri", "value": "http://www.wikidata.org/entity/Q40"},
             "nb_it": {"type": "literal", "value": "Austria"},
             "nb_en": {"type": "literal", "value": "Austria"}},
        ])
    if "ps:P1082" in query:  # population
        return bindings([
            {"pop": {"type": "literal", "value": "58983000"}},
        ])
    if "wdt:P1082" in query:  # population fallback
        return bindings([])
    if "wdt:P37" in query:  # official languages
        return bindings([
            {"lbl": {"type": "literal", "value": "italiano"}},
        ])
    if "wdt:P2936" in query:  # other spoken languages
        return bindings([])
    if "P1448" in query:  # country fields
        values = {
            "official_it": "Repubblica Italiana",
            "capital_it": "Roma",
            "gov_it": "repubblica parlamentare",
            "pres_it": "Sergio Mattarella",
            "procl": "1946-06-02T00:00:00Z",
            "demon_it": "italiani",
            "cont_it": "Europa",
            "area": "301340",
        }
        row = {
            var: {"type": "literal", "value": val}
            for var, val in values.items()
        }
        return bindings([row])
    return bindings([])


def _fake_http_json(url: str, params=None):
    from app.quiz.country_info import (
        WIKIDATA_API,
        WIKIDATA_QUERY_URL,
        WIKIPEDIA_IT_API,
    )

    if url == WIKIDATA_API:
        ids = (params or {}).get("ids", "")
        return {
            "entities": {
                qid: {"id": qid, "modified": "2026-08-01T00:00:00Z"}
                for qid in ids.split("|")
                if qid
            }
        }
    if url == WIKIPEDIA_IT_API:
        return {
            "query": {
                "pages": {"1": {"pageid": 1, "title": "Italia", "pageprops": {"wikibase_item": "Q38"}}}
            }
        }
    if url == WIKIDATA_QUERY_URL:
        return _wikidata_json((params or {}).get("query", ""))
    raise AssertionError(f"unexpected url: {url}")


def test_flag_details_404_unknown_code() -> None:
    resp = client.get("/api/flags/NOPE")
    assert resp.status_code == 404


@patch("app.quiz.country_info._http_json", side_effect=_fake_http_json)
def test_flag_details_live_sources(mock_http) -> None:
    body = client.get("/api/flags/ITA").json()
    assert body["code"] == "ITA"
    assert body["name"] == "Italia"
    details = body["details"]
    assert details["official_name"] == "Repubblica Italiana"
    assert details["capital"] == "Roma"
    assert details["government_form"] == "repubblica parlamentare"
    assert details["president"] == "Sergio Mattarella"
    assert details["proclamation"] == "1946-06-02"
    assert details["area_km2"] == 301340.0
    assert details["population"] == 58983000
    assert details["density"] == pytest.approx(195.7, abs=0.1)
    assert details["demonym"] == "italiani"
    assert details["continent"] == "Europa"
    assert details["official_languages"] == ["italiano"]
    assert details["other_languages"] == []
    codes = {b["code"] for b in details["borders"]}
    assert codes == {"FRA", "AUT"}
    assert details["football"]["wc"] == 18
    assert details["football"]["trophies"] == 4


@patch("app.quiz.country_info._http_json", side_effect=OSError("offline"))
def test_flag_details_degrades_to_partial(mock_http) -> None:
    body = client.get("/api/flags/ITA").json()
    details = body["details"]
    assert body["name"] == "Italia"
    # base dataset + curated football data must still be present
    assert details["full_name"] == "Italia"
    assert details["football"]["wc"] == 18
    # live-only fields are empty
    assert details["capital"] is None
    assert details["official_languages"] == []
    assert details["borders"] == []


@patch("app.quiz.country_info._http_json", side_effect=_fake_http_json)
def test_flag_details_refresh_bypasses_cache(mock_http) -> None:
    client.get("/api/flags/ITA")  # fills the on-disk cache
    calls_after_fill = mock_http.call_count
    body = client.get("/api/flags/ITA").json()  # served from cache
    assert body["details"]["capital"] == "Roma"
    assert mock_http.call_count == calls_after_fill
    body = client.get("/api/flags/ITA?refresh=true").json()
    assert body["details"]["capital"] == "Roma"
    assert mock_http.call_count > calls_after_fill


@patch("app.quiz.country_info._http_json", side_effect=_fake_http_json)
def test_prewarm_fills_cache_and_reports_progress(mock_http) -> None:
    start = client.post("/api/flags/warmup").json()
    assert start["running"] is True
    assert start["total"] == 3
    status = start
    for _ in range(400):
        status = client.get("/api/flags/warmup/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["running"] is False
    assert status["done"] == status["total"] == 3
    assert status["errors"] == 0
    # everything is on disk now: no external calls needed
    mock_http.reset_mock()
    body = client.get("/api/flags/AUT").json()
    assert body["code"] == "AUT"
    assert mock_http.call_count == 0


@patch("app.quiz.country_info._http_json", side_effect=_fake_http_json)
def test_failed_refresh_keeps_previously_cached_details(mock_http) -> None:
    """An offline refresh must not wipe a good cached entry."""
    filled = client.get("/api/flags/ITA").json()
    assert filled["details"]["capital"] == "Roma"

    with patch("app.quiz.country_info._http_json", side_effect=OSError("offline")):
        body = client.get("/api/flags/ITA?refresh=true").json()

    assert body["details"]["capital"] == "Roma"
    assert body["details"]["population"] == 58983000
    codes = {b["code"] for b in body["details"]["borders"]}
    assert codes == {"FRA", "AUT"}
    assert body["live_ok"] is False


@patch("app.quiz.country_info._http_json", side_effect=_fake_http_json)
def test_stale_entry_is_served_without_waiting(mock_http) -> None:
    """A stale cache is returned immediately and revalidated in background."""
    client.get("/api/flags/ITA")  # fill
    from app.quiz import country_info as ci

    path = ci._cache_dir() / "ITA.json"
    payload = ci._read_json(path)
    payload["fetched_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    refreshed: list[str] = []
    with patch.object(ci, "_spawn_refresh", lambda code: refreshed.append(code)):
        mock_http.reset_mock()
        body = client.get("/api/flags/ITA").json()

    assert refreshed == ["ITA"]
    assert body["details"]["capital"] == "Roma"
    assert mock_http.call_count == 0  # no synchronous network wait


def test_fresh_rejects_bad_timestamps() -> None:
    from app.quiz.country_info import _fresh

    assert _fresh(None, 10) is False  # type: ignore[arg-type]
    assert _fresh("", 10) is False
    assert _fresh("not-a-date", 10) is False
    # naive ISO strings are treated as UTC instead of crashing
    assert _fresh("2020-01-01T00:00:00", 10**12) is True


def test_path_for_rejects_traversal(tmp_path) -> None:
    from app.data.assets import FlagAssetStore

    store = FlagAssetStore(cache_dir=tmp_path / "flags")
    assert store.path_for("../../etc/passwd") is None
    assert store.path_for("../ITA") is None
    assert store.path_for("ITA") is None  # valid shape, file missing
