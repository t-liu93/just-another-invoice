"""Read models for immutable M12 PDF artifacts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from jai.models._enums import DocumentArtifactKind, DocumentArtifactReason


class DocumentArtifactRead(BaseModel):
    id: uuid.UUID
    artifact_kind: DocumentArtifactKind
    sha256: str
    render_fingerprint: str
    locale: str
    filename: str
    creation_reason: DocumentArtifactReason
    renderer_version: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentArtifactListResponse(BaseModel):
    items: list[DocumentArtifactRead]
