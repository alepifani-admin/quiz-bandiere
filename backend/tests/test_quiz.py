"""Quiz engine + API tests: languages, difficulty levels, question flow."""
from fastapi.testclient import TestClient

from app.main import app
from app.quiz.engine import QuizEngine

client = TestClient(app)


def test_names_both_languages() -> None:
    engine = QuizEngine()
    assert engine.names_for("en").get("ITA") == "Italy"
    assert engine.names_for("it").get("ITA") == "Italia"


def test_difficulty_config_endpoint() -> None:
    resp = client.get("/api/quiz/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["languages"] == ["it", "en"]
    levels = body["difficulty"]
    assert [d["level"] for d in levels] == list(range(1, 8))
    # monotonic: more questions and higher min similarity at higher levels
    qs = [d["questions"] for d in levels]
    ms = [d["min_similarity"] for d in levels]
    assert qs == sorted(qs)
    assert ms == sorted(ms)
    # every level mixes both question types (weight strictly between 0 and 1)
    weights = [d["flag_to_name_weight"] for d in levels]
    assert all(0 < w < 1 for w in weights)
    assert weights == sorted(weights, reverse=True)


def test_question_respects_difficulty_and_language() -> None:
    engine = QuizEngine()
    types_l1 = {engine.question(difficulty=1, language="en")["type"] for _ in range(30)}
    assert "flag_to_name" in types_l1
    assert "name_to_flag" in types_l1

    q = engine.question(difficulty=1, language="en")
    assert q["difficulty"] == 1
    assert q["language"] == "en"
    assert len(q["options"]) == 4
    assert q["target"]["name"] == q["options"][q["correct_index"]]["name"]
    assert q["options"][q["correct_index"]]["code"] == q["target"]["code"]

    types_l7 = {engine.question(difficulty=7, language="it")["type"] for _ in range(30)}
    assert "flag_to_name" in types_l7
    assert "name_to_flag" in types_l7
    q7 = engine.question(difficulty=7, language="it")
    assert q7["difficulty"] == 7
    assert q7["target"]["name"].isprintable()


def test_exclude_prevents_repeats() -> None:
    engine = QuizEngine()
    q1 = engine.question(difficulty=4)
    used = {q1["target"]["code"]} | {o["code"] for o in q1["options"]}
    q2 = engine.question(difficulty=4, exclude=used)
    assert q2["target"]["code"] not in used
    assert all(o["code"] not in used for o in q2["options"])


def test_exclude_never_blocks_question() -> None:
    engine = QuizEngine()
    all_codes = {c for c in engine.codes() if engine.names_for("it").get(c)}
    q = engine.question(difficulty=4, exclude=all_codes)
    assert len(q["options"]) == 4


def test_options_rotate_through_whole_pool() -> None:
    """Answers are always distinct; a flag only repeats once every other flag
    in the pool has been shown at least once."""
    engine = QuizEngine()
    pool = {c for c in engine.codes() if engine.names_for("it").get(c)}
    used: set[str] = set()
    shown: set[str] = set()
    rounds = 0
    for _ in range(len(pool) * 2):
        q = engine.question(difficulty=7, exclude=used)
        opts = {o["code"] for o in q["options"]}
        assert len(opts) == 4  # the 4 answers are always distinct
        if len(pool - used) >= 4:
            # enough never-shown flags to fill a full question: no reuse
            assert not (opts & used), "flag reused before the whole pool was shown"
        shown |= opts
        used |= opts
        if not (pool - used):
            used = set()  # every flag shown -> new round
            rounds += 1
    assert len(shown) == len(pool)  # every flag ended up in the answers
    assert rounds >= 2  # and the rotation kept cycling


def test_answer_roundtrip() -> None:
    q = client.get("/api/quiz/question", params={"difficulty": 3}).json()
    resp = client.post(
        "/api/quiz/answer",
        json={"question": q, "selected_index": q["correct_index"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["correct"] is True
    assert body["correct_code"] == q["options"][q["correct_index"]]["code"]


def test_unknown_difficulty_rejected() -> None:
    resp = client.get("/api/quiz/question", params={"difficulty": 99})
    assert resp.status_code == 400


def test_world_pool_dataset() -> None:
    engine = QuizEngine(pool="world")
    codes = engine.codes()
    assert len(codes) >= 300  # all world flags (flagcdn set)
    assert "IT" in codes
    assert "US-CA" in codes  # non-ISO codes are part of the set
    assert engine.names_for("it").get("IT") == "Italia"
    assert engine.names_for("en").get("IT") == "Italy"
    assert engine.names_for("it").get("US-GA") == "Georgia (USA)"


def test_world_question_assets_and_names() -> None:
    engine = QuizEngine(pool="world")
    for lang in ("it", "en"):
        for qtype in ("flag_to_name", "name_to_flag"):
            q = engine.question(qtype=qtype, difficulty=1, language=lang)
            assert len(q["options"]) == 4
            assert all(o["asset_url"] and o["asset_url"].startswith("/api/world/flags/") for o in q["options"])
            assert q["target"]["name"] == q["options"][q["correct_index"]]["name"]
            assert all(o["name"] for o in q["options"])


def test_world_question_does_not_record_views() -> None:
    from app.quiz import flag_views

    engine = QuizEngine(pool="world")
    before = len(flag_views.top(limit=10000))
    engine.question(difficulty=2)
    after = len(flag_views.top(limit=10000))
    assert after == before
    fifa = QuizEngine()
    fifa.question(difficulty=2)
    assert len(flag_views.top(limit=10000)) >= after


def test_world_question_api_scope() -> None:
    # force a flag question: color questions (mixed in by difficulty) have
    # color/number options without an asset_url
    resp = client.get(
        "/api/quiz/question",
        params={"scope": "world", "difficulty": 1, "qtype": "flag_to_name"},
    )
    assert resp.status_code == 200
    q = resp.json()
    assert all(o["asset_url"].startswith("/api/world/flags/") for o in q["options"])


def test_world_flags_endpoint() -> None:
    resp = client.get("/api/world/flags")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 300
    assert "IT" in body["flags"]
    assert body["flags"]["IT"]["name"] == "Italia"
    assert body["flags"]["IT"]["continent"] == "Europe"


def test_world_flag_asset_endpoint() -> None:
    resp = client.get("/api/world/flags/IT/asset")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_color_question_types_generate() -> None:
    from app.quiz.engine import COLOR_QUESTION_TYPES

    engine = QuizEngine(pool="world")
    for qtype in COLOR_QUESTION_TYPES:
        for lang in ("it", "en"):
            for _ in range(5):
                q = engine.question(qtype=qtype, difficulty=4, language=lang)
                assert q["type"] == qtype
                assert len(q["options"]) == 4
                assert 0 <= q["correct_index"] <= 3
                assert q["prompt"]
                assert q["target"]["name"]
                kinds = {o.get("kind") for o in q["options"]}
                assert len(kinds) == 1
                # answer roundtrip works for any option kind
                ok = engine.answer(q, q["correct_index"])
                assert ok["correct"] is True
                ko = engine.answer(q, (q["correct_index"] + 1) % 4)
                assert ko["correct"] is False


def test_color_questions_world_only() -> None:
    from app.quiz.engine import COLOR_QUESTION_TYPES

    fifa = QuizEngine()
    for qtype in COLOR_QUESTION_TYPES:
        try:
            fifa.question(qtype=qtype, difficulty=4)
            raise AssertionError(f"{qtype} must be rejected on the FIFA pool")
        except ValueError:
            pass


def test_color_questions_api_scope() -> None:
    from app.quiz.engine import COLOR_QUESTION_TYPES

    resp = client.get(
        "/api/quiz/question",
        params={"scope": "world", "qtype": "flag_to_dominant_color", "difficulty": 3},
    )
    assert resp.status_code == 200
    q = resp.json()
    assert q["type"] == "flag_to_dominant_color"
    assert {o["kind"] for o in q["options"]} == {"color"}
    assert all(o["name"] for o in q["options"])
    # fifa scope rejects color types
    resp2 = client.get(
        "/api/quiz/question",
        params={"scope": "fifa", "qtype": "flag_to_dominant_color"},
    )
    assert resp2.status_code == 400
    # unknown types still rejected
    resp3 = client.get(
        "/api/quiz/question",
        params={"scope": "world", "qtype": "not_a_type"},
    )
    assert resp3.status_code == 400


def test_color_questions_mix_by_difficulty() -> None:
    from app.quiz.engine import COLOR_QUESTION_TYPES

    engine = QuizEngine(pool="world")
    counts: dict[str, int] = {}
    for _ in range(200):
        q = engine.question(difficulty=5, language="it")
        counts[q["type"]] = counts.get(q["type"], 0) + 1
    n_colors = sum(v for k, v in counts.items() if k in COLOR_QUESTION_TYPES)
    assert n_colors >= 60  # world quiz mixes color questions (weight > 0)
    assert counts.get("flag_to_name", 0) > 0
    assert counts.get("name_to_flag", 0) > 0
