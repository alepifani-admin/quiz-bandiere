"""Smoke test: app import and health endpoint."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_root_info() -> None:
    """The root serves either the frontend build (HTML) or the API info JSON
    depending on whether frontend/dist exists."""
    resp = client.get("/")
    assert resp.status_code == 200
    if "text/html" in resp.headers.get("content-type", ""):
        assert b"<html" in resp.content or b"<!doctype html" in resp.content.lower()
    else:
        assert resp.json()["app"] == "FIFA Flag Quiz API"
