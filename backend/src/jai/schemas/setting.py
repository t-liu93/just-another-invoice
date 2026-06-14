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

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

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

#: Invoice numbering template config (COMPANY level).  M2 stores only the
#: configuration; the rendering engine is implemented in M5.
SETTING_KEY_INVOICE_NUMBERING: str = "invoice.numbering"

#: Quote numbering template config (COMPANY level).
SETTING_KEY_QUOTE_NUMBERING: str = "quote.numbering"

#: Default valid days for new quotes (COMPANY level, int).
SETTING_KEY_QUOTE_DEFAULT_VALID_DAYS: str = "quote.default_valid_days"

#: User-level preferences (theme, locale, …).
SETTING_KEY_USER_PREFERENCES: str = "user.preferences"

#: AI / receipt-extraction configuration (GLOBAL level, env fallback).
SETTING_KEY_AI: str = "ai"

#: Company-level default document locale (used for PDF/email language resolution).
SETTING_KEY_DOCUMENT_DEFAULTS: str = "document.default_locale"


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


# ---------------------------------------------------------------------------
# Invoice numbering config (COMPANY level, stored only – M5 consumes)
# ---------------------------------------------------------------------------


class InvoiceNumberingConfig(BaseModel):
    """Invoice numbering template and sequence configuration.

    Stored at ``COMPANY`` level with key ``SETTING_KEY_INVOICE_NUMBERING``
    (``"invoice.numbering"``).

    ``sequence_start`` is consumed only once, when the sequence row is first
    created; changing it afterwards has no effect on an already-running counter.
    ``preview`` is read-only (populated by the GET endpoint, ignored on PUT).
    """

    template: str = Field(
        default="{{SERIES:INV}}-{{SEQUENCE:6}}",
        description=(
            "Numbering template. Supported placeholders: "
            "{{SERIES:VALUE}}, {{SEQUENCE:n}}, {{CUSTOMER_SERIES}}, "
            "{{CUSTOMER_SEQUENCE:n}}, {{DATE:format}}."
        ),
    )
    sequence_start: int = Field(
        default=1,
        ge=1,
        description=(
            "Starting number used only when the sequence row is first created. "
            "Changing this after the first invoice has no effect."
        ),
    )
    preview: str | None = Field(
        default=None,
        description="Read-only: preview of the next invoice number (ignored on PUT).",
    )


# ---------------------------------------------------------------------------
# Invoice number sequence read/write (M5 step 2)
# ---------------------------------------------------------------------------


class InvoiceNumberSequenceRead(BaseModel):
    """Response for GET/PUT /settings/invoice-number-sequence."""

    next_sequence: int = Field(description="The next sequence value that will be issued.")
    preview_number: str = Field(description="Preview of the next invoice number.")


class InvoiceNumberSequenceWrite(BaseModel):
    """Request body for PUT /settings/invoice-number-sequence."""

    next_sequence: int = Field(
        ge=1,
        description=(
            "New next sequence value. Must be strictly greater than the current "
            "next_sequence if a sequence already exists (forward-only)."
        ),
    )


# ---------------------------------------------------------------------------
# Quote numbering config (COMPANY level, stored only – M6 consumes)
# ---------------------------------------------------------------------------


class QuoteNumberingConfig(BaseModel):
    """Quote numbering template and sequence configuration.

    Same shape as ``InvoiceNumberingConfig``; stored at COMPANY level with key
    ``SETTING_KEY_QUOTE_NUMBERING``.
    """

    template: str = Field(
        default="{{SERIES:QUO}}-{{SEQUENCE:6}}",
        description=(
            "Numbering template. Supported placeholders: "
            "{{SERIES:VALUE}}, {{SEQUENCE:n}}, {{CUSTOMER_SERIES}}, "
            "{{CUSTOMER_SEQUENCE:n}}, {{DATE:format}}."
        ),
    )
    sequence_start: int = Field(
        default=1,
        ge=1,
        description=(
            "Starting number used only when the sequence row is first created. "
            "Changing this after the first quote has no effect."
        ),
    )
    preview: str | None = Field(
        default=None,
        description="Read-only: preview of the next quote number (ignored on PUT).",
    )


class QuoteNumberSequenceRead(BaseModel):
    """Response for GET/PUT /settings/quote-number-sequence."""

    next_sequence: int = Field(description="The next sequence value that will be issued.")
    preview_number: str = Field(description="Preview of the next quote number.")


class QuoteNumberSequenceWrite(BaseModel):
    """Request body for PUT /settings/quote-number-sequence."""

    next_sequence: int = Field(
        ge=1,
        description=(
            "New next sequence value. Must be strictly greater than the current "
            "next_sequence if a sequence already exists (forward-only)."
        ),
    )


class QuoteDefaultValidDaysRead(BaseModel):
    """Response for GET /settings/quote-default-valid-days."""

    default_valid_days: int = Field(
        ge=1,
        description="Default number of days until a quote expires.",
    )


class QuoteDefaultValidDaysWrite(BaseModel):
    """Request body for PUT /settings/quote-default-valid-days."""

    default_valid_days: int = Field(
        ge=1,
        description="Default number of days until a quote expires.",
    )


# ---------------------------------------------------------------------------
# User preferences (USER level)
# ---------------------------------------------------------------------------


class UserPreferences(BaseModel):
    """Per-user preferences stored at USER level.

    Stored at ``USER`` level with key ``SETTING_KEY_USER_PREFERENCES``
    (``"user.preferences"``).  Holds the theme selection and the UI language;
    more preferences will be added in later milestones.

    The PUT endpoint replaces the whole object, so the frontend always persists
    the full set (theme + locale together) — see ``composables/userPreferences``.
    """

    theme: Literal["system", "light", "dark"] = Field(
        default="system",
        description="UI theme preference: 'system' follows OS, 'light' or 'dark' overrides.",
    )
    locale: Literal["en", "zh"] = Field(
        default="en",
        description="UI language preference (interface locale).",
    )


# ---------------------------------------------------------------------------
# AI / receipt-extraction configuration (GLOBAL level, env fallback)
# ---------------------------------------------------------------------------


class AiSettings(BaseModel):
    """AI service configuration stored at GLOBAL level.

    Stored with key ``SETTING_KEY_AI`` (``"ai"``).  Mirrors the SMTP settings
    pattern: plain-text JSONB, read-desensitised, env fallback (D1).

    ``receipt_prompt`` is an **optional additive instruction** that is appended
    after the built-in ``DEFAULT_RECEIPT_PROMPT``; leaving it empty means only
    the default is used.  Users can use this field to refine or extend the
    extraction without losing the built-in Dutch BTW context (D15).
    The system always appends the fixed ``_OUTPUT_CONTRACT`` footer so that the
    JSON output schema cannot be accidentally broken by extra instructions.

    ``api_key`` is stored in plain text (same approach as SMTP ``password``).
    It is **never** returned in any API response; only ``api_key_set: bool``
    is exposed (see ``AiSettingsRead``).  Credentials are never written to logs.
    """

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    receipt_prompt: str = ""


class AiSettingsRead(BaseModel):
    """AI settings as returned by the API – ``api_key`` is desensitised.

    ``api_key_set`` replaces the raw key: ``True`` means a key is stored,
    ``False`` means the field is empty.  All other fields are returned as-is
    so the user can inspect and update them.
    """

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key_set: bool = False
    model: str = ""
    receipt_prompt: str = ""


class AiSettingsUpdate(BaseModel):
    """Request body for ``PUT /settings/ai``.

    ``api_key`` semantics mirror ``SmtpSettingsUpdate.password``:
    - ``None`` (field omitted) → keep the existing key unchanged.
    - Empty string ``""`` → clear the stored key.
    - Non-empty string → replace with the new key.

    All other fields are optional; omitted fields keep their current value.
    """

    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = None  # None = keep existing; "" = clear
    model: str | None = None
    receipt_prompt: str | None = None


class AiTestResult(BaseModel):
    """Result from ``POST /settings/ai/test``.

    ``ok`` is ``True`` when the endpoint responded successfully.
    ``multimodal`` is ``True`` when the model accepted an image input without
    error (confirms vision capability).
    ``detail`` carries a human-readable message for failures.
    """

    ok: bool
    multimodal: bool
    detail: str


# ---------------------------------------------------------------------------
# Document default locale (COMPANY level, M9 step 2)
# ---------------------------------------------------------------------------


class DocumentDefaultsSetting(BaseModel):
    """Company-level default locale for PDF / email document rendering.

    Stored at ``COMPANY`` level with key ``SETTING_KEY_DOCUMENT_DEFAULTS``
    (``"document.default_locale"``).

    ``locale`` is the fallback language when neither the export request nor the
    customer record specifies an explicit locale.  Defaults to ``"en"``.

    Resolution chain (D2): export override → customer.locale → this setting → "en".
    """

    locale: Literal["en", "zh"] = Field(
        default="en",
        description="Default document language: 'en' (English) or 'zh' (Chinese).",
    )


class DocumentDefaultsRead(BaseModel):
    """Response body for ``GET /api/v1/settings/document-defaults``."""

    locale: Literal["en", "zh"] = Field(
        default="en",
        description="Current company-level default document language.",
    )


class DocumentDefaultsUpdate(BaseModel):
    """Request body for ``PUT /api/v1/settings/document-defaults``."""

    locale: Literal["en", "zh"] = Field(
        description="New default document language: 'en' or 'zh'.",
    )


# ---------------------------------------------------------------------------
# Email templates (COMPANY level, M9 step 5)
# ---------------------------------------------------------------------------

#: Company-level email templates (invoice + quote, EN/ZH).
SETTING_KEY_EMAIL_TEMPLATES: str = "email.templates"


class EmailTemplate(BaseModel):
    """A single email template with a subject line and plain-text body.

    Both fields may contain placeholder tokens of the form ``{TOKEN_NAME}``.
    Tokens are resolved at send time; unknown tokens are left as-is (not
    raised).  The ``body`` is stored as **plain text + placeholders** – no
    HTML.  HTML conversion (nl2br + HTML-escaping) happens only at render
    time inside ``services/email.render_email_template``.
    """

    subject: str = Field(description="Email subject line (plain text, may contain placeholders).")
    body: str = Field(description="Email body (plain text + placeholders, no HTML).")


class EmailTemplateLocaleMap(BaseModel):
    """Per-locale templates for a single document type."""

    en: EmailTemplate
    zh: EmailTemplate


class EmailTemplatesSetting(BaseModel):
    """Company-level email template configuration.

    Stored at ``COMPANY`` level with key ``SETTING_KEY_EMAIL_TEMPLATES``
    (``"email.templates"``).  Holds one set of ``{en, zh}`` templates for
    each supported document type.

    Body is **plain text + placeholders** (red-line 7: no arbitrary HTML
    stored).  Rendering via ``render_email_template`` converts the body to
    safe HTML at send time.
    """

    invoice: EmailTemplateLocaleMap
    quote: EmailTemplateLocaleMap


# ---------------------------------------------------------------------------
# API-facing read / write models
# ---------------------------------------------------------------------------


class EmailTemplatesRead(BaseModel):
    """Response body for ``GET /api/v1/settings/email-templates``."""

    invoice: EmailTemplateLocaleMap
    quote: EmailTemplateLocaleMap


class EmailTemplatesUpdate(BaseModel):
    """Request body for ``PUT /api/v1/settings/email-templates``."""

    invoice: EmailTemplateLocaleMap
    quote: EmailTemplateLocaleMap


# ---------------------------------------------------------------------------
# Built-in default email templates (fallback when no setting exists – D4)
# ---------------------------------------------------------------------------

DEFAULT_EMAIL_TEMPLATES = EmailTemplatesSetting(
    invoice=EmailTemplateLocaleMap(
        en=EmailTemplate(
            subject="Invoice {INVOICE_NUMBER} from {COMPANY_NAME}",
            body=(
                "Dear {CUSTOMER_NAME},\n\n"
                "Please find attached invoice {INVOICE_NUMBER} dated {DATE}.\n\n"
                "Amount due: {CURRENCY} {AMOUNT_DUE}\n"
                "Due date: {DUE_DATE}\n\n"
                "If you have any questions, please do not hesitate to contact us.\n\n"
                "Kind regards,\n{COMPANY_NAME}"
            ),
        ),
        zh=EmailTemplate(
            subject="{COMPANY_NAME} 发票 {INVOICE_NUMBER}",
            body=(
                "尊敬的 {CUSTOMER_NAME}，\n\n"
                "请查收附件中的发票 {INVOICE_NUMBER}，开票日期：{DATE}。\n\n"
                "应付金额：{CURRENCY} {AMOUNT_DUE}\n"
                "付款截止日：{DUE_DATE}\n\n"
                "如有任何疑问，请随时与我们联系。\n\n"
                "此致\n{COMPANY_NAME}"
            ),
        ),
    ),
    quote=EmailTemplateLocaleMap(
        en=EmailTemplate(
            subject="Quote {QUOTE_NUMBER} from {COMPANY_NAME}",
            body=(
                "Dear {CUSTOMER_NAME},\n\n"
                "Please find attached our quote {QUOTE_NUMBER} dated {DATE}.\n\n"
                "Total amount: {CURRENCY} {TOTAL}\n"
                "Valid until: {VALID_UNTIL}\n\n"
                "We look forward to your response. Please contact us if you have "
                "any questions.\n\n"
                "Kind regards,\n{COMPANY_NAME}"
            ),
        ),
        zh=EmailTemplate(
            subject="{COMPANY_NAME} 报价单 {QUOTE_NUMBER}",
            body=(
                "尊敬的 {CUSTOMER_NAME}，\n\n"
                "请查收附件中的报价单 {QUOTE_NUMBER}，报价日期：{DATE}。\n\n"
                "报价总额：{CURRENCY} {TOTAL}\n"
                "有效期至：{VALID_UNTIL}\n\n"
                "期待您的回复，如有疑问请随时联系我们。\n\n"
                "此致\n{COMPANY_NAME}"
            ),
        ),
    ),
)
