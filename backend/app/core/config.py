"""Application configuration.

All values can be overridden via environment variables with the FIFAQUIZ_ prefix.
Runtime config is the single source of truth for paths, limits and weights.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# In the packaged desktop app the Python files live inside the PyInstaller
# bundle (sys._MEIPASS); in the web version they live at <repo>/backend/...
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
else:
    REPO_ROOT = Path(__file__).resolve().parents[3]


class SimilarityWeights(BaseModel):
    """Configurable weights for the Flag Similarity Score (section 15)."""

    color: float = 0.30
    color_count: float = 0.20
    structure: float = 0.15
    geometry: float = 0.15
    symbol: float = 0.10
    orientation: float = 0.10

    def total(self) -> float:
        return (
            self.color
            + self.color_count
            + self.structure
            + self.geometry
            + self.symbol
            + self.orientation
        )


class DifficultyLevel(BaseModel):
    """Quiz difficulty profile (levels 1-7)."""

    level: int
    questions: int
    flag_to_name_weight: float  # 1.0 = only flag->name; 0.0 = only name->flag
    min_similarity: float  # distractors must score at least this (0 = any)
    max_similarity: float = 1.0  # upper bound for distractor similarity
    color_questions_weight: float = 0.0  # share of color questions (world quiz only)
    confederations: tuple[str, ...] = (
        "UEFA", "CONMEBOL", "CONCACAF", "AFC", "CAF", "OFC",
    )


#: Difficulty 1-7 profiles. Higher levels: more questions, more name->flag
#: questions, harder (more similar) distractors, wider confederation pool.
#: flag_to_name_weight is always strictly between 0 and 1 so every level
#: offers a mix of both question types.
#: Question counts are the original values increased by 20%.
DIFFICULTY_LEVELS: tuple[DifficultyLevel, ...] = (
    DifficultyLevel(
        level=1, questions=10, flag_to_name_weight=0.8, min_similarity=0.0,
        max_similarity=0.35, color_questions_weight=0.4,
        confederations=("UEFA", "CONMEBOL"),
    ),
    DifficultyLevel(
        level=2, questions=12, flag_to_name_weight=0.7, min_similarity=0.0,
        max_similarity=0.45, color_questions_weight=0.4,
        confederations=("UEFA", "CONMEBOL", "CONCACAF"),
    ),
    DifficultyLevel(
        level=3, questions=12, flag_to_name_weight=0.6, min_similarity=0.15,
        max_similarity=0.6, color_questions_weight=0.4,
        confederations=("UEFA", "CONMEBOL", "CONCACAF", "AFC"),
    ),
    DifficultyLevel(
        level=4, questions=14, flag_to_name_weight=0.5, min_similarity=0.3,
        max_similarity=0.75, color_questions_weight=0.55,
        confederations=("UEFA", "CONMEBOL", "CONCACAF", "AFC", "CAF"),
    ),
    DifficultyLevel(
        level=5, questions=14, flag_to_name_weight=0.4, min_similarity=0.45,
        max_similarity=0.9, color_questions_weight=0.55,
        confederations=("UEFA", "CONMEBOL", "CONCACAF", "AFC", "CAF", "OFC"),
    ),
    DifficultyLevel(
        level=6, questions=18, flag_to_name_weight=0.3, min_similarity=0.55,
        max_similarity=1.0, color_questions_weight=0.65,
        confederations=("UEFA", "CONMEBOL", "CONCACAF", "AFC", "CAF", "OFC"),
    ),
    DifficultyLevel(
        level=7, questions=18, flag_to_name_weight=0.2, min_similarity=0.65,
        max_similarity=1.0, color_questions_weight=0.65,
        confederations=("UEFA", "CONMEBOL", "CONCACAF", "AFC", "CAF", "OFC"),
    ),
)


def difficulty_config(level: int) -> DifficultyLevel:
    """Return the profile for a 1-7 difficulty (defaults to level 4)."""
    if not 1 <= level <= len(DIFFICULTY_LEVELS):
        raise ValueError(f"difficulty must be 1-{len(DIFFICULTY_LEVELS)}")
    return DIFFICULTY_LEVELS[level - 1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIFAQUIZ_", env_file=".env", extra="ignore")

    # Backend
    db_path: str = str(REPO_ROOT / "data" / "fifa_quiz.db")
    default_language: str = "it"
    debug: bool = False
    admin_enabled: bool = True
    admin_token: str = "change-me"

    # Data update layer
    data_cache_dir: str = str(REPO_ROOT / "data" / "cache")
    data_baseline_dir: str = str(REPO_ROOT / "data" / "baseline")
    leaderboard_file: str = str(REPO_ROOT / "data" / "leaderboard.json")
    update_timeout_seconds: int = 30
    expected_associations: int = 211

    # Quiz engine
    similarity_weights: SimilarityWeights = SimilarityWeights()

    # Security / rate limiting
    rate_limit_per_minute: int = 60

    # Similarity precompute batch size
    similarity_batch_size: int = 100

    @property
    def db_file(self) -> Path:
        return Path(self.db_path)

    @property
    def cache_dir(self) -> Path:
        return Path(self.data_cache_dir)

    @property
    def baseline_dir(self) -> Path:
        return Path(self.data_baseline_dir)

    @property
    def leaderboard(self) -> Path:
        return Path(self.leaderboard_file)


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (FastAPI dependency)."""
    env_file = os.environ.get("FIFAQUIZ_ENV_FILE")
    if env_file:
        return Settings(_env_file=env_file)
    return Settings()
