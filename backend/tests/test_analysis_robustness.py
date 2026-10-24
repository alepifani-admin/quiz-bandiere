"""Robustness tests for the image analysis layer (bad inputs + cache I/O)."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.analysis import cache as analysis_cache
from app.analysis.analyzer import analyze_flag


@pytest.mark.parametrize("size", [(0, 0), (1, 1), (1, 10), (10, 1)])
def test_analyze_flag_rejects_tiny_images(size: tuple[int, int]) -> None:
    """Degenerate images must raise a clean ValueError, not ZeroDivisionError."""
    with pytest.raises(ValueError):
        analyze_flag(Image.new("RGB", size), "ITA")


def test_analysis_cache_creates_dir_and_writes_atomically(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "analysis.json"
    path = analysis_cache.save({"ITA": {"ok": True}}, name=str(target))
    assert path == target
    assert target.exists()
    assert not list(target.parent.glob("*.tmp"))
    assert analysis_cache.load_cached(str(target)) == {"ITA": {"ok": True}}


def test_analysis_cache_rejects_non_object(tmp_path: Path) -> None:
    target = tmp_path / "analysis.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    assert analysis_cache.load_cached(str(target)) is None
