"""Review layer: report generation and manual override application.

Section 9/10 of the spec. The analyzer marks flags with uncertain positive
detections as needs_review. This module:
- runs the full analysis over the cached flag images,
- collects flags needing review with the reasons (low-confidence geometry,
  uncertain star counts, non-rectangular shape, missing manual symbol data),
- writes a human-readable report (report.md) and a machine-readable review
  queue (queue.json) that drives the manual verification workflow.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PIL import Image

from app.analysis.analyzer import FlagAnalysis, analyze_flag
from app.analysis.symbols import _load_manual_overrides
from app.core.config import get_settings


def _load_names() -> dict[str, str]:
    path = Path(get_settings().data_baseline_dir) / "name_it.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _flag_path(code: str) -> Path:
    return Path(get_settings().data_cache_dir) / "flag-svgs" / f"{code}.png"


def _review_reasons(analysis: FlagAnalysis) -> list[str]:
    """Human-readable reasons for including a flag in the review queue."""
    reasons: list[str] = []
    if analysis.shape == "non_rectangular":
        reasons.append("forma non rettangolare")
    for f in analysis.geometry.features:
        if f.needs_review:
            reasons.append(f"geometria '{f.feature}' a confidenza bassa")
    for s in analysis.symbols.symbols:
        if s.needs_review:
            reasons.append(f"simbolo '{s.symbol}' incerto")
    stars = analysis.symbols.star_count
    if stars and not _has_manual_entry(analysis.fifa_code):
        reasons.append("conteggio stelle non verificato manualmente")
    return sorted(set(reasons))


def _has_manual_entry(code: str) -> bool:
    return code in _load_manual_overrides()


def analyze_all() -> list[tuple[str, FlagAnalysis]]:
    """Run the full analysis over every cached flag image, reusing the
    on-disk analysis cache when available."""
    cache_dir = Path(get_settings().data_cache_dir) / "flag-svgs"
    from app.analysis.cache import load_cached
    from app.analysis.colors import ColorAnalysis

    cached = load_cached()
    results: list[tuple[str, FlagAnalysis]] = []
    for png in sorted(cache_dir.glob("*.png")):
        code = png.stem
        if cached and code in cached:
            results.append((code, _from_cached(code, cached[code])))
            continue
        try:
            image = Image.open(png).convert("RGB")
        except OSError:
            continue
        try:
            results.append((code, analyze_flag(image, code)))
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            results.append((code, _failed_analysis(code, str(exc))))
    return results


def _from_cached(code: str, data: dict) -> FlagAnalysis:
    from app.analysis import colors as color_mod
    from app.analysis import geometry as geom_mod
    from app.analysis import symbols as sym_mod

    color_dict = data.get("color", {})
    color = color_mod.ColorAnalysis(
        primary_colors=color_dict.get("primary_colors", []),
        all_colors=color_dict.get("all_colors", []),
        color_count_primary=color_dict.get("color_count_primary", 0),
        color_count_emblem_inclusive=color_dict.get("color_count_emblem_inclusive", 0),
        dominant_color=color_dict.get("dominant_color"),
        surface_percentages=color_dict.get("surface_percentages", {}),
        presence=color_dict.get("presence", {}),
        technical_rgb={
            k: tuple(v) for k, v in color_dict.get("technical_rgb", {}).items()
        },
    )
    geometry = geom_mod.GeometryAnalysis(
        features=[
            geom_mod.GeometryFeature(
                feature=name,
                value=feat["value"],
                confidence=feat["confidence"],
                needs_review=feat["needs_review"],
            )
            for name, feat in data.get("geometry", {}).items()
        ]
    )
    symbols = sym_mod.SymbolAnalysis(
        symbols=[
            sym_mod.SymbolDetection(
                symbol=name,
                count=det["count"],
                confidence=det["confidence"],
                source=det["source"],
                needs_review=det["needs_review"],
            )
            for name, det in data.get("symbols", {}).items()
        ]
    )
    return FlagAnalysis(
        fifa_code=code,
        color=color,
        geometry=geometry,
        symbols=symbols,
        shape=data.get("shape", "rectangular"),
    )


def _failed_analysis(code: str, error: str) -> FlagAnalysis:
    from app.analysis import colors as color_mod
    from app.analysis import geometry as geom_mod
    from app.analysis import symbols as sym_mod

    return FlagAnalysis(
        fifa_code=code,
        color=color_mod.ColorAnalysis(
            primary_colors=[], all_colors=[], color_count_primary=0,
            color_count_emblem_inclusive=0, dominant_color=None,
            surface_percentages={}, presence={}, technical_rgb={},
        ),
        geometry=geom_mod.GeometryAnalysis(),
        symbols=sym_mod.SymbolAnalysis(),
        shape="rectangular",
    )


def generate_report() -> dict:
    """Run analysis, write report.md and queue.json, return the summary."""
    settings = get_settings()
    review_dir = settings.baseline_dir.parent / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    names = _load_names()
    entries = analyze_all()

    reviewed: list[dict] = []
    for code, analysis in entries:
        stars_unverified = (
            analysis.symbols.star_count
            and not _has_manual_entry(code)
        )
        needs_review = analysis.needs_review or bool(stars_unverified)
        entry = {
            "fifa_code": code,
            "name": names.get(code, code),
            "needs_review": needs_review,
            "shape": analysis.shape,
            "reasons": _review_reasons(analysis),
            "has_manual_symbols": _has_manual_entry(code),
            "auto": analysis.as_dict(),
        }
        reviewed.append(entry)

    queue = [e for e in reviewed if e["needs_review"]]
    queue.sort(key=lambda e: e["fifa_code"])

    (review_dir / "queue.json").write_text(
        json.dumps(
            {"version": settings.expected_associations,
             "total": len(reviewed),
             "needs_review": len(queue),
             "items": queue},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report_path = review_dir / "report.md"
    report_path.write_text(
        _render_markdown(reviewed, names),
        encoding="utf-8",
    )

    return {
        "report": str(report_path),
        "queue": str(review_dir / "queue.json"),
        "total": len(reviewed),
        "needs_review": len(queue),
    }


def _render_markdown(
    entries: list[dict], names: dict[str, str]
) -> str:
    lines = [
        "# Rapporto di revisione bandiere",
        "",
        f"Totale bandiere analizzate: **{len(entries)}**",
        f"Bandiere da revisionare: **{sum(1 for e in entries if e['needs_review'])}**",
        "",
        "## Motivi di revisione",
        "",
    ]
    reasons: dict[str, int] = {}
    for e in entries:
        if not e["needs_review"]:
            continue
        for reason in e["reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: {count}")
    lines += ["", "## Coda di revisione", ""]
    for e in entries:
        if not e["needs_review"]:
            continue
        manual = "" if e["has_manual_symbols"] else " (mancano simboli manuali)"
        lines.append(f"- **{e['fifa_code']}** {e['name']}: {', '.join(e['reasons'])}{manual}")
    return "\n".join(lines) + "\n"


def queue() -> list[dict]:
    path = Path(get_settings().data_baseline_dir).parent / "review" / "queue.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("items", [])
    except (json.JSONDecodeError, OSError):
        return []
