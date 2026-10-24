"""Unit tests for data validation."""
from datetime import datetime, timezone

import pytest

from app.data.schemas import AssociationRecord, DatasetManifest
from app.data.validate import compute_checksum, validate_manifest, validate_record_list


def _record(code: str = "ITA", confed: str = "UEFA") -> AssociationRecord:
    return AssociationRecord(
        fifa_code=code,
        name=code,
        confederation=confed,
        flag_url=f"https://api.fifa.com/api/v3/picture/flags-sq-2/{code}",
        retrieved_at=datetime.now(timezone.utc),
    )


def _manifest(records):
    manifest = DatasetManifest(
        version="v1",
        timestamp=datetime.now(timezone.utc),
        source="test",
        record_count=len(records),
        checksum="",
        associations=records,
    )
    manifest.checksum = compute_checksum(manifest)
    return manifest


def test_checksum_changes_with_records():
    m1 = _manifest([_record("ITA"), _record("FRA")])
    m2 = _manifest([_record("ITA"), _record("GER")])
    assert m1.checksum != m2.checksum


def test_valid_manifest_passes():
    manifest = _manifest([_record("ITA"), _record("FRA")])
    report = validate_manifest(manifest, expected=2)
    assert report.valid
    assert report.actual == 2


def test_invalid_code_rejected():
    # model_construct bypasses schema validation to simulate raw bad input
    bad = AssociationRecord.model_construct(
        fifa_code="I",
        name="Bad",
        confederation="UEFA",
        flag_url="x",
        retrieved_at=datetime.now(timezone.utc),
    )
    manifest = _manifest([bad])
    report = validate_manifest(manifest, expected=1)
    assert not report.valid
    assert any(i.field == "fifa_code" for i in report.issues)


def test_duplicate_code_rejected():
    manifest = _manifest([_record("ITA"), _record("ITA")])
    report = validate_manifest(manifest, expected=2)
    assert not report.valid
    assert any("duplicate" in i.message for i in report.issues)


def test_wrong_count_rejected():
    manifest = _manifest([_record("ITA")])
    report = validate_manifest(manifest, expected=211)
    assert not report.valid
    assert any("DATASET INVALID" in i.message for i in report.issues)


def test_unknown_confederation_rejected():
    record = _record(confed="XYZ")
    report = validate_manifest(_manifest([record]), expected=1)
    assert not report.valid
    assert any(i.field == "confederation" for i in report.issues)


def test_checksum_mismatch_rejected():
    manifest = _manifest([_record("ITA")])
    manifest.checksum = "deadbeef"
    report = validate_manifest(manifest, expected=1)
    assert not report.valid
    assert any(i.field == "checksum" for i in report.issues)


def test_validate_record_list():
    report = validate_record_list([_record("ITA"), _record("FRA")], expected=2)
    assert report.valid


def test_missing_flag_url_rejected():
    record = _record("ITA")
    record.flag_url = ""
    report = validate_manifest(_manifest([record]), expected=1)
    assert not report.valid
    assert any(i.field == "flag_url" for i in report.issues)
