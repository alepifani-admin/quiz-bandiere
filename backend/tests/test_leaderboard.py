"""Leaderboard tests: persistence file, rank ordering, API endpoints."""
import os
import threading

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.quiz import leaderboard

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_leaderboard(tmp_path):
    """Point the leaderboard at a temp file so real scores are never touched."""
    os.environ["FIFAQUIZ_LEADERBOARD_FILE"] = str(tmp_path / "leaderboard.json")
    get_settings.cache_clear()
    yield


def test_leaderboard_empty_by_default() -> None:

    assert leaderboard.load_scores() == []
    body = client.get("/api/leaderboard").json()
    assert body["levels"] == {}


def test_sanitize_nickname() -> None:
    assert leaderboard.sanitize_nickname("  Alice-77  ") == "Alice-77"
    assert len(leaderboard.sanitize_nickname("x" * 100)) == leaderboard.MAX_NICKNAME_LENGTH
    try:
        leaderboard.sanitize_nickname("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("blank nickname must be rejected")


def test_add_score_returns_rank_and_persists() -> None:

    e1 = leaderboard.add_score("Mario", difficulty=3, score=6, total=10)
    assert e1["rank"] == 1  # only entry at insert time
    e2 = leaderboard.add_score("Luigi", difficulty=3, score=9, total=10)
    assert e2["rank"] == 1  # better score, top of level 3
    assert leaderboard.rank_of(e1["id"]) == 2
    assert leaderboard.load_scores()[0]["nickname"] == "Luigi"
    # lower score on a different level does not disturb level 3
    leaderboard.add_score("Mario", difficulty=1, score=1, total=8)
    top3 = leaderboard.best_per_level(limit=10)["3"]
    assert [e["nickname"] for e in top3] == ["Luigi", "Mario"]
    assert len(leaderboard.best_per_level()["1"]) == 1


def test_api_add_and_get() -> None:

    resp = client.post(
        "/api/leaderboard",
        json={"nickname": "TestUser", "difficulty": 5, "score": 12, "total": 15},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rank"] == 1
    assert body["nickname"] == "TestUser"
    get = client.get("/api/leaderboard").json()
    assert get["levels"]["5"][0]["score"] == 12


def test_flag_is_sanitized_and_returned() -> None:

    assert leaderboard.sanitize_flag("ita") == "ITA"
    assert leaderboard.sanitize_flag("it-aly!") == "ITA"
    assert leaderboard.sanitize_flag("") == ""
    assert leaderboard.sanitize_flag("123") == ""

    e = leaderboard.add_score("Alice", difficulty=4, score=8, total=10, flag="fra!")
    assert e["flag"] == "FRA"
    top = leaderboard.best_per_level(limit=3)["4"][0]
    assert top["flag"] == "FRA"

    resp = client.post(
        "/api/leaderboard",
        json={"nickname": "Bob", "difficulty": 4, "score": 7, "total": 10, "flag": "DEU"},
    )
    assert resp.status_code == 200
    assert resp.json()["flag"] == "DEU"


def test_api_rejects_invalid_score() -> None:
    resp = client.post(
        "/api/leaderboard",
        json={"nickname": "  ", "difficulty": 5, "score": 12, "total": 15},
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/leaderboard",
        json={"nickname": "Bob", "difficulty": 5, "score": 99, "total": 15},
    )
    assert resp.status_code == 422


def test_two_scopes_are_separate_boards() -> None:
    e1 = leaderboard.add_score("Mario", difficulty=3, score=6, total=10, scope="fifa")
    e2 = leaderboard.add_score("Luigi", difficulty=3, score=9, total=10, scope="world")
    e3 = leaderboard.add_score("Anna", difficulty=3, score=8, total=10, scope="world")
    assert e1["rank"] == 1
    assert e2["rank"] == 1  # top of the world board (not competing with fifa)
    assert e3["rank"] == 2
    fifa_top = leaderboard.best_per_level(limit=10, scope="fifa")["3"]
    world_top = leaderboard.best_per_level(limit=10, scope="world")["3"]
    assert [e["nickname"] for e in fifa_top] == ["Mario"]
    assert [e["nickname"] for e in world_top] == ["Luigi", "Anna"]
    # rank_of respects the scope
    assert leaderboard.rank_of(e1["id"]) == 1
    assert leaderboard.rank_of(e2["id"]) == 1
    assert leaderboard.rank_of(e3["id"]) == 2


def test_api_scope_param() -> None:
    resp = client.post(
        "/api/leaderboard",
        json={"nickname": "Bob", "difficulty": 4, "score": 7, "total": 10, "scope": "world"},
    )
    assert resp.status_code == 200
    assert resp.json()["scope"] == "world"
    fifa = client.get("/api/leaderboard", params={"scope": "fifa"}).json()
    world = client.get("/api/leaderboard", params={"scope": "world"}).json()
    assert "4" not in fifa["levels"] or all(
        e["scope"] == "fifa" for e in fifa["levels"].get("4", [])
    )
    assert world["levels"]["4"][0]["nickname"] == "Bob"
    assert world["levels"]["4"][0]["scope"] == "world"


def test_invalid_scope_defaults_to_fifa() -> None:
    assert leaderboard.sanitize_scope("") == "fifa"
    assert leaderboard.sanitize_scope("MONDO") == "fifa"
    assert leaderboard.sanitize_scope("World") == "world"


def test_concurrent_scores_are_not_lost() -> None:
    """Multi-player games submit one score per player in parallel: the
    read-modify-write must be serialized so no score is lost and the shared
    temp file never races."""
    n = 8
    errors: list[Exception] = []

    def submit(i: int) -> None:
        try:
            leaderboard.add_score(f"P{i}", difficulty=3, score=i + 1, total=10)
        except Exception as exc:  # noqa: BLE001 - surface any failure
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    scores = leaderboard.load_scores()
    assert len(scores) == n  # every parallel score survived
    assert sorted(e["score"] for e in scores) == list(range(1, n + 1))
    # the file is valid JSON after all the concurrent writes
    assert client.get("/api/leaderboard").status_code == 200
