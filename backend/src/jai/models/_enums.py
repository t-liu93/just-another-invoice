"""Enum definitions shared across ORM models.

This module will grow as business entities are introduced (M1+).
"""
from __future__ import annotations

import enum


class SettingLevel(enum.StrEnum):
    """Hierarchy level for the three-layer settings system.

    ``USER`` > ``COMPANY`` > ``GLOBAL`` — lookup falls back from the most
    specific level to the most general.
    """

    GLOBAL = "GLOBAL"
    COMPANY = "COMPANY"
    USER = "USER"


class AddressType(enum.StrEnum):
    """Address type for customer addresses (billing or shipping)."""

    BILLING = "BILLING"
    SHIPPING = "SHIPPING"


class VatTreatmentSide(enum.StrEnum):
    """Which side of a transaction a VAT treatment applies to."""

    SALES = "SALES"
    PURCHASE = "PURCHASE"


class VatTreatmentEffect(enum.StrEnum):
    """How a VAT treatment affects the tax amount on a line item."""

    APPLY_RATE = "APPLY_RATE"
    ZERO_REVERSE = "ZERO_REVERSE"
    ZERO_EXPORT = "ZERO_EXPORT"
    EXEMPT = "EXEMPT"


class InvoiceStatus(enum.StrEnum):
    """Lifecycle status of an invoice."""

    DRAFT = "DRAFT"
    SENT = "SENT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class InvoicePaidStatus(enum.StrEnum):
    """Payment status of an invoice."""

    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"


class InvoiceTaxMode(enum.StrEnum):
    """Whether VAT is calculated per-line or per-document."""

    LINE = "LINE"
    DOCUMENT = "DOCUMENT"


class DiscountType(enum.StrEnum):
    """Type of discount applied to a line or document."""

    NONE = "NONE"
    PERCENTAGE = "PERCENTAGE"
    FIXED = "FIXED"


class NumberSequenceScope(enum.StrEnum):
    """Whether a sequence counter is scoped to the company or a specific customer."""

    COMPANY = "COMPANY"
    CUSTOMER = "CUSTOMER"


class QuoteStatus(enum.StrEnum):
    """Lifecycle status of a quote."""

    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ContentBlockKind(enum.StrEnum):
    """Category of a standard content block."""

    WARRANTY = "WARRANTY"
    TERMS = "TERMS"
    BANK = "BANK"
    PAYMENT_TERMS = "PAYMENT_TERMS"


class DocumentTemplateScope(enum.StrEnum):
    """Which document types a document template applies to."""

    QUOTE = "QUOTE"
    INVOICE = "INVOICE"
    BOTH = "BOTH"


class RecurringFrequency(enum.StrEnum):
    """How often a recurring expense template fires.

    Used by ``models.recurring_expense.RecurringExpense.frequency``.
    """

    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    YEARLY = "YEARLY"


class PaidBy(enum.StrEnum):
    """Payment source indicator for an expense (D2 / M8.5).

    Indicates whether the expense was paid from the private account or the
    business account.  **Pure bookkeeping – does not affect any calculation.**
    Default: ``BUSINESS``.
    """

    PRIVATE = "PRIVATE"
    BUSINESS = "BUSINESS"


class ExpenseKind(enum.StrEnum):
    """Whether an expense is a normal purchase or a mileage projection."""

    PURCHASE = "PURCHASE"
    MILEAGE = "MILEAGE"


class MileageTripOwnership(enum.StrEnum):
    """Transport ownership boundary for a mileage trip."""

    PRIVATE = "PRIVATE"


class EmailRelatedType(enum.StrEnum):
    """Which document type an email_log row relates to (M9 step 6).

    Uses a generic ``(related_type, related_id)`` pair instead of multiple
    nullable FKs (red-line 6).
    """

    INVOICE = "INVOICE"
    QUOTE = "QUOTE"


class EmailStatus(enum.StrEnum):
    """Send-attempt status for an email_log row (M9 step 6).

    ``SENT``   – aiosmtplib accepted the message without error.
    ``FAILED`` – sending raised an exception; ``error_message`` has details.
    """

    SENT = "SENT"
    FAILED = "FAILED"
