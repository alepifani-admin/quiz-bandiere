"""Data validation (section 3 / 25 / 35).

Responsible for integrity checks: counts, required fields, code pattern,
confederation whitelist, checksums.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from app.data.schemas import AssociationRecord, DatasetManifest, ValidationIssue, ValidationReport
from app.data.sources import CANONICAL_CONFEDERATIONS

FIFA_CODE_RE = re.compile(r"^[A-Z]{3}$")


def compute_checksum(manifest: DatasetManifest) -> str:
    """SHA-256 over the canonical serialized manifest (without the checksum field)."""
    payload = {
        "version": manifest.version,
        "timestamp": manifest.timestamp.isoformat(),
        "source": manifest.source,
        "record_count": manifest.record_count,
        "associations": [
            record.model_dump(mode="json") for record in manifest.associations
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def validate_manifest(manifest: DatasetManifest, expected: int) -> ValidationReport:
    """Full integrity validation of a dataset manifest."""
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    actual = len(manifest.associations)

    for record in manifest.associations:
        if not FIFA_CODE_RE.match(record.fifa_code):
            issues.append(
                ValidationIssue(record=record.fifa_code, field="fifa_code", message="invalid code pattern")
            )
        if record.fifa_code in seen:
            issues.append(
                ValidationIssue(record=record.fifa_code, field="fifa_code", message="duplicate code")
            )
        seen.add(record.fifa_code)
        if not record.name or not record.name.strip():
            issues.append(
                ValidationIssue(record=record.fifa_code, field="name", message="missing name")
            )
        if record.confederation not in CANONICAL_CONFEDERATIONS:
            issues.append(
                ValidationIssue(
                    record=record.fifa_code,
                    field="confederation",
                    message=f"unknown confederation {record.confederation!r}",
                )
            )
        if not record.flag_url:
            issues.append(
                ValidationIssue(record=record.fifa_code, field="flag_url", message="missing flag_url")
            )

    if actual != expected:
        issues.append(
            ValidationIssue(
                field="record_count",
                message=f"expected {expected} associations, got {actual} (DATASET INVALID)",
            )
        )

    expected_checksum = compute_checksum(manifest)
    if manifest.checksum != expected_checksum:
        issues.append(
            ValidationIssue(field="checksum", message="checksum mismatch — data may be corrupted")
        )

    return ValidationReport(valid=not issues, expected=expected, actual=actual, issues=issues)


def validate_record_list(records: Iterable[AssociationRecord], expected: int) -> ValidationReport:
    """Validate a raw record list before building a manifest."""
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for record in records:
        if not FIFA_CODE_RE.match(record.fifa_code):
            issues.append(
                ValidationIssue(record=record.fifa_code, field="fifa_code", message="invalid code pattern")
            )
        if record.fifa_code in seen:
            issues.append(
                ValidationIssue(record=record.fifa_code, field="fifa_code", message="duplicate code")
            )
        seen.add(record.fifa_code)
        if record.confederation not in CANONICAL_CONFEDERATIONS:
            issues.append(
                ValidationIssue(
                    record=record.fifa_code,
                    field="confederation",
                    message=f"unknown confederation {record.confederation!r}",
                )
            )
    if len(seen) != expected:
        issues.append(
            ValidationIssue(
                field="record_count",
                message=f"expected {expected} associations, got {len(seen)} (DATASET INVALID)",
            )
        )
    return ValidationReport(valid=not issues, expected=expected, actual=len(seen), issues=issues)
