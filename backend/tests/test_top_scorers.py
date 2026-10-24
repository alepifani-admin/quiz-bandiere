"""Top-scorers cache tests: Wikipedia parsing, TTL refresh, warm-up, endpoints."""
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.quiz import top_scorers

client = TestClient(app)


def _row(rank, player, nation, goals, caps, years="2003–", conf="UEFA"):
    rank_td = f"<td>{rank}</td>" if rank else ""
    return (
        f"<tr>{rank_td}<td>{player}</td><td>{nation}</td><td>{conf}</td>"
        f"<td>{goals}</td><td>{caps}</td><td>0.6</td><td>{years}</td>"
        f"<td>26 June 2014</td><td>[2]</td></tr>"
    )


def _page_html(rows: list[dict], multiname: str = "Hungary | Spain") -> str:
    header = (
        "<table class=\"wikitable sortable plainrowheaders\">"
        "<tbody><tr><th>Rank</th><th>Player</th><th>Nation</th><th>Confederation</th>"
        "<th>Goals</th><th>Caps</th><th>Goal/match</th><th>Career span</th>"
        "<th>Date of 50th goal</th><th>Ref.</th></tr>"
    )
    body = []
    for r in rows:
        if r["team"].startswith("MULTI"):
            body.append(_row(r["rank"], r["player"], multiname, r["goals"], r["caps"]))
        else:
            body.append(_row(r["rank"], r["player"], r["team"], r["goals"], r["caps"]))
    return header + "".join(body) + "</tbody></table>"


def _rows():
    return [
        {"rank": 1, "player": "Cristiano Ronaldo", "team": "Portugal", "goals": "146", "caps": "233"},
        {"rank": 2, "player": "Lionel Messi", "team": "Argentina", "goals": "125", "caps": "207"},
        {"rank": 3, "player": "Ali Daei", "team": "Iran", "goals": "108 [c]", "caps": "148 [c]"},
        {"rank": None, "player": "Robert Lewandowski", "team": "Poland", "goals": "89", "caps": "167"},
    ]


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
    import json

    (tmp_path / "baseline").mkdir(parents=True, exist_ok=True)
    (tmp_path / "baseline" / "associations_manifest.json").write_text(
        json.dumps({
            "associations": [
                {"fifa_code": "POR", "name": "Portugal", "name_it": "Portogallo",
                 "confederation": "UEFA", "continent": "Europe"},
                {"fifa_code": "IRN", "name": "IR Iran", "name_it": "Iran",
                 "confederation": "AFC", "continent": "Asia"},
                {"fifa_code": "ARG", "name": "Argentina", "name_it": "Argentina",
                 "confederation": "CONMEBOL", "continent": "South America"},
                {"fifa_code": "POL", "name": "Poland", "name_it": "Polonia",
                 "confederation": "UEFA", "continent": "Europe"},
            ]
        }),
        encoding="utf-8",
    )
    yield
    top_scorers._team_index.cache_clear()
    top_scorers._dataset.cache_clear()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def _fetch_ok(rows=None, updated="2026-07-19T00:00:00Z"):
    html = _page_html(rows or _rows())
    return patch.object(top_scorers, "_fetch_page",
                        return_value=(top_scorers._parse_page(html), updated))


def test_parse_page_extracts_rows():
    scorers = top_scorers._parse_page(_page_html(_rows()))
    assert len(scorers) == 4
    assert scorers[0]["player"] == "Cristiano Ronaldo"
    assert scorers[0]["team"] == "Portugal"
    assert scorers[0]["goals"] == 146
    assert scorers[0]["caps"] == 233
    assert scorers[3]["rank"] == 3  # tied rank carried forward
    assert scorers[3]["player"] == "Robert Lewandowski"
    assert scorers[3]["goals"] == 89


def test_parse_strips_footnote_markers():
    scorers = top_scorers._parse_page(_page_html(_rows()))
    assert scorers[2]["goals"] == 108
    assert scorers[2]["caps"] == 148


def test_parse_multi_nation_cells():
    rows = [{"rank": 10, "player": "Ferenc Puskás", "team": "MULTI",
             "goals": "84", "caps": "89"}]
    scorers = top_scorers._parse_page(_page_html(rows))
    assert scorers[0]["team"] == "Hungary, Spain"


def test_fetch_and_cache_then_reuse(monkeypatch):
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return top_scorers._parse_page(_page_html(_rows())), "2026-07-19T00:00:00Z"

    monkeypatch.setattr(top_scorers, "_fetch_page", fake_fetch)
    first = top_scorers.get_top_scorers()
    assert len(first["scorers"]) == 4
    assert first["scorers"][0]["code"] == "POR"
    assert first["scorers"][0]["name_it"] == "Portogallo"
    assert top_scorers._cache_path().exists()

    second = top_scorers.get_top_scorers()  # fresh -> no network
    assert calls["n"] == 1
    assert second["scorers"][0]["player"] == "Cristiano Ronaldo"


def test_force_refresh_unchanged_keeps_cache(monkeypatch):
    html = _page_html(_rows())
    monkeypatch.setattr(top_scorers, "_fetch_page",
                        lambda: (top_scorers._parse_page(html), "2026-07-19T00:00:00Z"))
    top_scorers.get_top_scorers()
    before = top_scorers._cache_path().read_text(encoding="utf-8")

    top_scorers.get_top_scorers(force=True, check_modified=True)
    after = top_scorers._cache_path().read_text(encoding="utf-8")
    assert after == before  # unchanged ranking -> no rewrite


def test_force_refresh_changed_updates_cache(monkeypatch):
    monkeypatch.setattr(top_scorers, "_fetch_page",
                        lambda: (top_scorers._parse_page(_page_html(_rows())), "2026-07-19T00:00:00Z"))
    top_scorers.get_top_scorers()
    changed = _rows()
    changed[0]["goals"] = "147"
    monkeypatch.setattr(top_scorers, "_fetch_page",
                        lambda: (top_scorers._parse_page(_page_html(changed)), "2026-07-20T00:00:00Z"))
    updated = top_scorers.get_top_scorers(force=True, check_modified=True)
    assert updated["scorers"][0]["goals"] == 147
    assert updated["source_updated_at"] == "2026-07-20T00:00:00Z"


def test_offline_fallback_returns_cached(monkeypatch):
    monkeypatch.setattr(top_scorers, "_fetch_page",
                        lambda: (top_scorers._parse_page(_page_html(_rows())), "2026-07-19T00:00:00Z"))
    top_scorers.get_top_scorers()

    def boom():
        raise OSError("network down")

    monkeypatch.setattr(top_scorers, "_fetch_page", boom)
    payload = top_scorers.get_top_scorers()  # stale but cached -> fallback
    assert payload["scorers"][0]["player"] == "Cristiano Ronaldo"


def test_offline_without_cache_raises(monkeypatch):
    monkeypatch.setattr(top_scorers, "_fetch_page", lambda: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError):
        top_scorers.get_top_scorers()


def test_clear_cache_removes_file():
    with _fetch_ok():
        top_scorers.get_top_scorers()
    assert top_scorers._cache_path().exists()
    result = top_scorers.clear_cache()
    assert result["removed"] == 1
    assert not top_scorers._cache_path().exists()


def test_warmup_fills_cache():
    with _fetch_ok():
        status = top_scorers.prewarm()
        assert status["running"] is True
        deadline = time.time() + 5
        while time.time() < deadline:
            s = top_scorers.warmup_status()
            if not s["running"]:
                break
            time.sleep(0.05)
        assert s["running"] is False
        assert s["errors"] == 0
        assert top_scorers._cache_path().exists()


def test_warmup_warm_cache_is_noop():
    with _fetch_ok():
        top_scorers.get_top_scorers()
        status = top_scorers.prewarm()  # warm cache, force=False
        deadline = time.time() + 5
        while time.time() < deadline:
            s = top_scorers.warmup_status()
            if not s["running"]:
                break
            time.sleep(0.05)
        assert s["done"] == 1  # "checked" without network traffic


def test_endpoint_list_and_refresh():
    with _fetch_ok():
        resp = client.get("/api/topscorers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 4
        assert data["scorers"][0]["player"] == "Cristiano Ronaldo"
        assert data["scorers"][0]["name_it"] == "Portogallo"
        assert data["scorers"][0]["asset_url"] == "/api/flags/POR/asset"
        # multi-team entries keep the raw name, no code
        with _fetch_ok([{"rank": 10, "player": "Ferenc Puskás", "team": "MULTI",
                         "goals": "84", "caps": "89"}]):
            resp2 = client.get("/api/topscorers?refresh=true")
            assert resp2.status_code == 200
            puskas = resp2.json()["scorers"][0]
            assert puskas["code"] is None
            assert puskas["team"] == "Hungary, Spain"
        assert client.get("/api/topscorers/cache").json()["entries"] == 1
        cleared = client.post("/api/topscorers/cache/clear").json()
        assert cleared["removed"] == 1
        assert client.get("/api/topscorers/cache").json()["entries"] == 0
