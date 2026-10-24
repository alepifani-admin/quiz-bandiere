"""Quiz engine (sections 11-14).

Loads the association data and the full flag analysis once (lazy in-memory
index), then generates multiple-choice questions:

- flag_to_name: show the flag, pick the association (4 country names)
- name_to_flag: show the name, pick the flag (4 flag images)

Distractors are the flags most similar to the target under the Flag
Similarity Score (section 15), so the quiz is genuinely challenging.
Difficulty levels 1-7 tune question count, question-type mix, distractor
similarity bounds and the confederation pool (see config.DIFFICULTY_LEVELS).
"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image

from app.analysis.analyzer import analyze_flag
from app.analysis.cache import ANALYSIS_FILES, load_cached, save
from app.core.config import difficulty_config, get_settings
from app.data.assets import FlagAssetStore
from app.quiz.flag_views import record as record_views
from app.quiz.similarity import similarity

QUESTION_TYPES = ("flag_to_name", "name_to_flag")

#: Color-based question types, available only in the world quiz. They reuse
#: the per-flag color/geometry data already stored in the analysis index.
COLOR_QUESTION_TYPES = (
    "flag_to_dominant_color",
    "flag_to_colors",
    "flag_has_not_color",
    "flag_to_color_count",
    "flag_shared_colors",
    "flag_to_stripes",
    "flag_to_monochrome",
)

ALL_QUESTION_TYPES = QUESTION_TYPES + COLOR_QUESTION_TYPES

DISTRACTOR_COUNT = 3

#: Human color categories -> localized labels (same keys as the analysis).
COLOR_NAMES: dict[str, dict[str, str]] = {
    "it": {
        "WHITE": "Bianco", "RED": "Rosso", "BLUE": "Blu", "GREEN": "Verde",
        "YELLOW": "Giallo", "BLACK": "Nero", "ORANGE": "Arancione",
        "PURPLE": "Viola", "BROWN": "Marrone", "GREY": "Grigio", "PINK": "Rosa",
    },
    "en": {
        "WHITE": "White", "RED": "Red", "BLUE": "Blue", "GREEN": "Green",
        "YELLOW": "Yellow", "BLACK": "Black", "ORANGE": "Orange",
        "PURPLE": "Purple", "BROWN": "Brown", "GREY": "Grey", "PINK": "Pink",
    },
}

_COLOR_ORDER = tuple(COLOR_NAMES["it"])

#: Translated question templates for the color question types.
COLOR_PROMPTS: dict[str, dict[str, str]] = {
    "flag_to_dominant_color": {
        "it": "Qual è il colore predominante della bandiera di {name}?",
        "en": "What is the dominant color of the flag of {name}?",
    },
    "flag_to_colors": {
        "it": "Quali sono i colori della bandiera di {name}?",
        "en": "What are the colors of the flag of {name}?",
    },
    "flag_has_not_color": {
        "it": "Quale bandiera NON contiene il colore {color}?",
        "en": "Which flag does NOT contain the color {color}?",
    },
    "flag_to_color_count": {
        "it": "Quanti colori ha la bandiera di {name}?",
        "en": "How many colors does the flag of {name} have?",
    },
    "flag_shared_colors": {
        "it": "La bandiera di {name} usa {c1} e {c2}. Quale bandiera li usa entrambi?",
        "en": "The flag of {name} uses {c1} and {c2}. Which flag uses them both?",
    },
    "flag_to_stripes": {
        "it": "Quale di queste bandiere ha le strisce {orient}?",
        "en": "Which of these flags has {orient} stripes?",
    },
    "flag_to_monochrome": {
        "it": "Quale di queste bandiere ha un solo colore?",
        "en": "Which of these flags has a single color?",
    },
}

#: country-name lookup per language (baseline files).
NAME_FILES = {"it": "name_it.json", "en": "name_en.json"}

#: per-pool flag asset directories under the data cache.
FLAG_DIRS = {"fifa": "flag-svgs", "world": "world-flags"}

#: per-pool asset URL prefixes (see FlagAssetStore).
ASSET_PREFIXES = {"fifa": "/api/flags", "world": "/api/world/flags"}

#: per-pool baseline manifest file (world names live there).
WORLD_MANIFEST = "world_manifest.json"

DEFAULT_LANGUAGE = "it"
DEFAULT_DIFFICULTY = 4


class QuizEngine:
    """State-less question generator backed by a lazy analysis index.

    ``pool`` selects the dataset: ``"fifa"`` (the 211 FIFA national teams,
    confederation-filtered difficulties, view counting) or ``"world"`` (every
    world flag served by flagcdn, no confederations, no view counting).
    """

    def __init__(
        self,
        pool: str = "fifa",
        weights: Optional[dict] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        if pool not in FLAG_DIRS:
            raise ValueError(f"unknown pool: {pool}")
        self._pool = pool
        self._world = pool == "world"
        self._settings = get_settings()
        self._weights = weights
        self._rng = rng or random.SystemRandom()
        self._assets = FlagAssetStore(
            cache_dir=self._settings.cache_dir / FLAG_DIRS[pool],
            url_prefix=ASSET_PREFIXES[pool],
        )
        self._index: Optional[dict[str, dict]] = None
        self._names: dict[str, dict[str, str]] = {}
        self._confederations: dict[str, str] = {}
        # memoized data reused across questions (see _ranked_similar /
        # _presence_set): questions only re-rank cached scores instead of
        # recomputing every pairwise similarity from scratch.
        self._weights_flat: Optional[dict] = None
        self._ranked: dict[str, list[tuple[float, str]]] = {}
        self._presence_cache: dict[str, frozenset[str]] = {}
        self._primary_cache: dict[str, frozenset[str]] = {}

    # ------------------------------------------------------------------
    # Index loading
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self._index is not None:
            return
        if not self._world:
            self._load_confederations()
        cached = load_cached(ANALYSIS_FILES[self._pool])
        if cached is not None and len(cached) >= self._expected_count():
            self._index = cached
            return
        index: dict[str, dict] = {}
        flag_dir = self._settings.cache_dir / FLAG_DIRS[self._pool]
        for png in sorted(flag_dir.glob("*.png")):
            code = png.stem
            try:
                analysis = analyze_flag(Image.open(png).convert("RGB"), code)
            except Exception:  # noqa: BLE001 - skip corrupt assets
                continue
            index[code] = analysis.as_dict()
        self._index = index
        save(index, ANALYSIS_FILES[self._pool])

    def _expected_count(self) -> int:
        if self._world:
            manifest = self._world_manifest()
            return len(manifest.get("countries", [])) or 1
        return self._settings.expected_associations

    def _load_confederations(self) -> None:
        manifest_path = self._settings.baseline_dir / "associations_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self._confederations = {
            r.get("fifa_code", ""): r.get("confederation", "")
            for r in manifest.get("associations", [])
            if r.get("fifa_code")
        }

    def _world_manifest(self) -> dict:
        path = Path(self._settings.baseline_dir) / WORLD_MANIFEST
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"countries": []}

    def _load_names(self, language: str) -> dict[str, str]:
        if self._world:
            key = "name_it" if language == "it" else "name"
            return {
                c.get("code"): c.get(key) or c.get("name") or c.get("code")
                for c in self._world_manifest().get("countries", [])
                if c.get("code")
            }
        filename = NAME_FILES.get(language, NAME_FILES[DEFAULT_LANGUAGE])
        path = Path(self._settings.data_baseline_dir) / filename
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def names_for(self, language: str) -> dict[str, str]:
        """Country names for a language (cached)."""
        language = language or DEFAULT_LANGUAGE
        if language not in self._names:
            self._load()
            self._names[language] = self._load_names(language)
        return self._names[language]

    @property
    def index(self) -> dict[str, dict]:
        self._load()
        assert self._index is not None
        return self._index

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------
    def codes(self, language: str = DEFAULT_LANGUAGE, confederations: Optional[tuple[str, ...]] = None) -> list[str]:
        """Available codes, optionally filtered by confederation (FIFA only)."""
        names = self.names_for(language)
        codes = [c for c in self.index if c in names]
        if confederations and not self._world:
            codes = [c for c in codes if self._confederations.get(c) in confederations]
        return sorted(codes)

    def similar(
        self,
        code: str,
        n: int = DISTRACTOR_COUNT,
        min_similarity: float = 0.0,
        max_similarity: float = 1.0,
        pool: Optional[list[str]] = None,
    ) -> list[str]:
        """The n flags most similar to `code` (excluding itself), respecting
        the given similarity window; only candidates in `pool` are considered."""
        ranked = self._ranked_similar(code)
        if pool is None:
            picked = [
                c for s, c in ranked
                if min_similarity <= s <= max_similarity
            ]
        else:
            pool_set = set(pool)
            picked = [
                c for s, c in ranked
                if min_similarity <= s <= max_similarity and c in pool_set
            ]
        return picked[:n]

    def _score_weights(self) -> dict:
        """Plain-dict similarity weights (dumped once, never in the hot loop)."""
        if self._weights is not None:
            return self._weights
        if self._weights_flat is None:
            self._weights_flat = get_settings().similarity_weights.model_dump()
        return self._weights_flat

    def precompute(self) -> None:
        """Precompute the pairwise similarity rankings for every flag in the
        pool. Called by the startup warm-up so served questions never wait on
        the first-time score computation of their target."""
        for code in self.index:
            self._ranked_similar(code)

    def _ranked_similar(self, code: str) -> list[tuple[float, str]]:
        """All flags ranked by similarity to `code` (best first), memoized:
        the pairwise scores of one target are computed once and reused by
        every question that needs that target as its answer."""
        ranked = self._ranked.get(code)
        if ranked is None:
            index = self.index
            target = index[code]
            w = self._score_weights()
            scored = [
                (similarity(target, other_data, w), other)
                for other, other_data in index.items()
                if other != code
            ]
            scored.sort(reverse=True)
            self._ranked[code] = scored
            ranked = scored
        return ranked

    def question(
        self,
        qtype: Optional[str] = None,
        difficulty: int = DEFAULT_DIFFICULTY,
        language: str = DEFAULT_LANGUAGE,
        exclude: Optional[set] = None,
    ) -> dict:
        """Generate one question. Returns an API-ready dict.

        `exclude` is a set of fifa codes that must not appear in this
        question (neither target nor distractor). Used by the game to make
        sure every option is a flag the player has never seen before: flags
        are only reused once the whole pool has been shown (round-robin).
        """
        self._load()
        assert self._index is not None
        cfg = difficulty_config(difficulty)
        pool = self.codes(language=language, confederations=cfg.confederations)

        if qtype is None:
            qtype = self._pick_qtype(cfg)
        if qtype not in ALL_QUESTION_TYPES:
            raise ValueError(f"unknown question type: {qtype}")
        if not pool:
            raise ValueError("no flags available for this difficulty/language")

        excluded = {c.upper() for c in (exclude or set()) if c}

        # Rotate through the pool: unseen flags are always preferred for the
        # target and the distractors, so every flag appears in the answers
        # before any of them is reused. Computing distractors on the reduced
        # pool is also faster as the session progresses.
        candidate_pool = [c for c in pool if c not in excluded]

        # Color questions are a world-quiz-only feature: they reuse the color
        # and geometry data from the analysis index (no new computation).
        if qtype in COLOR_QUESTION_TYPES:
            if not self._world:
                raise ValueError(f"{qtype} is only available in the world quiz")
            question = self._color_question(qtype, cfg, language, pool, candidate_pool)
            if question is None:
                raise ValueError(f"could not generate a color question ({qtype})")
            return question

        target = self._rng.choice(candidate_pool) if candidate_pool else self._rng.choice(pool)

        # Distractors prefer unseen flags, most similar first; the similarity
        # window only ranks the preferred candidates, never blocks coverage.
        distractors = self.similar(
            target,
            n=DISTRACTOR_COUNT,
            min_similarity=cfg.min_similarity,
            max_similarity=cfg.max_similarity,
            pool=candidate_pool,
        )
        if len(distractors) < DISTRACTOR_COUNT:
            rest = [c for c in candidate_pool if c != target and c not in distractors]
            if len(distractors) + len(rest) < DISTRACTOR_COUNT:
                # not enough unseen flags left: reuse seen ones to complete
                # the answer, so the last question of a round still uses every
                # remaining unseen flag
                seen = [
                    c for c in pool
                    if c != target and c not in distractors and c not in rest
                ]
                rest.extend(seen)
            while len(distractors) < DISTRACTOR_COUNT and rest:
                idx = self._rng.randrange(len(rest))
                distractors.append(rest.pop(idx))

        options = [target] + list(distractors)
        self._rng.shuffle(options)
        correct_index = options.index(target)

        # view statistics are only collected for the FIFA pool: the world
        # quiz never touches the "most viewed flags" ranking
        if not self._world:
            record_views(options)

        target_data = self._target_payload(target, language)
        option_payloads = [self._target_payload(c, language) for c in options]

        return {
            "id": uuid.uuid4().hex,
            "type": qtype,
            "difficulty": cfg.level,
            "language": language,
            "target": target_data,
            "options": option_payloads,
            "correct_index": correct_index,
        }

    def _pick_qtype(self, cfg) -> str:
        if self._world and cfg.color_questions_weight > 0 and self._rng.random() < cfg.color_questions_weight:
            return self._rng.choice(COLOR_QUESTION_TYPES)
        roll = self._rng.random()
        if roll < cfg.flag_to_name_weight:
            return "flag_to_name"
        return "name_to_flag"

    # ------------------------------------------------------------------
    # Color questions (world quiz only)
    # ------------------------------------------------------------------
    def _color_of(self, code: str) -> dict:
        return self.index[code].get("color") or {}

    def _geom_of(self, code: str) -> dict:
        return self.index[code].get("geometry") or {}

    def _presence_set(self, code: str) -> frozenset[str]:
        cached = self._presence_cache.get(code)
        if cached is None:
            cached = frozenset(
                k for k, v in (self._color_of(code).get("presence") or {}).items() if v
            )
            self._presence_cache[code] = cached
        return cached

    def _primary_set(self, code: str) -> frozenset[str]:
        cached = self._primary_cache.get(code)
        if cached is None:
            cached = frozenset(self._color_of(code).get("primary_colors") or [])
            self._primary_cache[code] = cached
        return cached

    def _color_label(self, key: str, language: str) -> str:
        table = COLOR_NAMES.get(language, COLOR_NAMES["it"])
        return table.get(key, key.title())

    def _prompt(self, qtype: str, language: str, **kwargs) -> str:
        table = COLOR_PROMPTS[qtype]
        return table.get(language, table["it"]).format(**kwargs)

    def _question_payload(
        self,
        qtype: str,
        cfg,
        language: str,
        target_code: str,
        options: list[dict],
        correct_index: int,
        prompt: str,
        prompt_colors: list[dict],
    ) -> dict:
        return {
            "id": uuid.uuid4().hex,
            "type": qtype,
            "difficulty": cfg.level,
            "language": language,
            "prompt": prompt,
            "prompt_colors": prompt_colors,
            "target": self._target_payload(target_code, language),
            "options": options,
            "correct_index": correct_index,
        }

    def _color_option(self, key: str, language: str) -> dict:
        return {"code": key, "name": self._color_label(key, language), "kind": "color"}

    def _color_list_option(self, code: str, language: str) -> dict:
        prims = self._color_of(code).get("primary_colors") or []
        return {
            "code": code,
            "name": ", ".join(self._color_label(c, language) for c in prims),
            "colors": list(prims),
            "kind": "color_list",
        }

    def _number_option(self, n: int) -> dict:
        return {"code": str(n), "name": str(n), "kind": "number"}

    def _flag_option(self, code: str, language: str) -> dict:
        payload = self._target_payload(code, language)
        payload["kind"] = "flag"
        return payload

    def _pick_candidate(self, pool: list[str], candidate_pool: list[str], predicate=None) -> Optional[str]:
        """Pick a target preferring the unseen pool (round-robin coverage)."""
        candidates = [c for c in candidate_pool if predicate is None or predicate(c)]
        if not candidates:
            candidates = [c for c in pool if predicate is None or predicate(c)]
        return self._rng.choice(candidates) if candidates else None

    def _distractors(
        self,
        target: str,
        candidates: list[str],
        n: int,
        pool: list[str],
        used: Optional[set[str]] = None,
    ) -> list[str]:
        """n flags most similar to `target` among `candidates`, with a
        fallback fill from the whole pool so an answer always has 4 options."""
        used = used or set()
        candidate_set = set(candidates)
        chosen: list[str] = []
        for _, c in self._ranked_similar(target):
            if len(chosen) >= n:
                break
            if c == target or c in used or c not in candidate_set:
                continue
            chosen.append(c)
        if len(chosen) < n:
            rest = [
                c for c in pool
                if c not in chosen and c != target and c not in used
            ]
            self._rng.shuffle(rest)
            chosen.extend(rest[: n - len(chosen)])
        return chosen

    def _color_question(self, qtype, cfg, language, pool, candidate_pool) -> Optional[dict]:
        for _ in range(12):
            question = self._build_color_question(qtype, cfg, language, pool, candidate_pool)
            if question is not None:
                return question
        # fallback to a type that always has valid data
        for _ in range(12):
            question = self._build_color_question(
                "flag_to_dominant_color", cfg, language, pool, candidate_pool
            )
            if question is not None:
                return question
        return None

    def _build_color_question(self, qtype, cfg, language, pool, candidate_pool) -> Optional[dict]:
        if qtype == "flag_to_dominant_color":
            return self._q_dominant(cfg, language, pool, candidate_pool)
        if qtype == "flag_to_colors":
            return self._q_colors(cfg, language, pool, candidate_pool)
        if qtype == "flag_has_not_color":
            return self._q_has_not(cfg, language, pool, candidate_pool)
        if qtype == "flag_to_color_count":
            return self._q_count(cfg, language, pool, candidate_pool)
        if qtype == "flag_shared_colors":
            return self._q_shared(cfg, language, pool, candidate_pool)
        if qtype == "flag_to_stripes":
            return self._q_stripes(cfg, language, pool, candidate_pool)
        if qtype == "flag_to_monochrome":
            return self._q_mono(cfg, language, pool, candidate_pool)
        return None

    def _q_dominant(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        target = self._pick_candidate(pool, candidate_pool)
        if target is None:
            return None
        data = self._color_of(target)
        correct = data.get("dominant_color")
        if not correct:
            prims = data.get("primary_colors") or []
            correct = prims[0] if prims else None
        if correct is None:
            return None
        present = self._presence_set(target)
        others = [c for c in _COLOR_ORDER if c not in present and c != correct]
        self._rng.shuffle(others)
        dist = others[:3]
        if len(dist) < 3:
            rest = [c for c in _COLOR_ORDER if c != correct and c not in dist]
            self._rng.shuffle(rest)
            dist.extend(rest[: 3 - len(dist)])
        option_keys = [correct] + dist
        self._rng.shuffle(option_keys)
        name = self.names_for(language).get(target, target)
        return self._question_payload(
            qtype="flag_to_dominant_color",
            cfg=cfg, language=language, target_code=target,
            options=[self._color_option(k, language) for k in option_keys],
            correct_index=option_keys.index(correct),
            prompt=self._prompt("flag_to_dominant_color", language, name=name),
            prompt_colors=[],
        )

    def _q_colors(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        candidates = candidate_pool or pool
        target = self._pick_candidate(
            pool, candidates, lambda c: len(self._color_of(c).get("primary_colors") or []) >= 2
        )
        if target is None:
            return None
        correct = self._primary_set(target)
        if len(correct) < 2:
            return None
        count = len(correct)
        # one option per distinct primary set: flags sharing the exact same
        # palette would be indistinguishable options, so dedupe up front
        seen: set[frozenset[str]] = {correct}
        distinct: list[str] = []
        for c in candidates:
            s = self._primary_set(c)
            if c == target or s in seen:
                continue
            seen.add(s)
            distinct.append(c)
        preferred = [
            c for c in distinct
            if len(self._primary_set(c)) == count and self._primary_set(c) & correct
        ]
        dist = self._distractors(target, preferred, 3, distinct)
        if len(dist) < 3:
            rest = [c for c in distinct if c not in dist]
            self._rng.shuffle(rest)
            dist.extend(rest[: 3 - len(dist)])
        if len(dist) < 3:
            return None
        option_codes = [target] + dist
        self._rng.shuffle(option_codes)
        name = self.names_for(language).get(target, target)
        return self._question_payload(
            qtype="flag_to_colors",
            cfg=cfg, language=language, target_code=target,
            options=[self._color_list_option(c, language) for c in option_codes],
            correct_index=option_codes.index(target),
            prompt=self._prompt("flag_to_colors", language, name=name),
            prompt_colors=[],
        )

    def _q_has_not(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        counts = {
            c: sum(1 for f in pool if c in self._presence_set(f)) for c in _COLOR_ORDER
        }
        usable = [
            c for c in _COLOR_ORDER if counts[c] >= 25 and (len(pool) - counts[c]) >= 5
        ] or list(_COLOR_ORDER)
        self._rng.shuffle(usable)
        color, target = None, None
        for cand in usable:
            pick = self._pick_candidate(
                pool, candidate_pool, lambda c, cc=cand: cc not in self._presence_set(c)
            )
            if pick is not None:
                color, target = cand, pick
                break
        if color is None or target is None:
            return None
        has_it = [
            c for c in (candidate_pool or pool)
            if c != target and color in self._presence_set(c)
        ]
        dist = self._distractors(target, has_it, 3, pool)
        if len(dist) < 3:
            return None
        option_codes = [target] + dist
        self._rng.shuffle(option_codes)
        label = self._color_label(color, language)
        return self._question_payload(
            qtype="flag_has_not_color",
            cfg=cfg, language=language, target_code=target,
            options=[self._flag_option(c, language) for c in option_codes],
            correct_index=option_codes.index(target),
            prompt=self._prompt("flag_has_not_color", language, color=label),
            prompt_colors=[{"key": color, "name": label}],
        )

    def _q_count(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        target = self._pick_candidate(pool, candidate_pool)
        if target is None:
            return None
        n = self._color_of(target).get("color_count_primary") or len(self._primary_set(target))
        if n < 1:
            return None
        real = sorted({self._color_of(c).get("color_count_primary") or 0 for c in pool})
        cands = sorted((v for v in real if v != n), key=lambda v: abs(v - n))
        dist = cands[:3]
        if len(dist) < 3:
            extras = [v for v in range(1, 7) if v != n and v not in dist]
            self._rng.shuffle(extras)
            dist.extend(extras[: 3 - len(dist)])
        if len(dist) < 3:
            return None
        option_values = [n] + dist
        self._rng.shuffle(option_values)
        name = self.names_for(language).get(target, target)
        return self._question_payload(
            qtype="flag_to_color_count",
            cfg=cfg, language=language, target_code=target,
            options=[self._number_option(v) for v in option_values],
            correct_index=option_values.index(n),
            prompt=self._prompt("flag_to_color_count", language, name=name),
            prompt_colors=[],
        )

    def _q_shared(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        candidates = candidate_pool or pool
        for _ in range(8):
            ref = self._pick_candidate(
                pool, candidates, lambda c: len(self._primary_set(c)) >= 2
            )
            if ref is None:
                return None
            prims = self._color_of(ref).get("primary_colors") or []
            sp = self._color_of(ref).get("surface_percentages") or {}
            order = sorted(prims, key=lambda c: sp.get(c, 0), reverse=True)
            if len(order) < 2:
                continue
            c1, c2 = order[0], order[1]

            def both(c: str, a: str = c1, b: str = c2) -> bool:
                s = self._presence_set(c)
                return a in s and b in s

            shared = [c for c in candidates if c != ref and both(c)]
            if not shared:
                continue
            target = self._rng.choice(shared)
            one = [
                c for c in candidates
                if c != target and c != ref
                and (c1 in self._presence_set(c)) != (c2 in self._presence_set(c))
            ]
            dist = self._distractors(target, one, 3, pool)
            if len(dist) < 3:
                rest = [
                    c for c in candidates
                    if c != target and c != ref and not both(c) and c not in dist
                ]
                self._rng.shuffle(rest)
                dist.extend(rest[: 3 - len(dist)])
            if len(dist) < 3:
                continue
            option_codes = [target] + dist
            self._rng.shuffle(option_codes)
            ref_name = self.names_for(language).get(ref, ref)
            label1 = self._color_label(c1, language)
            label2 = self._color_label(c2, language)
            return self._question_payload(
                qtype="flag_shared_colors",
                cfg=cfg, language=language, target_code=ref,
                options=[self._flag_option(c, language) for c in option_codes],
                correct_index=option_codes.index(target),
                prompt=self._prompt(
                    "flag_shared_colors", language, name=ref_name, c1=label1, c2=label2
                ),
                prompt_colors=[
                    {"key": c1, "name": label1},
                    {"key": c2, "name": label2},
                ],
            )
        return None

    def _q_stripes(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        def orientation(code: str) -> Optional[float]:
            return self._geom_of(code).get("orientation_code", {}).get("value")

        hor = [c for c in pool if orientation(c) == 1.0]
        ver = [c for c in pool if orientation(c) == 2.0]
        groups: list[list[str]] = []
        if hor and ver:
            groups = [hor, ver]
            self._rng.shuffle(groups)
        elif hor:
            groups = [hor]
        elif ver:
            groups = [ver]
        else:
            return None
        for group in groups:
            target = self._pick_candidate(pool, candidate_pool, lambda c, gg=group: c in gg)
            if target is None:
                continue
            horizontal = orientation(target) == 1.0
            opposite = ver if horizontal else hor
            opp = [
                c for c in (candidate_pool or pool)
                if c in opposite and c != target
            ]
            dist = self._distractors(target, opp, 3, pool)
            if len(dist) < 3:
                continue
            option_codes = [target] + dist
            self._rng.shuffle(option_codes)
            orient = "orizzontali" if horizontal else "verticali"
            if language == "en":
                orient = "horizontal" if horizontal else "vertical"
            return self._question_payload(
                qtype="flag_to_stripes",
                cfg=cfg, language=language, target_code=target,
                options=[self._flag_option(c, language) for c in option_codes],
                correct_index=option_codes.index(target),
                prompt=self._prompt("flag_to_stripes", language, orient=orient),
                prompt_colors=[],
            )
        return None

    def _q_mono(self, cfg, language, pool, candidate_pool) -> Optional[dict]:
        mono = [c for c in pool if (self._color_of(c).get("color_count_primary") or 0) == 1]
        target = self._pick_candidate(pool, candidate_pool, lambda c: c in mono)
        if target is None:
            return None
        multi = [
            c for c in (candidate_pool or pool)
            if (self._color_of(c).get("color_count_primary") or 0) >= 2
        ]
        dist = self._distractors(target, multi, 3, pool)
        if len(dist) < 3:
            return None
        option_codes = [target] + dist
        self._rng.shuffle(option_codes)
        return self._question_payload(
            qtype="flag_to_monochrome",
            cfg=cfg, language=language, target_code=target,
            options=[self._flag_option(c, language) for c in option_codes],
            correct_index=option_codes.index(target),
            prompt=self._prompt("flag_to_monochrome", language),
            prompt_colors=[],
        )

    def answer(self, question: dict, selected_index: int) -> dict:
        """Validate an answer; returns the correct flag code."""
        correct_index = question["correct_index"]
        correct = question["options"][correct_index]["code"]
        selected = question["options"][selected_index]["code"]
        return {
            "correct": selected_index == correct_index,
            "selected_code": selected,
            "correct_code": correct,
        }

    def _target_payload(self, code: str, language: str = DEFAULT_LANGUAGE) -> dict:
        # Every code in the index has a local asset (the index is built by
        # globbing the PNG cache), so the URL is deterministic: no need to
        # open the image to answer a question.
        return {
            "code": code,
            "name": self.names_for(language).get(code, code),
            "asset_url": f"{self._assets.url_prefix}/{code}/asset",
        }
