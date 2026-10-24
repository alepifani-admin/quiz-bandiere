"""Ingestion data models (Pydantic).

Every record carries source, confidence and needs_review so that no data is
silently assumed (sections 26 and 35).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AssociationRecord(BaseModel):
    fifa_code: str = Field(pattern=r"^[A-Z]{3}$")
    name: str
    name_it: Optional[str] = None
    confederation: str
    continent: Optional[str] = None
    status: str = "member"
    flag_url: str
    flag_asset_path: Optional[str] = None
    needs_review: bool = False
    source: str = "fifa-associations"
    retrieved_at: datetime
    confidence: float = 1.0


class DatasetManifest(BaseModel):
    """Versioned dataset envelope (section 25)."""

    version: str
    timestamp: datetime
    source: str
    record_count: int
    checksum: str
    associations: list[AssociationRecord]


class ValidationIssue(BaseModel):
    record: Optional[str] = None
    field: str
    message: str


class ValidationReport(BaseModel):
    valid: bool
    expected: int
    actual: int
    issues: list[ValidationIssue] = Field(default_factory=list)
