#!/usr/bin/env python3
"""Data update CLI.

Usage:
    python scripts/update_data.py --source online   # fetch from FIFA (default)
    python scripts/update_data.py --source baseline # offline fallback (cached)
    python scripts/update_data.py --skip-flags      # do not download flag assets

Follows download -> validate -> compare -> commit. A failed update never
overwrites a valid cached dataset (sections 24/25).
"""
from __future__ import annotations

import argparse
import logging
import sys

import _bootstrap  # noqa: F401  (adds backend root to sys.path)

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.data.manager import UpdateManager

setup_logging()


def main() -> int:
    parser = argparse.ArgumentParser(description="FIFA Flag Quiz data updater")
    parser.add_argument("--source", choices=["online", "baseline"], default="online")
    parser.add_argument("--skip-flags", action="store_true", help="skip flag asset download")
    args = parser.parse_args()

    settings = get_settings()
    manager = UpdateManager()

    if args.source == "online":
        result = manager.update_from_network()
    else:
        result = manager.update_from_baseline()

    print(f"STATUS: {'OK' if result.ok else 'FAILED'}")
    print(f"MESSAGE: {result.message}")
    if not result.ok:
        return 1

    if result.manifest is None:
        print("STATUS: NO DATA")
        return 1

    print(f"VERSION: {result.manifest.version}")
    print(f"TIMESTAMP: {result.manifest.timestamp.isoformat()}")
    print(f"RECORDS: {result.manifest.record_count}")
    print(f"SOURCE: {result.manifest.source}")
    print(f"CHECKSUM: {result.manifest.checksum[:16]}...")

    if not args.skip_flags:
        flags = manager.update_flag_assets(result.manifest)
        print(f"FLAG ASSETS: {flags['downloaded']} downloaded, {flags['failed']} failed")
        if flags["codes"]:
            print(f"MISSING: {','.join(flags['codes'])}")

    print("STATUS: DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
