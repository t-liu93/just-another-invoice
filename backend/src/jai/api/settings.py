"""Settings API routes – SMTP configuration (owner-only).

Step 4 endpoints:
  - ``GET  /api/v1/settings/smtp``  – read current SMTP config (password desensitised).
  - ``PUT  /api/v1/settings/smtp``  – update SMTP config.
  - ``POST /api/v1/settings/smtp/test`` – send a test email using the current config.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import SettingLevel
from jai.models.user import User
from jai.schemas.setting import SETTING_KEY_SMTP, SmtpSettings, SmtpSettingsRead
from jai.services import email as email_svc
from jai.services.settings import get_setting, set_setting

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SmtpSettingsUpdate(BaseModel):
    """Body for ``PUT /settings/smtp``."""

    host: str
    port: int = 587
    username: str = ""
    password: str | None = None  # None = keep existing password
    from_email: EmailStr
    from_name: str = ""
    use_tls: bool = True
    use_ssl: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owner_only(user: User) -> None:
    """Ensure the authenticated user has the owner role."""
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required.",
        )


def _to_read_model(cfg: SmtpSettings) -> SmtpSettingsRead:
    """Convert the internal model to the desensitised API response."""
    return SmtpSettingsRead(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password_set=bool(cfg.password),
        from_email=cfg.from_email,
        from_name=cfg.from_name,
        use_tls=cfg.use_tls,
        use_ssl=cfg.use_ssl,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/smtp", response_model=SmtpSettingsRead)
async def get_smtp_settings(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> SmtpSettingsRead:
    """Return the current SMTP settings (password desensitised)."""
    _owner_only(user)

    cfg = await get_setting(
        session,
        SETTING_KEY_SMTP,
        level=SettingLevel.GLOBAL,
        value_type=SmtpSettings,
    )
    if cfg is None:
        return SmtpSettingsRead()
    return _to_read_model(cfg)


@router.put("/smtp", response_model=SmtpSettingsRead)
async def update_smtp_settings(
    body: SmtpSettingsUpdate,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> SmtpSettingsRead:
    """Update SMTP settings. If ``password`` is ``None``, the existing password is kept."""
    _owner_only(user)

    # Load existing to preserve password when not supplied.
    existing = await get_setting(
        session,
        SETTING_KEY_SMTP,
        level=SettingLevel.GLOBAL,
        value_type=SmtpSettings,
    )

    new_password = body.password
    if new_password is None and existing is not None:
        new_password = existing.password

    new_cfg = SmtpSettings(
        host=body.host,
        port=body.port,
        username=body.username,
        password=new_password or "",
        from_email=body.from_email,
        from_name=body.from_name,
        use_tls=body.use_tls,
        use_ssl=body.use_ssl,
    )

    await set_setting(
        session,
        SETTING_KEY_SMTP,
        new_cfg,
        level=SettingLevel.GLOBAL,
    )
    await session.commit()

    return _to_read_model(new_cfg)


@router.post("/smtp/test", status_code=status.HTTP_202_ACCEPTED)
async def test_smtp_settings(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Send a test email to the owner's address using current SMTP config.

    Returns ``{"status": "sent"}`` on success or raises 400 with error detail.
    """
    _owner_only(user)

    try:
        await email_svc.send_test_email(session, user.email)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to send test email: {exc}",
        ) from exc

    return {"status": "sent"}
