"""Company API routes – business profile CRUD (owner-only, M2 step 1).

Endpoints:
  - ``GET  /api/v1/company`` – read the singleton company profile.
  - ``PUT  /api/v1/company`` – create or update the company profile.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.company import CompanyRead, CompanyWrite
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
