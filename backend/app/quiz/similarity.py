"""Flag Similarity Score (section 15).

Compares two analyzed flags on six weighted components:
- color: overlap of the primary color sets
- color_count: penalty for differing number of colors
- structure: stripe band layout (count + orientation)
- geometry: cross / canton / disc / emblem / triangle features
- symbol: star-count proximity
- orientation: stripe orientation match

The score is normalized to [0, 1]; higher means more confusable. Used to
select distractors that look like the target flag.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import get_settings

GEOMETRY_FEATURES = (
    "horizontal_stripes",
    "vertical_stripes",
    "cross",
    "nordic_cross",
    "canton",
    "central_circle",
    "central_emblem",
    "border",
    "triangle",
)


def color_overlap(
    a_primary: list[str],
    b_primary: list[str],
    a_secondary: Optional[list[str]] = None,
    b_secondary: Optional[list[str]] = None,
) -> float:
    """Jaccard-like overlap of primary (and secondary) color sets."""
    a = set(a_primary)
    b = set(b_primary)
    if not a or not b:
        return 0.0
    primary = len(a & b) / len(a | b)
    if a_secondary and b_secondary:
        sa = set(a_secondary) - a
        sb = set(b_secondary) - b
        if sa or sb:
            secondary = len(sa & sb) / len(sa | sb)
            return 0.7 * primary + 0.3 * secondary
    return primary


def _feature_vec(features: dict) -> dict[str, float]:
    return {name: features.get(name, {}).get("value", 0.0) for name in GEOMETRY_FEATURES}


def geometry_similarity(a: dict, b: dict) -> float:
    fa = _feature_vec(a)
    fb = _feature_vec(b)
    matches = sum(1 for name in GEOMETRY_FEATURES if bool(fa[name]) == bool(fb[name]))
    return matches / len(GEOMETRY_FEATURES)


def orientation_similarity(a: dict, b: dict) -> float:
    oa = a.get("orientation_code", {}).get("value", 3.0)
    ob = b.get("orientation_code", {}).get("value", 3.0)
    return 1.0 if oa == ob else 0.0


def structure_similarity(a: dict, b: dict) -> float:
    """Bands: compare stripe counts when both are striped."""
    ha = a.get("horizontal_stripes", {}).get("value", 0.0)
    hb = b.get("horizontal_stripes", {}).get("value", 0.0)
    va = a.get("vertical_stripes", {}).get("value", 0.0)
    vb = b.get("vertical_stripes", {}).get("value", 0.0)
    if ha and hb:
        return 1.0 if ha == hb else 0.5
    if va and vb:
        return 1.0 if va == vb else 0.5
    if (ha or va) and (hb or vb):
        return 0.3
    return 0.5


def symbol_similarity(a: dict, b: dict) -> float:
    sa = a.get("stars", {}).get("count", 0)
    sb = b.get("stars", {}).get("count", 0)
    if sa == sb:
        return 1.0
    diff = abs(sa - sb)
    if sa == 0 or sb == 0:
        return 0.0
    return max(0.0, 1.0 - diff / max(sa, sb))


def similarity(
    a: dict,
    b: dict,
    weights: Optional[dict] = None,
) -> float:
    """Weighted Flag Similarity Score between two analysis dicts (as_dict)."""
    w = weights or get_settings().similarity_weights.model_dump()

    a_color = a.get("color", {})
    b_color = b.get("color", {})
    a_geo = a.get("geometry", {})
    b_geo = b.get("geometry", {})

    scores = {
        "color": color_overlap(
            a_color.get("primary_colors", []),
            b_color.get("primary_colors", []),
            a_color.get("all_colors"),
            b_color.get("all_colors"),
        ),
        "color_count": color_count_similarity(a_color, b_color),
        "structure": structure_similarity(a_geo, b_geo),
        "geometry": geometry_similarity(a_geo, b_geo),
        "symbol": symbol_similarity(a.get("symbols", {}), b.get("symbols", {})),
        "orientation": orientation_similarity(a_geo, b_geo),
    }

    total = sum(w.get(name, 0.0) for name in scores)
    if total <= 0:
        return 0.0
    weighted = sum(w.get(name, 0.0) * scores[name] for name in scores)
    return round(weighted / total, 4)


def color_count_similarity(a_color: dict, b_color: dict) -> float:
    ca = a_color.get("color_count_primary", 0)
    cb = b_color.get("color_count_primary", 0)
    return 1.0 if ca == cb else 0.5
