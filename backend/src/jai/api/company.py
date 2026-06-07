"""Company API routes – business profile CRUD + logo management (M2).

Endpoints:
  - ``GET  /api/v1/company``      – read the singleton company profile.
  - ``PUT  /api/v1/company``      – create or update the company profile.
  - ``PUT  /api/v1/company/logo``  – upload or replace the company logo.
  - ``DELETE /api/v1/company/logo`` – remove the company logo.
  - ``GET  /api/v1/company/logo``  – serve the company logo binary.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.company import CompanyLogoRead, CompanyRead, CompanyWrite
from jai.services.assets import MAX_UPLOAD_BYTES, AssetValidationError
from jai.services.assets import clear_company_logo as _clear_logo
from jai.services.assets import get_company_logo as _get_logo
from jai.services.assets import set_company_logo as _set_logo
from jai.services.company import company_to_read, get_company, upsert_company

router = APIRouter(prefix="/api/v1/company", tags=["company"])


def _owner_only(user: User) -> None:
    """Ensure the authenticated user has the owner role."""
    if user.role != "owner":
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required.",
        )


# ---------------------------------------------------------------------------
# Company profile CRUD (step 1)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=CompanyRead,
    responses={204: {"description": "No company profile yet", "content": {}}},
)
async def read_company(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CompanyRead | Response:
    """Return the company profile.

    Returns ``204 No Content`` if the company has not been created yet.
    """
    _owner_only(user)
    company = await get_company(session)
    if company is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return company_to_read(company)


@router.put("", response_model=CompanyRead)
async def write_company(
    body: CompanyWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CompanyRead:
    """Create or update the singleton company profile.

    On first creation, also:
      - Set ``onboarding.completed = true``.
      - Link the owner's ``company_id`` to the new company.
    """
    _owner_only(user)
    result = await upsert_company(session, body, owner=user)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Logo upload / serve / delete (step 2)
# ---------------------------------------------------------------------------


@router.put(
    "/logo",
    response_model=CompanyLogoRead,
    responses={
        400: {"description": "Invalid file type, too large, or no company"},
    },
)
async def upload_logo(
    file: UploadFile,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CompanyLogoRead:
    """Upload or replace the company logo.

    Accepts PNG, JPEG, WebP, or SVG.  SVG content is sanitized (red-line 7).
    Maximum size: 512 KB.
    """
    _owner_only(user)

    # Read with size limit to avoid loading arbitrarily large files.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    mime_type = file.content_type or "application/octet-stream"
    filename = file.filename

    try:
        result = await _set_logo(session, content, mime_type, filename)
    except AssetValidationError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    await session.commit()
    return CompanyLogoRead(**result)


@router.delete(
    "/logo",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "No logo to delete"},
    },
)
async def delete_logo(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove the company logo."""
    _owner_only(user)
    deleted = await _clear_logo(session)
    await session.commit()
    if not deleted:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/logo",
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
                "image/svg+xml": {"schema": {"type": "string", "format": "binary"}},
            }
        },
        404: {"description": "No logo uploaded"},
    },
)
async def serve_logo(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Serve the company logo binary with caching headers."""
    _owner_only(user)
    result = await _get_logo(session)
    if result is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    content, mime_type = result
    # Generate ETag from content hash for cache validation.
    etag = '"' + hashlib.sha256(content).hexdigest()[:32] + '"'
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": etag,
        },
    )
