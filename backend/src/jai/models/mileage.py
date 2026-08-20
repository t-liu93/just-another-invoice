"""ORM models for M11 private-transport mileage expenses."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import MileageTripOwnership

_DECIMAL = Numeric(18, 3)


class MileageTransportType(Base):
    __tablename__ = "mileage_transport_type"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MileageRate(Base):
    __tablename__ = "mileage_rate"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    transport_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mileage_transport_type.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rate_per_km: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MileageTrip(Base):
    __tablename__ = "mileage_trip"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    ownership: Mapped[MileageTripOwnership] = mapped_column(
        Text, nullable=False, server_default=text("'PRIVATE'")
    )
    transport_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mileage_transport_type.id", ondelete="SET NULL"),
        nullable=True,
    )
    transport_type_name: Mapped[str] = mapped_column(Text, nullable=False)
    rate_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mileage_rate.id", ondelete="SET NULL"), nullable=True
    )
    # Rule scope is an immutable fact independent of the selected trip type.
    # A rule may later be edited from type-specific to general (or to another
    # type), so audit history must never recover this from the live rule.
    rate_transport_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rate_transport_type_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    rate_per_km: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    trip_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    one_way_distance_km: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    total_distance_km: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    round_trip: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    calculated_amount: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    origin_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MileageRateAdjustment(Base):
    __tablename__ = "mileage_rate_adjustment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mileage_trip.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    old_rate_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    new_rate_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    old_rate_transport_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    new_rate_transport_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    old_rate_transport_type_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_rate_transport_type_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_rate_effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    new_rate_effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    old_rate_per_km: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    new_rate_per_km: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    old_amount: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    new_amount: Mapped[object] = mapped_column(_DECIMAL, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
