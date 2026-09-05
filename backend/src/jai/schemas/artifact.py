"""Read models for immutable M12 PDF artifacts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from jai.models._enums import (
    DocumentArtifactKind,
    DocumentArtifactReason,
    DocumentArtifactValidationCheckStatus,
    DocumentArtifactValidationConfidence,
    DocumentArtifactValidationField,
    DocumentArtifactValidationStatus,
)


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


class DocumentArtifactValidationCheck(BaseModel):
    """One advisory comparison between a PDF and an issued-document snapshot."""

    field: DocumentArtifactValidationField
    status: DocumentArtifactValidationCheckStatus
    expected_value: str | None
    observed_value: str | None
    note: str | None


class DocumentArtifactValidationRead(BaseModel):
    """Non-persistent advisory result for a selected formal-artifact PDF."""

    file_sha256: str
    status: DocumentArtifactValidationStatus
    confidence: DocumentArtifactValidationConfidence | None
    summary: str
    total_pages: int
    checked_pages: list[int]
    checks: list[DocumentArtifactValidationCheck]


# These errors deliberately model FastAPI's existing ``{"detail": {…}}``
# envelope.  The separate response types keep each endpoint/status code set
# machine-readable in OpenAPI instead of making the frontend infer it from a
# prose description.
class DocumentArtifactNotFoundErrorDetail(BaseModel):
    code: Literal["ARTIFACT_UPLOAD_NOT_FOUND"]
    message: str


class DocumentArtifactNotFoundErrorResponse(BaseModel):
    detail: DocumentArtifactNotFoundErrorDetail


class DocumentArtifactUploadConflictErrorDetail(BaseModel):
    code: Literal["ARTIFACT_ALREADY_EXISTS"]
    message: str


class DocumentArtifactUploadConflictErrorResponse(BaseModel):
    detail: DocumentArtifactUploadConflictErrorDetail


class DocumentArtifactValidationConflictErrorDetail(BaseModel):
    code: Literal["ARTIFACT_ALREADY_EXISTS", "AI_NOT_CONFIGURED"]
    message: str


class DocumentArtifactValidationConflictErrorResponse(BaseModel):
    detail: DocumentArtifactValidationConflictErrorDetail


class DocumentArtifactInvalidFileErrorDetail(BaseModel):
    code: Literal["INVALID_ARTIFACT_FILE"]
    message: str


class DocumentArtifactInvalidFileErrorResponse(BaseModel):
    detail: DocumentArtifactInvalidFileErrorDetail


class DocumentArtifactInvalidRequestErrorDetail(BaseModel):
    """Stable non-file 422 for the two M13 artifact-upload routes."""

    code: Literal["INVALID_ARTIFACT_UPLOAD_REQUEST"]
    message: str


class DocumentArtifactInvalidRequestErrorResponse(BaseModel):
    detail: DocumentArtifactInvalidRequestErrorDetail


class DocumentArtifactUnprocessableErrorResponse(BaseModel):
    """The endpoint's file-input and path/query validation alternatives."""

    detail: DocumentArtifactInvalidFileErrorDetail | DocumentArtifactInvalidRequestErrorDetail


class DocumentArtifactValidationFailedErrorDetail(BaseModel):
    code: Literal["AI_VALIDATION_FAILED"]
    message: str


class DocumentArtifactValidationFailedErrorResponse(BaseModel):
    detail: DocumentArtifactValidationFailedErrorDetail
