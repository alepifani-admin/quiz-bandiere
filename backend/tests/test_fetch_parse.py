"""Tests for FIFA payload parsing (offline, using a fixture shaped like the
real payload from inside.fifa.com)."""
from datetime import datetime, timezone

from app.data.fetch import records_from_fifa_payload

FIFA_PAYLOAD_FIXTURE = [
    {
        "name": "Afghanistan",
        "confederation": "AFC",
        "flag": {
            "src": "https://api.fifa.com/api/v3/picture/flags-sq-2/AFG",
            "width": 70,
            "height": 46,
        },
        "url": "/associations/AFG",
    },
    {
        "name": "Wales",
        "confederation": "UEFA",
        "flag": {
            "src": "https://api.fifa.com/api/v3/picture/flags-sq-2/WAL",
            "width": 70,
            "height": 46,
        },
        "url": "/associations/WAL",
    },
]


def test_records_from_fifa_payload():
    now = datetime.now(timezone.utc)
    records = records_from_fifa_payload(FIFA_PAYLOAD_FIXTURE, now)
    assert len(records) == 2
    assert records[0].fifa_code == "AFG"
    assert records[0].confederation == "AFC"
    assert records[1].fifa_code == "WAL"
    assert "flags-sq-2/AFG" in records[0].flag_url
    assert records[0].source == "fifa-associations"
