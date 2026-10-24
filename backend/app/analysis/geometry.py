"""Geometry analysis (section 6-B).

Pixel-based structural detection on the color-classified grid: stripes,
crosses (incl. Nordic), canton, central emblem, border, triangle, chevron,
symmetry and field color. Every feature carries a confidence; uncertain
detections are flagged needs_review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PIL import Image


@dataclass
class GeometryFeature:
    feature: str
    value: float
    confidence: float
    needs_review: bool = False
    label: Optional[str] = None

    def as_dict(self) -> dict:
        data = {
            "value": self.value,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
        }
        if self.label is not None:
            data["label"] = self.label
        return data


@dataclass
class GeometryAnalysis:
    features: list[GeometryFeature] = field(default_factory=list)

    def get(self, name: str) -> Optional[GeometryFeature]:
        for f in self.features:
            if f.feature == name:
                return f
        return None

    def value_of(self, name: str) -> float:
        f = self.get(name)
        return f.value if f else 0.0

    def as_dict(self) -> dict:
        return {f.feature: f.as_dict() for f in self.features}


#: Central disc radius fraction (of min dimension) for emblem detection.
CENTER_RADIUS = 0.22


#: Reference grid size for geometry detection. Detection thresholds were
#: validated at this resolution; high-resolution assets are downscaled to it
#: so results are stable regardless of the source image size.
GRID_REFERENCE = (70, 46)


def _grid_of(image: Image.Image) -> list[list[str]]:
    from app.analysis.colors import _mode_filter, classify_color

    image = image.convert("RGB")
    if image.size[0] > GRID_REFERENCE[0] or image.size[1] > GRID_REFERENCE[1]:
        image = image.resize(GRID_REFERENCE)
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


def analyze_geometry(image: Image.Image) -> GeometryAnalysis:
    grid = _grid_of(image)
    height, width = len(grid), len(grid[0])
    features: list[GeometryFeature] = []

    def add(feature: str, value: float, confidence: float) -> None:
        features.append(
            GeometryFeature(
                feature=feature,
                value=value,
                confidence=confidence,
                needs_review=value != 0 and confidence <= 0.7,
            )
        )

    # ---- cross / nordic cross (before stripes: a cross flag is not striped) --
    cross, nordic = _detect_cross(grid)
    add("cross", 1.0 if cross else 0.0, 0.85)
    add("nordic_cross", 1.0 if nordic else 0.0, 0.85)

    # ---- stripes / orientation -------------------------------------------
    uniform_rows = _uniformity_ratio(grid, horizontal=True)
    uniform_cols = _uniformity_ratio(grid, horizontal=False)
    h_bands = _horizontal_band_count(grid)
    v_bands = _vertical_band_count(grid)

    horizontal_striped = (
        not cross
        and uniform_rows >= 0.85
        and h_bands >= 2
        and _band_color_coverage(grid, _line_dominant(grid, True)) >= 0.15
    )
    vertical_striped = (
        not cross
        and uniform_cols >= 0.85
        and v_bands >= 2
        and _band_color_coverage(grid, _line_dominant(grid, False)) >= 0.15
    )

    if horizontal_striped:
        add("horizontal_stripes", h_bands, min(0.95, 0.5 + uniform_rows))
        add("vertical_stripes", 0, 0.8)
        orientation_code = 1.0
    elif vertical_striped:
        add("horizontal_stripes", 0, 0.8)
        add("vertical_stripes", v_bands, min(0.95, 0.5 + uniform_cols))
        orientation_code = 2.0
    else:
        add("horizontal_stripes", 0, 0.8)
        add("vertical_stripes", 0, 0.8)
        orientation_code = 3.0

    add("orientation_code", orientation_code, 0.95)

    # ---- canton -----------------------------------------------------------
    canton, canton_color = _detect_canton(grid)
    add("canton", 1.0 if canton else 0.0, 0.85)
    features.append(
        GeometryFeature(feature="canton_color", value=0.0, confidence=0.9,
                        needs_review=False, label=canton_color)
    )

    # ---- central emblem / circle ------------------------------------------
    central, disc = _detect_central_disc(grid)
    add("central_circle", 1.0 if disc else 0.0, 0.7)
    add("central_emblem", 1.0 if central else 0.0, 0.7)

    # ---- border / triangle / symmetry ---------------------------------------
    add("border", 1.0 if _detect_border(grid) else 0.0, 0.7)
    add("triangle", 1.0 if _detect_triangle(grid) else 0.0, 0.7)
    # chevron auto-detection proved unreliable at source resolution (many
    # symmetric flags false-positive); values come from manual verification.
    add("chevron", 0.0, 0.6)
    add("symmetric_left_right", _symmetry_score(grid, vertical=True), 0.8)
    add("symmetric_top_bottom", _symmetry_score(grid, vertical=False), 0.8)

    # ---- field color (best-effort, informational only) -----------------------
    features.append(
        GeometryFeature(feature="field_color", value=0.0, confidence=0.6,
                        needs_review=False, label=_field_color(grid))
    )

    return GeometryAnalysis(features=features)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uniformity_ratio(grid: list[list[str]], horizontal: bool) -> float:
    """Fraction of lines whose dominant color covers >= 0.75 of the line."""
    height, width = len(grid), len(grid[0])
    uniform = 0
    total = height if horizontal else width
    for i in range(total):
        counts: dict[str, int] = {}
        for j in range(width if horizontal else height):
            c = grid[i][j] if horizontal else grid[j][i]
            if c is not None:
                counts[c] = counts.get(c, 0) + 1
        line_len = sum(counts.values())
        if line_len == 0:
            continue
        dominant, count = max(counts.items(), key=lambda kv: kv[1])
        if count / line_len >= 0.75:
            uniform += 1
    return uniform / max(total, 1)


def _line_dominant(grid: list[list[str]], horizontal: bool) -> list[Optional[str]]:
    """Dominant color per line (row when horizontal else column)."""
    height, width = len(grid), len(grid[0])
    seq: list[Optional[str]] = []
    total = height if horizontal else width
    for i in range(total):
        counts: dict[str, int] = {}
        for j in range(width if horizontal else height):
            c = grid[i][j] if horizontal else grid[j][i]
            if c is not None:
                counts[c] = counts.get(c, 0) + 1
        seq.append(max(counts, key=counts.get) if counts else None)
    return seq


def _horizontal_band_count(grid: list[list[str]]) -> int:
    return _count_transitions(_line_dominant(grid, horizontal=True))


def _vertical_band_count(grid: list[list[str]]) -> int:
    return _count_transitions(_line_dominant(grid, horizontal=False))


def _count_transitions(seq: list[Optional[str]]) -> int:
    bands = 0
    previous = None
    for c in seq:
        if c != previous:
            bands += 1
            previous = c
    return bands


def _band_color_coverage(grid: list[list[str]], seq: list[Optional[str]]) -> float:
    """Smallest total image coverage among the colors forming the bands."""
    colors = {c for c in seq if c is not None}
    counts: dict[str, int] = {}
    for row in grid:
        for c in row:
            if c is not None:
                counts[c] = counts.get(c, 0) + 1
    total = sum(counts.values())
    if not colors or not total:
        return 0.0
    return min(counts.get(c, 0) / total for c in colors)


def _coverage_profile(
    grid: list[list[str]], color: Optional[str]
) -> tuple[list[float], list[float]]:
    """Per-row and per-column coverage fraction of a color (0..1)."""
    height, width = len(grid), len(grid[0])
    row_frac = [0.0] * height
    col_frac = [0.0] * width
    for y in range(height):
        for x in range(width):
            if grid[y][x] == color:
                row_frac[y] += 1.0
                col_frac[x] += 1.0
    for i in range(height):
        row_frac[i] /= width
    for i in range(width):
        col_frac[i] /= height
    return row_frac, col_frac


def _detect_cross(grid: list[list[str]]) -> tuple[bool, bool]:
    """Detect a cross (Switzerland, Tonga, Dominican Rep.) and a Nordic cross
    (offset to the hoist: Sweden, Denmark, ...). Two signatures are matched:
    full crosses that span the flag edges, and compact Greek crosses that do
    not reach the edges (e.g. Switzerland)."""
    colors = sorted({c for row in grid for c in row if c is not None})
    profiles = {c: _coverage_profile(grid, c) for c in colors}

    # pass 1: full / Nordic crosses (arms span the whole flag)
    for color, (row_frac, col_frac) in profiles.items():
        max_row = max(row_frac)
        max_col = max(col_frac)
        if max_row < 0.85 or max_col < 0.85:
            continue
        avg_row = sum(row_frac) / len(row_frac)
        avg_col = sum(col_frac) / len(col_frac)
        if avg_row > 0.5 and avg_col > 0.5:
            continue  # solid field, not a cross (e.g. Brazil's green)
        nordic = _nordic_offset(col_frac, 0.85)
        return True, nordic

    # pass 2: compact Greek crosses (arms centered, not reaching the edges)
    height, width = len(grid), len(grid[0])
    for color, (row_frac, col_frac) in profiles.items():
        if not row_frac or not col_frac:
            continue
        center_row = row_frac[height // 2]
        center_col = col_frac[width // 2]
        if center_row < 0.25 or center_col < 0.25:
            continue
        if max(row_frac) > 0.9 or max(col_frac) > 0.9:
            continue  # a full band (stripe), not a cross
        avg_row = sum(row_frac) / len(row_frac)
        avg_col = sum(col_frac) / len(col_frac)
        if avg_row > 0.5 or avg_col > 0.5:
            continue
        bbox = _color_bbox(grid, color)
        if bbox is None:
            continue
        min_x, min_y, max_x, max_y = bbox
        bw = max_x - min_x + 1
        bh = max_y - min_y + 1
        if bw >= 0.8 * width or bh >= 0.8 * height:
            continue  # reaches the edges (would be a full cross)
        area = bw * bh
        if area <= 0:
            continue
        counts = sum(1 for row in grid for c in row if c == color)
        if counts / area <= 0.65:
            return True, False
    return False, False


def _nordic_offset(col_frac: list[float], threshold: float) -> bool:
    """Nordic crosses sit within the hoist half (left ~42% of the width)."""
    cols = [x for x, f in enumerate(col_frac) if f >= threshold]
    if not cols:
        return False
    return (min(cols) + max(cols)) / 2 / len(col_frac) < 0.42


def _detect_canton(grid: list[list[str]]) -> tuple[bool, Optional[str]]:
    """True if the top-left quadrant is structurally different from the rest
    (USA flag-style solid canton, or Union-Jack-style canton on a plain
    field like Australia / New Zealand / Tonga)."""
    height, width = len(grid), len(grid[0])
    if width < 10 or height < 6:
        return False, None
    quad_w = width // 2
    quad_h = height // 2

    def profile(x0, x1, y0, y1) -> dict[str, int]:
        counts: dict[str, int] = {}
        for y in range(y0, y1):
            for x in range(x0, x1):
                c = grid[y][x]
                if c is not None:
                    counts[c] = counts.get(c, 0) + 1
        return counts

    tl = profile(0, quad_w, 0, quad_h)
    tr = profile(quad_w, width, 0, quad_h)
    bl = profile(0, quad_w, quad_h, height)
    if not tl or not tr or not bl:
        return False, None

    def dominant(counts: dict[str, int]) -> tuple[Optional[str], float]:
        total = sum(counts.values())
        if not total:
            return None, 0.0
        color, count = max(counts.items(), key=lambda kv: kv[1])
        return color, count / total

    tl_color, tl_dom = dominant(tl)
    tr_color, tr_dom = dominant(tr)
    bl_color, bl_dom = dominant(bl)
    if tl_color is None:
        return False, None

    tl_uniform = tl_dom >= 0.85
    tr_uniform = tr_dom >= 0.85
    bl_uniform = bl_dom >= 0.85

    if tl_uniform and not tr_uniform:
        # US style: solid canton on a patterned field
        return True, tl_color
    if not tl_uniform and tr_uniform and bl_uniform:
        # Union-Jack style: patterned canton on a plain field
        if tl_color != tr_color and (bl_color == tr_color or bl_color == tl_color):
            return True, tl_color
    return False, None


def _detect_central_disc(grid: list[list[str]]) -> tuple[bool, bool]:
    """Detect a central disc (Japan, Laos) and a generic central emblem.
    Both require the center color to be a compact blob, not a full band."""
    height, width = len(grid), len(grid[0])
    cx, cy = width / 2, height / 2
    radius = max(3, min(width, height) * CENTER_RADIUS)
    center_colors: dict[str, int] = {}
    ring_colors: dict[str, int] = {}
    for y in range(height):
        for x in range(width):
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            color = grid[y][x]
            if color is None:
                continue
            if dist <= radius:
                center_colors[color] = center_colors.get(color, 0) + 1
            elif dist <= radius * 2.2:
                ring_colors[color] = ring_colors.get(color, 0) + 1
    if not center_colors or not ring_colors:
        return False, False
    center_total = sum(center_colors.values())
    ring_total = sum(ring_colors.values())
    if center_total < 20 or ring_total < 20:
        return False, False
    center_top = max(center_colors.items(), key=lambda kv: kv[1])[0]
    center_frac = center_colors[center_top] / center_total
    ring_top = max(ring_colors.items(), key=lambda kv: kv[1])[0]
    if center_top == ring_top or center_frac < 0.6:
        return False, False

    bbox = _color_bbox(grid, center_top)
    if bbox is None:
        return False, False
    min_x, min_y, max_x, max_y = bbox
    bw = max_x - min_x + 1
    bh = max_y - min_y + 1
    if bw >= 0.8 * width or bh >= 0.8 * height:
        return False, False  # the color is a full band, not a blob
    area = bh * bw
    if area <= 0:
        return False, False
    counts = sum(1 for row in grid for c in row if c == center_top)
    fill = counts / area
    if fill < 0.25:
        return False, False
    aspect = bh / max(bw, 1)
    disc = fill >= 0.6 and 0.55 <= aspect <= 1.8
    return True, disc


def _color_bbox(
    grid: list[list[str]], color: Optional[str]
) -> Optional[tuple[int, int, int, int]]:
    xs: list[int] = []
    ys: list[int] = []
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            if c == color:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _detect_border(grid: list[list[str]]) -> bool:
    """True if one color dominates the edges but not the interior."""
    height, width = len(grid), len(grid[0])
    edge_counts: dict[str, int] = {}
    for y in range(height):
        for x in (0, width - 1):
            c = grid[y][x]
            if c is not None:
                edge_counts[c] = edge_counts.get(c, 0) + 1
    for x in range(width):
        for y in (0, height - 1):
            c = grid[y][x]
            if c is not None:
                edge_counts[c] = edge_counts.get(c, 0) + 1
    if not edge_counts:
        return False
    total_edges = max(1, 2 * height + 2 * width - 4)
    dominant, count = max(edge_counts.items(), key=lambda kv: kv[1])
    if count / total_edges < 0.8:
        return False
    interior_counts: dict[str, int] = {}
    for row in grid[1 : height - 1]:
        for c in row[1 : width - 1]:
            if c is not None:
                interior_counts[c] = interior_counts.get(c, 0) + 1
    if not interior_counts:
        return False
    return interior_counts.get(dominant, 0) / sum(interior_counts.values()) < 0.3


def _detect_triangle(grid: list[list[str]]) -> bool:
    """Detect a hoist triangle (color band on the left tapering toward the
    right; Czechia, Bahamas, Sudan)."""
    height, width = len(grid), len(grid[0])
    if width < 12:
        return False
    left_colors: dict[str, int] = {}
    for y in range(height):
        c = grid[y][0]
        if c is not None:
            left_colors[c] = left_colors.get(c, 0) + 1
    if not left_colors:
        return False
    tri_color, count = max(left_colors.items(), key=lambda kv: kv[1])
    if count / height < 0.6:
        return False

    total_pixels = height * width
    coverage = sum(
        1 for row in grid for c in row if c == tri_color
    ) / max(total_pixels, 1)
    if not (0.05 <= coverage <= 0.35):
        return False

    runs: list[int] = []
    for y in range(height):
        run = 0
        for x in range(width):
            if grid[y][x] == tri_color:
                run += 1
            else:
                break
        runs.append(run)
    max_run = max(runs)
    avg_run = sum(runs) / len(runs)
    return (
        max_run >= 5
        and max_run < width * 0.55
        and avg_run < max_run * 0.85
    )


def _symmetry_score(grid: list[list[str]], vertical: bool) -> float:
    """Fraction of mirror-matching pixels (left-right or top-bottom)."""
    height, width = len(grid), len(grid[0])
    matches = 0
    total = height * width
    for y in range(height):
        for x in range(width):
            if vertical:
                other = grid[y][width - 1 - x]
            else:
                other = grid[height - 1 - y][x]
            if grid[y][x] == other:
                matches += 1
    return round(matches / max(total, 1), 3)


def _field_color(grid: list[list[str]]) -> Optional[str]:
    """Most frequent color (best-effort field color, informational)."""
    counts: dict[str, int] = {}
    for row in grid:
        for c in row:
            if c is not None:
                counts[c] = counts.get(c, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]
