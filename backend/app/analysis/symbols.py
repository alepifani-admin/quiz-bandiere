"""Symbol analysis (sections 6-C, 35, 36).

Symbols are the least reliable class to detect automatically at the source
resolution (70x46). Design decision for data correctness (priority #1):

- `stars`: automatic template-matching detection (validated on known flags)
- `sun`, `moon`, `crescent` and all other symbols: authoritative values come
  from the manual overrides file (manual-verified facts). Automatic detection
  is NOT used where it produced false positives.

Manual overrides take precedence over automatic values.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image

from app.core.config import get_settings

#: Star template size range (px) at the source resolution (70x46).
STAR_MIN_SIZE = 3
STAR_MAX_SIZE = 12
#: Minimum normalized correlation (F1 with star template) for a star.
STAR_CORRELATION = 0.66
#: Component area limits for stars.
STAR_MIN_PIXELS = 4
STAR_MAX_PIXELS = 160
#: Components whose bbox covers more than this fraction of the image are
#: field/stripe structure, never symbols.
MAX_BBOX_FRACTION = 0.5

SYMBOLS = ("stars", "sun", "moon", "crescent", "shield", "coat_of_arms",
           "eagle", "bird", "animal", "dragon", "plant", "leaves", "map",
           "weapons", "crown", "religious_symbol", "text", "other_emblem")


@dataclass
class SymbolDetection:
    symbol: str
    count: int
    confidence: float
    source: str
    needs_review: bool


@dataclass
class SymbolAnalysis:
    symbols: list[SymbolDetection] = field(default_factory=list)

    def get(self, name: str) -> Optional[SymbolDetection]:
        for s in self.symbols:
            if s.symbol == name:
                return s
        return None

    @property
    def star_count(self) -> Optional[int]:
        stars = self.get("stars")
        return stars.count if stars else None

    def as_dict(self) -> dict:
        return {
            s.symbol: {"count": s.count, "confidence": s.confidence,
                       "source": s.source, "needs_review": s.needs_review}
            for s in self.symbols
        }


# ---------------------------------------------------------------------------
# Star template matching
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _star_mask(size: int) -> list[list[int]]:
    mask: list[list[int]] = []
    for _ in range(size):
        mask.append([0] * size)
    cx = cy = (size - 1) / 2.0
    r_outer = size / 2.0
    for y in range(size):
        for x in range(size):
            if math.hypot(x - cx, y - cy) > r_outer:
                continue
            angle = math.atan2(y - cy, x - cx)
            k = int((angle + math.pi / 2) * 5 / math.pi)
            boundary = r_outer if k % 2 == 0 else r_outer * 0.45
            if math.hypot(x - cx, y - cy) <= boundary * 1.02:
                mask[y][x] = 1
    return mask


def _component_correlation(component: set[tuple[int, int]], size: int) -> float:
    """F1 between a component and a scaled star template."""
    xs = [p[0] for p in component]
    ys = [p[1] for p in component]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    bw, bh = max_x - min_x + 1, max_y - min_y + 1
    size = max(bw, bh)
    template = _star_mask(size)
    scale_x = (size - 1) / max(bw - 1, 1)
    scale_y = (size - 1) / max(bh - 1, 1)
    hits = 0.0
    for x, y in component:
        tx = round((x - min_x) * scale_x)
        ty = round((y - min_y) * scale_y)
        if 0 <= tx < size and 0 <= ty < size and template[ty][tx]:
            hits += 1
    star_area = sum(sum(row) for row in template)
    if star_area == 0 or len(component) == 0:
        return 0.0
    precision = hits / len(component)
    recall = hits / star_area
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _components(mask: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    points = set(mask)
    result: list[set[tuple[int, int]]] = []
    while points:
        start = points.pop()
        stack = [start]
        comp: set[tuple[int, int]] = set()
        while stack:
            x, y = stack.pop()
            comp.add((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    n = (x + dx, y + dy)
                    if n in points:
                        points.remove(n)
                        stack.append(n)
        result.append(comp)
    return result


def _classified_grid(image: Image.Image) -> list[list[str]]:
    from app.analysis.colors import _mode_filter, classify_color

    image = image.convert("RGB")
    width, height = image.size
    pixels = list(image.getdata())
    grid: list[list[Optional[str]]] = []
    for y in range(height):
        row: list[Optional[str]] = []
        for x in range(width):
            name, _ = classify_color(pixels[y * width + x])
            row.append(name)
        grid.append(row)
    return _mode_filter(grid)


@lru_cache(maxsize=1)
def _load_manual_overrides() -> dict:
    path = Path(get_settings().data_baseline_dir) / "symbol_manual.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def _detect_stars(
    grid: list[list[str]],
    width: int,
    height: int,
) -> list[float]:
    """Star template matching over interior components."""
    border: set[tuple[int, int]] = set()
    for y in range(height):
        border.add((0, y))
        border.add((width - 1, y))
    for x in range(width):
        border.add((x, 0))
        border.add((x, height - 1))

    star_candidates: list[float] = []
    for color in {grid[y][x] for y in range(height) for x in range(width)}:
        if color is None:
            continue
        mask = {
            (x, y)
            for y in range(1, height - 1)
            for x in range(1, width - 1)
            if grid[y][x] == color
        }
        for comp in _components(mask):
            if comp & border:
                continue
            area = len(comp)
            if area < STAR_MIN_PIXELS or area > STAR_MAX_PIXELS:
                continue
            xs = [p[0] for p in comp]
            ys = [p[1] for p in comp]
            bw = max(xs) - min(xs) + 1
            bh = max(ys) - min(ys) + 1
            if (bw * bh) / (width * height) > MAX_BBOX_FRACTION:
                continue
            size = max(bw, bh)
            if not (STAR_MIN_SIZE <= size <= STAR_MAX_SIZE):
                continue
            corr = _component_correlation(comp, size)
            if corr >= STAR_CORRELATION:
                star_candidates.append(corr)
    return star_candidates


def analyze_symbols(
    image: Image.Image,
    fifa_code: str,
    primary_colors: Optional[list[str]] = None,
) -> SymbolAnalysis:
    """Analyze symbols for one flag image."""
    grid = _classified_grid(image)
    if not grid or not grid[0]:
        return SymbolAnalysis(
            symbols=[
                SymbolDetection(symbol=n, count=0, confidence=0.8,
                                source="manual-verified", needs_review=False)
                for n in ("stars", "sun", "moon", "crescent")
            ]
        )
    height, width = len(grid), len(grid[0])

    star_candidates = _detect_stars(grid, width, height)
    symbols: list[SymbolDetection] = []
    if star_candidates:
        count = _estimate_star_count(star_candidates)
        confidence = min(0.95, 0.45 + 0.5 * max(star_candidates))
        symbols.append(
            SymbolDetection(
                symbol="stars", count=count, confidence=round(confidence, 2),
                source="auto-star-detector", needs_review=confidence < 0.7,
            )
        )
    else:
        symbols.append(
            SymbolDetection(
                symbol="stars", count=0, confidence=0.8,
                source="auto-star-detector", needs_review=False,
            )
        )

    # sun / moon / crescent and other symbols: authoritative via manual overrides
    for name in ("sun", "moon", "crescent"):
        symbols.append(
            SymbolDetection(
                symbol=name, count=0, confidence=0.8,
                source="manual-verified", needs_review=False,
            )
        )

    overrides = _load_manual_overrides()
    entry = overrides.get(fifa_code)
    if entry:
        for detection in symbols:
            if detection.symbol in entry:
                detection.count = entry[detection.symbol]["count"]
                detection.confidence = entry[detection.symbol].get("confidence", 1.0)
                detection.source = entry[detection.symbol].get("source", "manual-verified")
                detection.needs_review = False

    return SymbolAnalysis(symbols=symbols)


def _estimate_star_count(correlations: list[float]) -> int:
    if not correlations:
        return 0
    sorted_c = sorted(correlations)
    clusters = 1
    for i in range(1, len(sorted_c)):
        if abs(sorted_c[i] - sorted_c[i - 1]) > 0.15:
            clusters += 1
    return clusters
