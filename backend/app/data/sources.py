"""Source registry: every external data source used by the system.

Each source is traceable (section 26): provider, url, license, type.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    provider: str
    url: str
    license: str
    description: str


SOURCES = {
    "fifa-associations": Source(
        provider="FIFA",
        url="https://inside.fifa.com/associations",
        license="FIFA official site content (display within the app; see Sources & Credits)",
        description="Official FIFA member associations list with name, confederation, FIFA code, flag asset.",
    ),
    "fifa-flag-assets": Source(
        provider="FIFA",
        url="https://api.fifa.com/api/v3/picture/flags-sq-4/{CODE}",
        license="FIFA official flag assets (display within the app; see Sources & Credits)",
        description="Official FIFA flag PNG assets keyed by FIFA code (flags-sq-4, 250px wide).",
    ),
    "bandiere-mondo": Source(
        provider="Bandiere-mondo.it (flagpedia.net, flagcdn.com CDN)",
        url="https://www.bandiere-mondo.it/ (API: https://flagcdn.com/{size}/{iso}.png)",
        license="Flag assets are public domain / freely usable (see flagcdn.com / Wikimedia Commons)",
        description=(
            "Italian flag portal (flagpedia.net); its images are served from the "
            "flagcdn.com CDN. Used as a fallback for FIFA flag assets when the "
            "official FIFA API is unreachable, keyed by ISO 3166-1 alpha-2."
        ),
    ),
    "wikimedia-flag-assets": Source(
        provider="Wikimedia Commons",
        url="https://commons.wikimedia.org",
        license="CC BY-SA / public domain (per asset)",
        description="Fallback flag assets when FIFA API does not provide one.",
    ),
    "world-flag-assets": Source(
        provider="flagcdn",
        url="https://flagcdn.com",
        license="Flag assets are public domain / freely usable (see flagcdn.com)",
        description="All world flag PNGs (ISO 3166-1 + extra codes) and their English names; used by the Quiz Bandiere Mondo.",
    ),
}

CANONICAL_CONFEDERATIONS = frozenset({"UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC"})

CONFEDERATION_CONTINENT = {
    "UEFA": "Europe",
    "CONMEBOL": "South America",
    "CONCACAF": "North America",
    "CAF": "Africa",
    "AFC": "Asia",
    "OFC": "Oceania",
}
