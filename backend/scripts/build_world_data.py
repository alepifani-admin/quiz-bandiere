#!/usr/bin/env python3
"""Build the "world flags" dataset for the Quiz Bandiere Mondo.

Sources (traceable, see app/data/sources.py):
- flagcdn.com/en/codes.json   every flag served (ISO 3166-1 alpha-2 + extra
                              codes such as the US states, England/Scotland/
                              Wales, EU, UN, Kosovo) with English names
- flagcdn.com/w320/{code}.png flag assets (320px wide PNG)
- Wikidata                    Italian names + continent for each code

Writes:
- data/cache/world-flags/{CODE}.png  flag assets
- data/cache/world_analysis.json     per-flag analysis (same schema as
                                     flag_analysis.json)
- data/baseline/world_manifest.json  code -> en/it name, qid, continent

Usage:
    python scripts/build_world_data.py [--force-flags]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
from PIL import Image

import _bootstrap  # noqa: F401  (adds backend root to sys.path)

from app.analysis.analyzer import analyze_flag
from app.core.config import get_settings
from app.data.sources import SOURCES

FLAGCDN_CODES_URL = "https://flagcdn.com/en/codes.json"
FLAGCDN_FLAG_URL = "https://flagcdn.com/w320/{code}.png"
WIKIDATA_QUERY_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "FIFAFlagQuiz/0.1 (world-dataset-builder)"

#: flagcdn codes without an ISO 3166-1 alpha-2 -> their Wikidata QID.
QID_OVERRIDES = {
    # UK home nations / supranational organisations / others
    "eu": "Q458",
    "un": "Q1065",
    "gb-eng": "Q21",
    "gb-nir": "Q26",
    "gb-sct": "Q22",
    "gb-wls": "Q25",
    "xk": "Q1246",  # Kosovo (Q403 is Serbia)
    # US states (flagcdn serves all 50)
    "us-ak": "Q797", "us-al": "Q173", "us-ar": "Q1612", "us-az": "Q816",
    "us-ca": "Q99", "us-co": "Q1261", "us-ct": "Q779", "us-de": "Q1393",
    "us-fl": "Q812", "us-ga": "Q1428", "us-hi": "Q782", "us-ia": "Q1546",
    "us-id": "Q1221", "us-il": "Q1204", "us-in": "Q1415", "us-ks": "Q1558",
    "us-ky": "Q1603", "us-la": "Q1588", "us-ma": "Q771", "us-md": "Q1391",
    "us-me": "Q724", "us-mi": "Q1166", "us-mn": "Q1527", "us-mo": "Q1581",
    "us-ms": "Q1494", "us-mt": "Q1212", "us-nc": "Q1454", "us-nd": "Q1207",
    "us-ne": "Q1553", "us-nh": "Q759", "us-nj": "Q1408", "us-nm": "Q1522",
    "us-nv": "Q1227", "us-ny": "Q1384", "us-oh": "Q1397", "us-ok": "Q1649",
    "us-or": "Q824", "us-pa": "Q1400", "us-ri": "Q1387", "us-sc": "Q1456",
    "us-sd": "Q1211", "us-tn": "Q1509", "us-tx": "Q1439", "us-ut": "Q829",
    "us-va": "Q1370", "us-vt": "Q16551", "us-wa": "Q1223", "us-wi": "Q1537",
    "us-wv": "Q1371", "us-wy": "Q1214",
}

_LABELS_QUERY = """
SELECT ?c ?it ?en ?contLabel WHERE {
  VALUES ?c { %s }
  OPTIONAL { ?c rdfs:label ?it. FILTER(LANG(?it) = "it") }
  OPTIONAL { ?c rdfs:label ?en. FILTER(LANG(?en) = "en") }
  OPTIONAL { ?c wdt:P30 ?cont. ?cont rdfs:label ?contLabel.
             FILTER(LANG(?contLabel) = "en") }
}
"""

_ISO_QIDS_QUERY = """
SELECT ?c ?code ?cLabel WHERE {
  ?c wdt:P297 ?code.
  OPTIONAL { ?c rdfs:label ?cLabel. FILTER(LANG(?cLabel) = "en") }
}
"""


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=60,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def _sparql(query: str, attempts: int = 6) -> list[dict]:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with _client() as c:
                resp = c.post(WIKIDATA_QUERY_URL, data={"query": query, "format": "json"})
                resp.raise_for_status()
            return resp.json()["results"]["bindings"]
        except Exception as exc:  # noqa: BLE001 - Wikidata is flaky; retry
            last = exc
            import time

            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"Wikidata query failed after {attempts} attempts: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the world-flags dataset")
    parser.add_argument(
        "--force-flags", action="store_true",
        help="re-download flag PNGs even if already cached",
    )
    args = parser.parse_args()

    settings = get_settings()
    flag_dir = Path(settings.cache_dir) / "world-flags"
    manifest_path = Path(settings.baseline_dir) / "world_manifest.json"
    flag_dir.mkdir(parents=True, exist_ok=True)

    print("[1/5] Fetching flagcdn codes...")
    with _client() as c:
        codes: dict[str, str] = c.get(FLAGCDN_CODES_URL).json()
    print(f"      {len(codes)} flags advertised by flagcdn")

    print("[2/5] Mapping codes to Wikidata entities...")
    iso_qids: dict[str, str] = {}
    by_code: dict[str, list[tuple[str, str]]] = {}
    for row in _sparql(_ISO_QIDS_QUERY):
        code = row["code"]["value"].lower()
        entity = row["c"]["value"].rsplit("/", 1)[-1]
        en = row.get("cLabel", {}).get("value", "")
        by_code.setdefault(code, []).append((entity, en))
    for code, candidates in by_code.items():
        # several codes (e.g. SA) map to more than one entity: prefer the one
        # whose English label matches the flagcdn name exactly
        expected = codes.get(code, "")
        best = None
        for entity, en in candidates:
            if en.lower() == expected.lower():
                best = entity
                break
        iso_qids[code] = best or candidates[0][0]
    qid_by_code = {code: QID_OVERRIDES.get(code) or iso_qids.get(code) for code in codes}
    unknown = [code for code, qid in qid_by_code.items() if not qid]
    if unknown:
        print(f"      WARNING: no QID for {len(unknown)} codes: {sorted(unknown)}")

    qids = sorted({qid for qid in qid_by_code.values() if qid})
    print(f"      {len(qids)} Wikidata entities ({len(codes) - len(qids)} without QID)")

    print("[3/5] Fetching Italian/English names + continents from Wikidata...")
    entity_rows: dict[str, dict] = {}
    # split in chunks to stay under the server query-size limit
    for i in range(0, len(qids), 120):
        chunk = qids[i:i + 120]
        values = " ".join(f"wd:{q}" for q in chunk)
        for row in _sparql(_LABELS_QUERY % values):
            entity = row["c"]["value"].rsplit("/", 1)[-1]
            rec = entity_rows.setdefault(entity, {})
            if row.get("it"):
                rec["name_it"] = row["it"]["value"]
            if row.get("en"):
                rec["name_en"] = row["en"]["value"]
            if row.get("contLabel"):
                rec["continent"] = row["contLabel"]["value"]
    print(f"      labels for {len(entity_rows)} entities")

    print("[4/5] Downloading flag assets...")
    ok = 0
    skipped = 0
    failed: list[str] = []
    with _client() as c:
        for code in codes:
            target = flag_dir / f"{code.upper()}.png"
            if target.exists() and not args.force_flags:
                skipped += 1
                continue
            try:
                resp = c.get(FLAGCDN_FLAG_URL.format(code=code))
                resp.raise_for_status()
                if not resp.content:
                    raise RuntimeError("empty body")
                target.write_bytes(resp.content)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - report and continue
                failed.append(code)
                print(f"      FAILED {code}: {exc}")
    print(f"      downloaded {ok}, skipped {skipped}, failed {len(failed)}")

    print("[5/5] Analyzing flags...")
    analysis_path = Path(settings.cache_dir) / "world_analysis.json"
    analysis: dict[str, dict] = {}
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            analysis = {}
        if len(analysis) >= len(codes):
            print(f"      cached analysis for {len(analysis)} flags (skipping)")
        else:
            analysis = {}
    pngs = sorted(flag_dir.glob("*.png"))
    if not analysis:
        for i, png in enumerate(pngs, start=1):
            try:
                result = analyze_flag(Image.open(png).convert("RGB"), png.stem)
            except Exception as exc:  # noqa: BLE001 - skip corrupt assets
                print(f"      ANALYZE FAILED {png.stem}: {exc}")
                continue
            analysis[png.stem] = result.as_dict()
            if i % 60 == 0 or i == len(pngs):
                print(f"      {i}/{len(pngs)} analyzed")
    analysis_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"      analysis for {len(analysis)} flags -> {analysis_path}")

    countries = []
    for code in sorted(codes):
        entity = entity_rows.get(qid_by_code[code], {}) if qid_by_code[code] else {}
        countries.append({
            "code": code.upper(),
            "name": entity.get("name_en") or codes[code],
            "name_it": entity.get("name_it") or codes[code],
            "qid": qid_by_code[code] or "",
            "continent": entity.get("continent", ""),
        })
    manifest = {
        "source": SOURCES["world-flag-assets"].url,
        "count": len(countries),
        "countries": countries,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"      manifest -> {manifest_path} ({len(countries)} countries)")

    mismatches = [
        (c["code"], codes[c["code"].lower()], c["name"])
        for c in countries
        if c["name"].lower() != codes[c["code"].lower()].lower()
    ]
    if mismatches:
        print("      WARNING: Wikidata name != flagcdn name:")
        for code, expected, got in mismatches:
            print(f"        {code}: flagcdn={expected!r} wikidata={got!r}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
