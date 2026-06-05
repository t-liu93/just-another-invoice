"""Pydantic models for typed setting values and setting key constants.

Each setting stored in the ``setting`` table has a JSONB ``value`` column.
This module defines the typed schemas that values are parsed into, ensuring
no ``'YES'/'NO'`` string comparisons leak into business logic (red-line 5).

Key constants
-------------
Setting keys are centralised here so that callers never hard-code raw
strings.  All keys match the names used in ``docs/plan/milestones/M1.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr

# ---------------------------------------------------------------------------
# Setting key constants – single source of truth
# ---------------------------------------------------------------------------

#: Global flag: ``true`` after the first owner completes onboarding (MFA
#: binding).  When ``true``, public registration is permanently closed.
SETTING_KEY_ONBOARDING_COMPLETED: str = "onboarding.completed"

#: SMTP connection parameters (nested dict via ``SmtpSettings``).
SETTING_KEY_SMTP: str = "smtp"

#: Auto-generated JWT signing secret (nested via ``AuthSecretState``).  Lives
#: at ``GLOBAL`` level; generated on first boot when no ``AUTH_SECRET`` env
#: override is provided.  Never exposed by any API endpoint.
SETTING_KEY_AUTH_SECRET: str = "auth.secret"


# ---------------------------------------------------------------------------
# Onboarding state (GLOBAL level)
# ---------------------------------------------------------------------------


class OnboardingState(BaseModel):
    """Tracks whether the initial owner onboarding has been completed.

    Stored at ``GLOBAL`` level with key ``SETTING_KEY_ONBOARDING_COMPLETED``
    (``"onboarding.completed"``).
    """

    completed: bool = False


# ---------------------------------------------------------------------------
# Auth secret (GLOBAL level)
# ---------------------------------------------------------------------------


class AuthSecretState(BaseModel):
    """The JWT signing secret, auto-generated and persisted on first boot.

    Stored at ``GLOBAL`` level with key ``SETTING_KEY_AUTH_SECRET``
    (``"auth.secret"``).  An explicit ``AUTH_SECRET`` environment variable
    takes precedence over this stored value (see ``jai.auth.secret``).
    """

    secret: str


# ---------------------------------------------------------------------------
# SMTP configuration (GLOBAL level, env fallback)
# ---------------------------------------------------------------------------


class SmtpSettings(BaseModel):
    """SMTP connection parameters used by the email service.

    Stored at ``GLOBAL`` level with key ``SETTING_KEY_SMTP`` (``"smtp"``).
    When read for display, the ``password`` field is masked (see
    ``SmtpSettingsRead`` in the API layer, step 4).
    """

    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: EmailStr = ""
    from_name: str = ""
    use_tls: bool = True
    use_ssl: bool = False


class SmtpSettingsRead(BaseModel):
    """SMTP settings as returned by the API – password is desensitised."""

    host: str = ""
    port: int = 587
    username: str = ""
    password_set: bool = False
    from_email: str = ""
    from_name: str = ""
    use_tls: bool = True
    use_ssl: bool = False
