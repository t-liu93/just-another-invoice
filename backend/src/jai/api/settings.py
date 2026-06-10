"""Settings API routes – SMTP configuration, numbering config, user preferences.

Endpoints:
  - ``GET  /api/v1/settings/smtp``                    – SMTP config (desensitised).
  - ``PUT  /api/v1/settings/smtp``                    – update SMTP config.
  - ``POST /api/v1/settings/smtp/test``               – send test email.
  - ``GET  /api/v1/settings/numbering``               – invoice numbering config.
  - ``PUT  /api/v1/settings/numbering``               – update numbering config.
  - ``GET  /api/v1/settings/invoice-number-sequence`` – next sequence + preview.
  - ``PUT  /api/v1/settings/invoice-number-sequence`` – advance sequence (forward only).
  - ``GET  /api/v1/settings/me``                      – current user preferences.
  - ``PUT  /api/v1/settings/me``                      – update user preferences.
"""

from __future__ import annotations

from datetime import date

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
    InvoiceNumberSequenceRead,
    InvoiceNumberSequenceWrite,
    SmtpSettings,
    SmtpSettingsRead,
    UserPreferences,
)
from jai.services import email as email_svc
from jai.services.numbering import (
    advance_sequence,
    get_next_sequence_info,
    needs_sequence_placeholder,
    validate_template,
)
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
    The ``preview`` field is populated with the next invoice number preview.
    """
    _owner_only(user)
    if user.company_id is None:
        return InvoiceNumberingConfig()

    cfg = await get_setting(
        session,
        SETTING_KEY_INVOICE_NUMBERING,
        level=SettingLevel.COMPANY,
        scope_id=user.company_id,
        value_type=InvoiceNumberingConfig,
    )
    config = cfg if cfg is not None else InvoiceNumberingConfig()

    # Attach preview
    _, preview = await get_next_sequence_info(
        session,
        user.company_id,
        date.today(),
        numbering_config=config,
    )
    config.preview = preview
    return config


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

    # Validate template: reject unknown placeholders and injection attempts.
    try:
        validate_template(body.template)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    # Require at least one sequence-type placeholder to guarantee unique numbers.
    if not needs_sequence_placeholder(body.template):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Template must contain at least one company-level {{SEQUENCE:n}} "
                "placeholder to guarantee unique invoice numbers. "
                "{{CUSTOMER_SEQUENCE:n}} may be used only together with {{SEQUENCE:n}}."
            ),
        )

    # Strip the read-only preview field before persisting.
    await set_setting(
        session,
        SETTING_KEY_INVOICE_NUMBERING,
        body.model_copy(update={"preview": None}),
        level=SettingLevel.COMPANY,
        scope_id=user.company_id,
    )
    await session.commit()

    _, preview = await get_next_sequence_info(
        session,
        user.company_id,
        date.today(),
        numbering_config=body,
    )
    body.preview = preview
    return body


# ---------------------------------------------------------------------------
# Invoice number sequence (M5 step 2)
# ---------------------------------------------------------------------------


async def _get_numbering_config(
    session: AsyncSession,
    company_id: object,
) -> InvoiceNumberingConfig:
    """Load the COMPANY-level numbering config, returning defaults if absent."""
    import uuid as _uuid
    company_uuid = company_id if isinstance(company_id, _uuid.UUID) else _uuid.UUID(str(company_id))
    cfg = await get_setting(
        session,
        SETTING_KEY_INVOICE_NUMBERING,
        level=SettingLevel.COMPANY,
        scope_id=company_uuid,
        value_type=InvoiceNumberingConfig,
    )
    return cfg if cfg is not None else InvoiceNumberingConfig()


@router.get("/invoice-number-sequence", response_model=InvoiceNumberSequenceRead)
async def get_invoice_number_sequence(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceNumberSequenceRead:
    """Return the current next sequence value and a preview of the next invoice number."""
    _owner_only(user)
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company profile must be created first.",
        )

    config = await _get_numbering_config(session, user.company_id)
    next_seq, preview = await get_next_sequence_info(
        session,
        user.company_id,
        date.today(),
        numbering_config=config,
    )
    return InvoiceNumberSequenceRead(next_sequence=next_seq, preview_number=preview)


@router.put("/invoice-number-sequence", response_model=InvoiceNumberSequenceRead)
async def update_invoice_number_sequence(
    body: InvoiceNumberSequenceWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceNumberSequenceRead:
    """Advance the company invoice sequence to a new (higher) value.

    The new value must be strictly greater than the current next_sequence.
    Useful for migrating from an existing invoice series or skipping reserved
    numbers.
    """
    _owner_only(user)
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company profile must be created first.",
        )

    config = await _get_numbering_config(session, user.company_id)
    try:
        new_next, preview = await advance_sequence(
            session,
            user.company_id,
            body.next_sequence,
            numbering_config=config,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    await session.commit()
    return InvoiceNumberSequenceRead(next_sequence=new_next, preview_number=preview)


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
