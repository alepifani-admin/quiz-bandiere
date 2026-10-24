"""Flag Analyzer orchestrator (section 6).

Combines color, geometry and symbol analyses into a single FlagAnalysis,
handling the non-rectangular Nepal special case (padding exclusion).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image

from app.analysis import colors as color_mod
from app.analysis import geometry as geom_mod
from app.analysis import symbols as sym_mod
from app.data.assets import NON_RECTANGULAR


@dataclass
class FlagAnalysis:
    fifa_code: str
    color: color_mod.ColorAnalysis
    geometry: geom_mod.GeometryAnalysis
    symbols: sym_mod.SymbolAnalysis
    shape: str
    content_bbox: Optional[tuple[int, int, int, int]] = None

    @property
    def needs_review(self) -> bool:
        if self.shape == "non_rectangular":
            return True
        return any(
            f.needs_review for f in self.geometry.features
        ) or any(s.needs_review for s in self.symbols.symbols)

    def as_dict(self) -> dict:
        return {
            "fifa_code": self.fifa_code,
            "shape": self.shape,
            "color": self.color.as_dict(),
            "geometry": self.geometry.as_dict(),
            "symbols": self.symbols.as_dict(),
            "needs_review": self.needs_review,
        }


def _content_bbox_for_nepal(image: Image.Image) -> tuple[int, int, int, int]:
    """Nepal's FIFA asset draws the pennant inside a white padded frame.
    Return the bounding box of the non-white content so that color surface
    percentages are computed over the flag itself (section 7, 42)."""
    from app.analysis.colors import classify_color

    image = image.convert("RGB")
    width, height = image.size
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            name, _ = classify_color(image.getpixel((x, y)))
            if name != "WHITE":
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0, 0, width, height)
    # pad by one pixel so the blue border is fully included
    return (max(0, min(xs) - 1), max(0, min(ys) - 1),
            min(width - 1, max(xs) + 1), min(height - 1, max(ys) + 1))


def analyze_flag(image: Image.Image, fifa_code: str) -> FlagAnalysis:
    fifa_code = fifa_code.upper()
    # 1px/empty images would divide by zero in the border/geometry passes
    if image.width < 2 or image.height < 2:
        raise ValueError(f"image too small: {image.size}")
    shape = "non_rectangular" if fifa_code in NON_RECTANGULAR else "rectangular"

    content_bbox = None
    if fifa_code == "NEP":
        content_bbox = _content_bbox_for_nepal(image)

    color = color_mod.analyze_colors(image, content_bbox=content_bbox)
    geometry_image = image
    if content_bbox:
        x0, y0, x1, y1 = content_bbox
        geometry_image = image.crop((x0, y0, x1 + 1, y1 + 1))
    geometry = geom_mod.analyze_geometry(geometry_image)
    symbols = sym_mod.analyze_symbols(image, fifa_code, color.primary_colors)

    return FlagAnalysis(
        fifa_code=fifa_code,
        color=color,
        geometry=geometry,
        symbols=symbols,
        shape=shape,
        content_bbox=content_bbox,
    )
