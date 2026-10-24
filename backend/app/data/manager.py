"""Update Manager (sections 3, 24, 25).

Implements the non-destructive update strategy:

    download → validate → compare → commit

A failed update never replaces a valid dataset. The manifest is written
atomically (temp file + rename). Offline: falls back to the last valid
local manifest.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from app.core.config import get_settings
from app.data import fetch
from app.data.schemas import AssociationRecord, DatasetManifest
from app.data.validate import compute_checksum, validate_manifest

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "associations_manifest.json"


class UpdateResult:
    def __init__(self, *, ok: bool, manifest: Optional[DatasetManifest], message: str, from_network: bool):
        self.ok = ok
        self.manifest = manifest
        self.message = message
        self.from_network = from_network

    def __repr__(self) -> str:  # pragma: no cover
        return f"UpdateResult(ok={self.ok}, from_network={self.from_network}, {self.message})"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _version_from_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y%m%d-%H%M%S")


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_manifest(records: list[AssociationRecord], source: str) -> DatasetManifest:
    ts = _now()
    manifest = DatasetManifest(
        version=_version_from_timestamp(ts),
        timestamp=ts,
        source=source,
        record_count=len(records),
        checksum="",
        associations=records,
    )
    manifest.checksum = compute_checksum(manifest)
    return manifest


def load_manifest(path: Path) -> Optional[DatasetManifest]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DatasetManifest.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Corrupt manifest at %s: %s", path, exc)
        return None


class UpdateManager:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def manifest_path(self) -> Path:
        return self.settings.baseline_dir / MANIFEST_FILENAME

    def last_manifest(self) -> Optional[DatasetManifest]:
        manifest = load_manifest(self.manifest_path)
        if manifest:
            report = validate_manifest(manifest, self.settings.expected_associations)
            if report.valid:
                return manifest
            logger.error("Last local manifest is invalid: %s", report.issues)
        return None

    def update_from_network(self) -> UpdateResult:
        """download → validate → compare → commit (associations + flag assets)."""
        try:
            raw = fetch.fetch_associations_from_fifa()
        except Exception as exc:
            logger.error("Network fetch failed: %s", exc)
            return UpdateResult(
                ok=False, manifest=None, from_network=True,
                message=f"network error: {exc}; keeping last valid cache",
            )

        records = fetch.records_from_fifa_payload(raw, _now())
        manifest = build_manifest(records, "fifa-associations")

        report = validate_manifest(manifest, self.settings.expected_associations)
        if not report.valid:
            logger.error("Downloaded dataset INVALID: %s", report.issues)
            return UpdateResult(
                ok=False, manifest=None, from_network=True,
                message=f"downloaded dataset invalid ({len(report.issues)} issues); keeping last valid cache",
            )

        previous = self.last_manifest()
        if previous and previous.record_count == manifest.record_count:
            logger.info("No change in record count (%d); committing new version anyway.", manifest.record_count)

        _write_atomic(self.manifest_path, manifest.model_dump(mode="json"))
        return UpdateResult(
            ok=True, manifest=manifest, from_network=True,
            message=f"committed version {manifest.version} with {manifest.record_count} associations",
        )

    def update_flag_assets(self, manifest: Optional[DatasetManifest] = None) -> dict:
        """Download all flag assets into the cache. Never fails the whole update."""
        manifest = manifest or self.last_manifest()
        if manifest is None:
            return {"downloaded": 0, "failed": 0, "codes": []}
        out = self.settings.cache_dir / "flag-svgs"
        out.mkdir(parents=True, exist_ok=True)
        downloaded, failed = 0, []
        for record in manifest.associations:
            path = fetch.download_flag_asset(record.fifa_code, out)
            if path:
                downloaded += 1
                record.flag_asset_path = path
            else:
                failed.append(record.fifa_code)
        # persist asset paths into the committed manifest (atomic); the
        # checksum covers the records, so it must be recomputed after the
        # asset paths were attached or last_manifest() would reject the file
        manifest.checksum = compute_checksum(manifest)
        _write_atomic(self.manifest_path, manifest.model_dump(mode="json"))
        return {"downloaded": downloaded, "failed": len(failed), "codes": failed}

    def update_from_baseline(self) -> UpdateResult:
        """Offline fallback: load last valid local manifest (section 31)."""
        manifest = self.last_manifest()
        if manifest is None:
            return UpdateResult(ok=False, manifest=None, from_network=False, message="no valid cached dataset")
        return UpdateResult(ok=True, manifest=manifest, from_network=False, message="using cached dataset")
