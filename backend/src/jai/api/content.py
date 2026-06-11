"""Content library API routes – document templates / content blocks / note
templates CRUD (M6 step 4).

Endpoints:
  GET    /api/v1/document-templates              – list (optionally filter by applies_to)
  POST   /api/v1/document-templates              – create
  GET    /api/v1/document-templates/{id}         – get by id
  PUT    /api/v1/document-templates/{id}         – update
  DELETE /api/v1/document-templates/{id}         – delete (cascade lines)

  GET    /api/v1/content-blocks                  – list (optionally filter by kind)
  POST   /api/v1/content-blocks                  – create
  GET    /api/v1/content-blocks/{id}             – get by id
  PUT    /api/v1/content-blocks/{id}             – update
  DELETE /api/v1/content-blocks/{id}             – delete

  GET    /api/v1/note-templates                  – list all
  POST   /api/v1/note-templates                  – create
  GET    /api/v1/note-templates/{id}             – get by id
  PUT    /api/v1/note-templates/{id}             – update
  DELETE /api/v1/note-templates/{id}             – delete
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import ContentBlockKind, DocumentTemplateScope
from jai.models.user import User
from jai.schemas.content import (
    ContentBlockRead,
    ContentBlockWrite,
    DocumentTemplateRead,
    DocumentTemplateWrite,
    NoteTemplateRead,
    NoteTemplateWrite,
)
from jai.services import content as content_svc

router = APIRouter(prefix="/api/v1", tags=["content"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _owner_only(user: User) -> None:
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required."
        )


def _require_company_id(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


# ---------------------------------------------------------------------------
# Document Template CRUD
# ---------------------------------------------------------------------------


@router.get("/document-templates", response_model=list[DocumentTemplateRead])
async def list_document_templates_endpoint(
    applies_to: DocumentTemplateScope | None = Query(default=None),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentTemplateRead]:
    """List document templates for the current company, optionally filtered."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await content_svc.list_document_templates(
        session, company_id, applies_to=applies_to
    )


@router.post(
    "/document-templates",
    response_model=DocumentTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_template_endpoint(
    body: DocumentTemplateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentTemplateRead:
    """Create a new document template with lines."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await content_svc.create_document_template(
            session, body=body, company_id=company_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/document-templates/{template_id}", response_model=DocumentTemplateRead)
async def get_document_template_endpoint(
    template_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentTemplateRead:
    """Get a document template by ID."""
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await content_svc.get_document_template(session, template_id, company_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document template not found."
        )
    return result


@router.put("/document-templates/{template_id}", response_model=DocumentTemplateRead)
async def update_document_template_endpoint(
    template_id: uuid.UUID,
    body: DocumentTemplateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentTemplateRead:
    """Update a document template (replace all fields + lines)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await content_svc.update_document_template(
            session, template_id, body=body, company_id=company_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document template not found."
        )
    return result


@router.delete(
    "/document-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document_template_endpoint(
    template_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a document template (cascade removes lines)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    deleted = await content_svc.delete_document_template(
        session, template_id, company_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document template not found."
        )


# ---------------------------------------------------------------------------
# Content Block CRUD
# ---------------------------------------------------------------------------


@router.get("/content-blocks", response_model=list[ContentBlockRead])
async def list_content_blocks_endpoint(
    kind: ContentBlockKind | None = Query(default=None),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> list[ContentBlockRead]:
    """List content blocks for the current company, optionally filtered by kind."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await content_svc.list_content_blocks(session, company_id, kind=kind)


@router.post(
    "/content-blocks",
    response_model=ContentBlockRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_block_endpoint(
    body: ContentBlockWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ContentBlockRead:
    """Create a new content block."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await content_svc.create_content_block(
            session, body=body, company_id=company_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/content-blocks/{block_id}", response_model=ContentBlockRead)
async def get_content_block_endpoint(
    block_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ContentBlockRead:
    """Get a content block by ID."""
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await content_svc.get_content_block(session, block_id, company_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content block not found."
        )
    return result


@router.put("/content-blocks/{block_id}", response_model=ContentBlockRead)
async def update_content_block_endpoint(
    block_id: uuid.UUID,
    body: ContentBlockWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ContentBlockRead:
    """Update a content block."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await content_svc.update_content_block(
            session, block_id, body=body, company_id=company_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content block not found."
        )
    return result


@router.delete("/content-blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content_block_endpoint(
    block_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a content block."""
    _owner_only(user)
    company_id = _require_company_id(user)
    deleted = await content_svc.delete_content_block(session, block_id, company_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content block not found."
        )


# ---------------------------------------------------------------------------
# Note Template CRUD
# ---------------------------------------------------------------------------


@router.get("/note-templates", response_model=list[NoteTemplateRead])
async def list_note_templates_endpoint(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> list[NoteTemplateRead]:
    """List note templates for the current company."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await content_svc.list_note_templates(session, company_id)


@router.post(
    "/note-templates",
    response_model=NoteTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_note_template_endpoint(
    body: NoteTemplateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> NoteTemplateRead:
    """Create a new note template."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await content_svc.create_note_template(
            session, body=body, company_id=company_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/note-templates/{template_id}", response_model=NoteTemplateRead)
async def get_note_template_endpoint(
    template_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> NoteTemplateRead:
    """Get a note template by ID."""
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await content_svc.get_note_template(session, template_id, company_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note template not found."
        )
    return result


@router.put("/note-templates/{template_id}", response_model=NoteTemplateRead)
async def update_note_template_endpoint(
    template_id: uuid.UUID,
    body: NoteTemplateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> NoteTemplateRead:
    """Update a note template."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await content_svc.update_note_template(
            session, template_id, body=body, company_id=company_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note template not found."
        )
    return result


@router.delete(
    "/note-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_note_template_endpoint(
    template_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a note template."""
    _owner_only(user)
    company_id = _require_company_id(user)
    deleted = await content_svc.delete_note_template(session, template_id, company_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note template not found."
        )
