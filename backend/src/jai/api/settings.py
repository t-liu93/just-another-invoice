"""Settings API routes – SMTP configuration, numbering config, user preferences.

Endpoints:
  - ``GET  /api/v1/settings/smtp``         – read current SMTP config (password desensitised).
  - ``PUT  /api/v1/settings/smtp``         – update SMTP config.
  - ``POST /api/v1/settings/smtp/test``    – send a test email using the current config.
  - ``GET  /api/v1/settings/numbering``    – read invoice numbering config (COMPANY level).
  - ``PUT  /api/v1/settings/numbering``    – update invoice numbering config (COMPANY level).
  - ``GET  /api/v1/settings/me``           – read current user's preferences (USER level).
  - ``PUT  /api/v1/settings/me``           – update current user's preferences (USER level).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import SettingLevel
from jai.models.user import User
from jai.schemas.setting import (
    SETTING_KEY_INVOICE_NUMBERING,
    SETTING_KEY_SMTP,
    SETTING_KEY_USER_PREFERENCES,
    InvoiceNumberingConfig,
    SmtpSettings,
    SmtpSettingsRead,
    UserPreferences,
)
from jai.services import email as email_svc
from jai.services.settings import get_effective_setting, get_setting, set_setting

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


# ---------------------------------------------------------------------------
# Invoice numbering configuration (COMPANY level, owner-only)
# ---------------------------------------------------------------------------


@router.get("/numbering", response_model=InvoiceNumberingConfig)
async def get_numbering_config(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceNumberingConfig:
    """Return the invoice numbering configuration for the current company.

    Returns the COMPANY-level setting, or the default if not yet configured.
    The actual numbering engine is implemented in M5; M2 only persists the
    configuration.
    """
    _owner_only(user)
    if user.company_id is None:
        # No company yet – return defaults.
        return InvoiceNumberingConfig()

    cfg = await get_setting(
        session,
        SETTING_KEY_INVOICE_NUMBERING,
        level=SettingLevel.COMPANY,
        scope_id=user.company_id,
        value_type=InvoiceNumberingConfig,
    )
    return cfg if cfg is not None else InvoiceNumberingConfig()


@router.put("/numbering", response_model=InvoiceNumberingConfig)
async def update_numbering_config(
    body: InvoiceNumberingConfig,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceNumberingConfig:
    """Update the invoice numbering configuration for the current company.

    Stored at COMPANY level (scope = company.id).  Requires owner role.
    """
    _owner_only(user)
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company profile must be created before configuring numbering.",
        )

    await set_setting(
        session,
        SETTING_KEY_INVOICE_NUMBERING,
        body,
        level=SettingLevel.COMPANY,
        scope_id=user.company_id,
    )
    await session.commit()
    return body


# ---------------------------------------------------------------------------
# User preferences (USER level)
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserPreferences)
async def get_user_preferences(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> UserPreferences:
    """Return the current user's effective preferences.

    Resolves via ``USER(user.id) → COMPANY(user.company_id) → GLOBAL``.
    Returns defaults when no setting exists at any level.
    """
    prefs = await get_effective_setting(
        session,
        SETTING_KEY_USER_PREFERENCES,
        user=user,
        value_type=UserPreferences,
    )
    return prefs if prefs is not None else UserPreferences()


@router.put("/me", response_model=UserPreferences)
async def update_user_preferences(
    body: UserPreferences,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> UserPreferences:
    """Update the current user's preferences (USER level, scope = user.id)."""
    await set_setting(
        session,
        SETTING_KEY_USER_PREFERENCES,
        body,
        level=SettingLevel.USER,
        scope_id=user.id,
    )
    await session.commit()
    return body
