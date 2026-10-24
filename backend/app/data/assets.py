"""Flag asset mapping (section 4 / 5 / 42).

Maps FIFA code -> local flag asset with explicit metadata. Never assumes
ISO == FIFA. Nepal is handled as a special non-rectangular case.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from PIL import Image

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: Flags whose official rendering is non-rectangular (Nepal is the only FIFA case).
NON_RECTANGULAR = frozenset({"NEP"})

#: Allowed asset codes: 1-4 alphanumerics plus optional region suffix
#: (e.g. ``GB-ENG``, ``US-CA``). Anything else (``../``, ``/``, ``.``)
#: is rejected so a code can never escape the cache directory.
_ASSET_CODE_RE = re.compile(r"^[A-Za-z0-9]{1,4}(?:-[A-Za-z0-9]{1,4})?$")

#: Known official aspect ratios (ratio = width / height), source: official flag spec.
#: Only set where verifiable; everything else stays None (needs_review).
KNOWN_OFFICIAL_RATIO = {
    "NEP": 1.3333,  # Nepal official ~4:3 pennant
    "SUI": 1.0,     # Swiss flag is square (1:1)
}


class FlagAsset:
    def __init__(
        self,
        fifa_code: str,
        path: Path,
        rendered_ratio: float,
        url_prefix: str = "/api/flags",
    ):
        self.fifa_code = fifa_code
        self.path = path
        self.rendered_ratio = rendered_ratio
        self.url_prefix = url_prefix

    @property
    def url(self) -> str:
        """Public URL served by the backend."""
        return f"{self.url_prefix}/{self.fifa_code}/asset"

    @property
    def shape(self) -> str:
        return "non_rectangular" if self.fifa_code in NON_RECTANGULAR else "rectangular"

    @property
    def official_ratio(self) -> Optional[float]:
        return KNOWN_OFFICIAL_RATIO.get(self.fifa_code)

    def metadata(self) -> dict:
        return {
            "fifa_code": self.fifa_code,
            "asset_url": self.url,
            "local_path": str(self.path),
            "rendered_ratio": round(self.rendered_ratio, 4),
            "official_ratio": self.official_ratio,
            "shape": self.shape,
            "source": "fifa-flag-assets",
            "confidence": 1.0,
        }


class FlagAssetStore:
    """Serves flag assets from a local cache directory, keyed by code.

    `url_prefix` distinguishes the FIFA pool (``/api/flags``) from the world
    pool (``/api/world/flags``).
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        url_prefix: str = "/api/flags",
    ) -> None:
        settings = get_settings()
        self.cache_dir = Path(cache_dir or (settings.cache_dir / "flag-svgs"))
        self.url_prefix = url_prefix

    def path_for(self, fifa_code: str) -> Optional[Path]:
        code = (fifa_code or "").strip().upper()
        if not _ASSET_CODE_RE.fullmatch(code):
            return None
        candidate = self.cache_dir / f"{code}.png"
        return candidate if candidate.exists() else None

    def asset_for(self, fifa_code: str) -> Optional[FlagAsset]:
        path = self.path_for(fifa_code)
        if path is None:
            return None
        try:
            with Image.open(path) as img:
                ratio = img.size[0] / img.size[1]
        except Exception as exc:  # pragma: no cover - corrupt asset guard
            logger.warning("Unreadable asset %s: %s", path, exc)
            return None
        return FlagAsset(fifa_code.upper(), path, ratio, url_prefix=self.url_prefix)

    def available_codes(self) -> set[str]:
        return {p.stem for p in self.cache_dir.glob("*.png")}

    def missing_codes(self, codes: list[str]) -> list[str]:
        available = self.available_codes()
        return [c for c in codes if c not in available]
