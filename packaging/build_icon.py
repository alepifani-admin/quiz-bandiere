"""Generate the QuiZ Bandiere app icon (.icns) for the macOS bundle.

White/black soccer ball (Twemoji, CC BY 4.0) rendered on a transparent
background, centered with a small margin.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent
ICONSET = OUT_DIR / "AppIcon.iconset"
ICNS = OUT_DIR / "icon.icns"
BALL = OUT_DIR / "soccer-ball.png"

SIZES = (16, 32, 128, 256, 512)
SCALE = 0.92


def make(src: Image.Image, size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ball = src.resize((max(1, round(size * SCALE)),) * 2, Image.LANCZOS)
    x = (size - ball.width) // 2
    y = (size - ball.height) // 2
    img.paste(ball, (x, y), ball)
    return img


def main() -> None:
    src = Image.open(BALL).convert("RGBA")
    shutil.rmtree(ICONSET, ignore_errors=True)
    ICONSET.mkdir()
    for s in SIZES:
        for scale in (1, 2):
            px = s * scale
            name = f"icon_{s}x{s}"
            if scale == 2:
                name += "@2x"
            make(src, px).save(ICONSET / f"{name}.png")
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)],
        check=True,
    )
    shutil.rmtree(ICONSET, ignore_errors=True)
    print(f"icon written to {ICNS}")


if __name__ == "__main__":
    sys.exit(main())
