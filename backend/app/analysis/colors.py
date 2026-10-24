"""Color analysis (sections 5, 6-A, 36).

Pixel-based classification into the 11 normalized human color categories
using CIELAB nearest-anchor. Technical RGB is preserved as metadata.
Anti-aliasing fringe is removed via connected-component filtering.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

#: Normalized human color categories with reference RGB anchors (section 5).
#: Multiple sub-anchors widen each category's capture basin so that shades
#: (azzurro, crimson, gold, ...) map to the right human category.
COLOR_ANCHORS: dict[str, list[tuple[int, int, int]]] = {
    "WHITE": [(255, 255, 255), (245, 245, 245)],
    "RED": [(200, 16, 46), (255, 0, 0), (165, 10, 20), (139, 0, 0), (128, 20, 30)],
    "BLUE": [(0, 38, 100), (0, 60, 130), (0, 90, 160), (0, 158, 224), (30, 50, 120), (5, 50, 165)],
    "GREEN": [(0, 106, 78), (0, 130, 80), (0, 170, 110), (34, 94, 36)],
    "YELLOW": [(255, 209, 0), (250, 200, 60), (240, 180, 0), (252, 209, 22)],
    "BLACK": [(0, 0, 0), (25, 25, 30)],
    "ORANGE": [(255, 122, 0), (230, 150, 30), (255, 140, 0)],
    "PURPLE": [(102, 16, 99), (90, 60, 140), (128, 0, 128)],
    "BROWN": [(88, 47, 21), (150, 100, 60), (125, 60, 30)],
    "GREY": [(120, 120, 120), (160, 160, 160), (90, 90, 95)],
    "PINK": [(233, 127, 156), (255, 180, 190), (255, 150, 170)],
}

COLOR_NAMES = tuple(COLOR_ANCHORS.keys())

#: Minimum surface % for a color to count as a primary (field) color.
PRIMARY_MIN_PERCENT = 5.0
#: Minimum connected-component size (px) to keep a color (fringe removal).
MIN_COMPONENT_PIXELS = 6
#: Minimum total surface % to keep a color at all.
MIN_TOTAL_PERCENT = 0.5
#: Pixels whose CIELAB distance to any anchor exceeds this are transition
#: (anti-aliasing) pixels and are excluded from the analysis.
MAX_LAB_DISTANCE = 55.0


def srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def _f(t: float) -> float:
        t = t / 255.0
        return t / 12.92 if t <= 0.04045 else ((t + 0.055) / 1.055) ** 2.4

    r, g, b = _f(rgb[0]), _f(rgb[1]), _f(rgb[2])
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505

    def _g(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    x, y, z = _g(x), _g(y), _g(z)
    return (116 * y - 16, 500 * (x - y), 200 * (y - z))


_ANCHOR_LAB = {
    (cat, i): srgb_to_lab(rgb)
    for cat, rgbs in COLOR_ANCHORS.items()
    for i, rgb in enumerate(rgbs)
}


def classify_color(rgb: tuple[int, int, int]) -> tuple[Optional[str], float]:
    """Map an RGB pixel to the nearest human color category.

    Returns (color_name, lab_distance). Distance > MAX_LAB_DISTANCE marks a
    transition (anti-aliasing) pixel -> category is None.
    """
    lab = srgb_to_lab(rgb)
    best_cat, best_i, best_dist = None, None, math.inf
    for (cat, i), anchor in _ANCHOR_LAB.items():
        dist = math.dist(lab, anchor)
        if dist < best_dist:
            best_cat, best_i, best_dist = cat, i, dist
    if best_dist > MAX_LAB_DISTANCE:
        return None, best_dist
    return best_cat, best_dist


def _connected_components(mask: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """8-connected components for a set of pixel coordinates."""
    points = set(mask)
    components: list[list[tuple[int, int]]] = []
    while points:
        start = points.pop()
        stack = [start]
        comp: list[tuple[int, int]] = []
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in points:
                        points.remove(neighbor)
                        stack.append(neighbor)
        components.append(comp)
    return components


@dataclass
class ColorAnalysis:
    primary_colors: list[str] = field(default_factory=list)
    all_colors: list[str] = field(default_factory=list)
    color_count_primary: int = 0
    color_count_emblem_inclusive: int = 0
    dominant_color: Optional[str] = None
    surface_percentages: dict[str, float] = field(default_factory=dict)
    presence: dict[str, bool] = field(default_factory=dict)
    technical_rgb: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "primary_colors": self.primary_colors,
            "all_colors": self.all_colors,
            "color_count_primary": self.color_count_primary,
            "color_count_emblem_inclusive": self.color_count_emblem_inclusive,
            "dominant_color": self.dominant_color,
            "surface_percentages": self.surface_percentages,
            "presence": self.presence,
            "technical_rgb": {k: list(v) for k, v in self.technical_rgb.items()},
        }


def _mode_filter(
    grid: list[list[Optional[str]]],
) -> list[list[Optional[str]]]:
    """3x3 modal filter over the classified grid.

    Anti-aliasing fringe between two regions forms 1px-thin lines; a modal
    filter replaces them with the surrounding dominant color while preserving
    real features that are >= 2px thick.
    """
    height, width = len(grid), len(grid[0])
    if height == 0 or width == 0:
        return []
    out: list[list[Optional[str]]] = [[None] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            window: list[Optional[str]] = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width:
                        window.append(grid[ny][nx])
            valid = [w for w in window if w is not None]
            if valid:
                out[y][x] = Counter(valid).most_common(1)[0][0]
    return out


def analyze_colors(
    image: Image.Image,
    *,
    content_bbox: Optional[tuple[int, int, int, int]] = None,
) -> ColorAnalysis:
    """Analyze an RGB image. `content_bbox` limits the analyzed region
    (used for non-rectangular flags to exclude padding)."""
    image = image.convert("RGB")
    if content_bbox:
        image = image.crop(content_bbox)
    width, height = image.size
    pixels = list(image.getdata())

    classified: list[Optional[str]] = []
    for rgb in pixels:
        name, _ = classify_color(rgb)
        classified.append(name)

    # Build the classified grid, apply mode filter, then flatten
    grid: list[list[Optional[str]]] = []
    for y in range(height):
        row = classified[y * width : (y + 1) * width]
        grid.append(row)
    filtered = _mode_filter(grid)
    flat: list[Optional[str]] = [cell for row in filtered for cell in row]

    # Build per-color pixel masks and connected components
    color_mask: dict[str, set[tuple[int, int]]] = {}
    index = 0
    for y in range(height):
        for x in range(width):
            name = flat[index]
            if name is not None:
                color_mask.setdefault(name, set()).add((x, y))
            index += 1

    components_by_color: dict[str, list[list[tuple[int, int]]]] = {}
    for name, mask in color_mask.items():
        components_by_color[name] = _connected_components(mask)

    # Filter fringe: drop colors whose every component is tiny
    kept_px_total = sum(len(mask) for mask in color_mask.values())
    total = max(width * height, 1)
    kept: set[str] = set()
    for name, components in components_by_color.items():
        total_px = len(color_mask[name])
        percent = 100.0 * total_px / total
        if percent < MIN_TOTAL_PERCENT:
            continue
        has_big_component = any(len(c) >= MIN_COMPONENT_PIXELS for c in components)
        if has_big_component or percent >= PRIMARY_MIN_PERCENT:
            kept.add(name)

    # Deterministic order (count desc, then color name) so the dominant
    # color and the primary/all lists never depend on set iteration order.
    ordered = sorted(kept, key=lambda c: (-len(color_mask[c]), c))

    # Surface percentages over kept colors
    kept_px = sum(len(color_mask[c]) for c in ordered) or 1
    percentages: dict[str, float] = {}
    for name in ordered:
        percentages[name] = round(100.0 * len(color_mask[name]) / kept_px, 2)

    # Dominant color
    dominant = ordered[0] if ordered else None

    # Primary colors: touch the image border OR are large (>= PRIMARY_MIN_PERCENT)
    border_pixels = set()
    for y in range(height):
        border_pixels.add((0, y))
        border_pixels.add((width - 1, y))
    for x in range(width):
        border_pixels.add((x, 0))
        border_pixels.add((x, height - 1))

    primary: set[str] = set()
    for name in kept:
        mask = color_mask[name]
        if mask & border_pixels or percentages[name] >= PRIMARY_MIN_PERCENT:
            primary.add(name)

    # Representative technical RGB per color
    technical: dict[str, tuple[int, int, int]] = {}
    counter_per_color: dict[str, Counter] = {}
    for rgb, name in zip(pixels, flat):
        if name is not None and name in kept:
            counter_per_color.setdefault(name, Counter())[rgb] += 1
    for name in ordered:
        technical[name] = counter_per_color[name].most_common(1)[0][0]

    primary_ordered = [c for c in ordered if c in primary]
    emblem_ordered = [c for c in ordered if c not in primary]

    return ColorAnalysis(
        primary_colors=primary_ordered,
        all_colors=ordered,
        color_count_primary=len(primary_ordered),
        color_count_emblem_inclusive=len(ordered),
        dominant_color=dominant,
        surface_percentages=percentages,
        presence={name: name in kept for name in COLOR_NAMES},
        technical_rgb=technical,
    )
