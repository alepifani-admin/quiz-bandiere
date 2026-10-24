"""Unit tests for normalization."""
from app.data.normalize import derive_continent, normalize_fifa_code, to_record


def test_normalize_fifa_code():
    assert normalize_fifa_code("  fra ") == "FRA"
    assert normalize_fifa_code("wal") == "WAL"


def test_derive_continent():
    assert derive_continent("UEFA") == "Europe"
    assert derive_continent("CONMEBOL") == "South America"
    assert derive_continent("UNKNOWN") is None


def test_to_record():
    record = to_record(
        fifa_code="fra", name="France", confederation="UEFA",
        flag_url="https://example.com/fra.png",
    )
    assert record.fifa_code == "FRA"
    assert record.confederation == "UEFA"
    assert record.continent == "Europe"
    assert record.status == "member"
    assert record.confidence == 1.0
