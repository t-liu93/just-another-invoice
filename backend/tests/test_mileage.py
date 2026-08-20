"""Unit tests for M11 Step 1 mileage rate and calculation helpers."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from jai.schemas.mileage import (
    MileageCalculationRequest,
    MileageRateAdjustmentRead,
    MileageRateWrite,
    validate_nonnegative_numeric_18_3,
)
from jai.services.mileage import (
    RateCandidate,
    calculate_mileage_amount,
    derive_total_distance,
    resolve_effective_rate,
)


def _rule(
    effective_from: date,
    rate_per_km: str,
    transport_type_id: uuid.UUID | None = None,
) -> RateCandidate:
    return RateCandidate(
        id=uuid.uuid4(),
        transport_type_id=transport_type_id,
        effective_from=effective_from,
        rate_per_km=Decimal(rate_per_km),
    )


class TestEffectiveRateResolution:
    def test_type_specific_rule_wins_over_general_rule(self) -> None:
        car_id = uuid.uuid4()
        general = _rule(date(2026, 1, 1), "0.250")
        override = _rule(date(2025, 1, 1), "0.300", car_id)

        assert resolve_effective_rate([general, override], date(2026, 6, 1), car_id) == override

    def test_falls_back_to_newest_general_rule(self) -> None:
        bicycle_id = uuid.uuid4()
        old_general = _rule(date(2024, 1, 1), "0.230")
        new_general = _rule(date(2026, 1, 1), "0.250")

        assert (
            resolve_effective_rate([old_general, new_general], date(2026, 1, 1), bicycle_id)
            == new_general
        )

    def test_exact_effective_date_is_inclusive(self) -> None:
        rule = _rule(date(2026, 1, 1), "0.250")
        assert resolve_effective_rate([rule], date(2026, 1, 1), None) == rule

    def test_missing_rule_is_configuration_error(self) -> None:
        with pytest.raises(LookupError, match="No applicable mileage rate"):
            resolve_effective_rate([], date(2026, 1, 1), uuid.uuid4())


class TestMileageCalculation:
    def test_return_trip_doubles_and_preserves_three_decimal_distance(self) -> None:
        assert derive_total_distance(Decimal("12.500"), True) == Decimal("25.000")
        assert derive_total_distance(Decimal("12.500"), False) == Decimal("12.500")

    @pytest.mark.parametrize("value", [Decimal("0.0004"), Decimal("1.2346")])
    def test_distance_with_more_than_three_decimal_places_is_rejected(self, value: Decimal) -> None:
        with pytest.raises(ValueError, match="at most 3 decimal places"):
            derive_total_distance(value, False)

    def test_return_trip_is_derived_from_the_persisted_one_way_snapshot(self) -> None:
        one_way = Decimal("1.234")
        assert derive_total_distance(one_way, True) == Decimal("2.468")

    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
    def test_distance_must_be_positive_and_finite(self, value: Decimal) -> None:
        with pytest.raises(ValueError, match="positive finite"):
            derive_total_distance(value, False)

    @pytest.mark.parametrize(
        ("value", "round_trip"),
        [
            (Decimal("1000000000000000.000"), False),
            (Decimal("500000000000000.000"), True),
        ],
    )
    def test_distance_must_fit_numeric_18_3(self, value: Decimal, round_trip: bool) -> None:
        with pytest.raises(ValueError, match="NUMERIC\\(18,3\\) limit"):
            derive_total_distance(value, round_trip)

    def test_final_amount_rounds_half_up_to_cents(self) -> None:
        assert calculate_mileage_amount(Decimal("1.000"), Decimal("0.005")) == Decimal("0.01")

    def test_decimal_precision_is_retained_until_final_rounding(self) -> None:
        assert calculate_mileage_amount(Decimal("25.000"), Decimal("0.250")) == Decimal("6.25")

    @pytest.mark.parametrize("rate", [Decimal("0.230"), Decimal("0.001")])
    def test_positive_subcent_amount_rounds_to_zero(self, rate: Decimal) -> None:
        assert calculate_mileage_amount(Decimal("0.001"), rate) == Decimal("0.00")

    @pytest.mark.parametrize(
        ("total_distance_km", "rate_per_km"),
        [
            (Decimal("-0.001"), Decimal("0.230")),
            (Decimal("0.001"), Decimal("-0.001")),
            (Decimal("NaN"), Decimal("0.230")),
            (Decimal("0.001"), Decimal("Infinity")),
        ],
    )
    def test_calculation_inputs_remain_positive_and_finite(
        self, total_distance_km: Decimal, rate_per_km: Decimal
    ) -> None:
        with pytest.raises(ValueError, match="positive finite"):
            calculate_mileage_amount(total_distance_km, rate_per_km)

    def test_rate_and_amount_must_fit_numeric_18_3(self) -> None:
        with pytest.raises(ValueError, match="NUMERIC\\(18,3\\) limit"):
            calculate_mileage_amount(Decimal("1.000"), Decimal("1000000000000000.000"))
        with pytest.raises(ValueError, match="NUMERIC\\(18,3\\)"):
            calculate_mileage_amount(Decimal("999999999999999.999"), Decimal("999999999999999.999"))

    def test_final_cents_cannot_carry_past_numeric_18_3_limit(self) -> None:
        with pytest.raises(ValueError, match="Mileage amount exceeds the NUMERIC\\(18,3\\) limit"):
            calculate_mileage_amount(Decimal("999999999999999.999"), Decimal("1.000"))

    def test_largest_persistable_cents_amount_is_accepted(self) -> None:
        assert calculate_mileage_amount(
            Decimal("999999999999999.990"), Decimal("1.000")
        ) == Decimal("999999999999999.99")

    def test_return_trip_cents_carry_past_numeric_18_3_limit_is_rejected(self) -> None:
        total = derive_total_distance(Decimal("499999999999999.999"), True)
        with pytest.raises(ValueError, match="Mileage amount exceeds the NUMERIC\\(18,3\\) limit"):
            calculate_mileage_amount(total, Decimal("1.000"))

    def test_insignificant_trailing_zeroes_are_representable_in_service_calculations(self) -> None:
        assert derive_total_distance(Decimal("1.2300"), False) == Decimal("1.2300")
        assert calculate_mileage_amount(Decimal("1.2300"), Decimal("1.0000")) == Decimal("1.23")

    @pytest.mark.parametrize(
        "amount", [Decimal("-0.001"), Decimal("NaN"), Decimal("Infinity")]
    )
    def test_final_amount_must_remain_nonnegative_and_finite(self, amount: Decimal) -> None:
        with pytest.raises(ValueError, match="non-negative finite"):
            validate_nonnegative_numeric_18_3(amount, "Mileage amount")

    def test_final_amount_must_fit_numeric_18_3(self) -> None:
        with pytest.raises(ValueError, match="NUMERIC\\(18,3\\) limit"):
            validate_nonnegative_numeric_18_3(
                Decimal("1000000000000000.000"), "Mileage amount"
            )


class TestMileageNumericContract:
    @pytest.mark.parametrize(
        ("model", "field"),
        [(MileageCalculationRequest, "one_way_distance_km"), (MileageRateWrite, "rate_per_km")],
    )
    def test_contract_rejects_unrepresentable_numeric_18_3_values(
        self, model: type[MileageCalculationRequest] | type[MileageRateWrite], field: str
    ) -> None:
        payload: dict[str, object] = {
            "trip_date": date(2026, 1, 1),
            "effective_from": date(2026, 1, 1),
            field: "0.0004",
        }
        with pytest.raises(ValueError):
            model.model_validate(payload)

    @pytest.mark.parametrize("value", ["0", "NaN", "1000000000000000.000"])
    def test_contract_rejects_non_positive_non_finite_and_overflow_values(self, value: str) -> None:
        with pytest.raises(ValueError):
            MileageRateWrite.model_validate({"effective_from": "2026-01-01", "rate_per_km": value})

    def test_contract_accepts_numeric_18_3_boundary(self) -> None:
        model = MileageCalculationRequest.model_validate(
            {"trip_date": "2026-01-01", "one_way_distance_km": "999999999999999.999"}
        )
        assert model.one_way_distance_km == Decimal("999999999999999.999")

    def test_contract_accepts_insignificant_trailing_zeroes(self) -> None:
        calculation = MileageCalculationRequest.model_validate(
            {"trip_date": "2026-01-01", "one_way_distance_km": "1.2300"}
        )
        rate = MileageRateWrite.model_validate(
            {"effective_from": "2026-01-01", "rate_per_km": "999999999999999.9900"}
        )
        assert calculation.one_way_distance_km == Decimal("1.2300")
        assert rate.rate_per_km == Decimal("999999999999999.9900")

    def test_typed_defaults_dump_uuid_values_as_json_strings(self) -> None:
        from jai.schemas.mileage import MileageDefaultsRead

        category_id = uuid.uuid4()
        transport_type_id = uuid.uuid4()
        defaults = MileageDefaultsRead(
            expense_category_id=category_id,
            default_transport_type_id=transport_type_id,
        )
        assert defaults.model_dump(mode="json") == {
            "expense_category_id": str(category_id),
            "default_transport_type_id": str(transport_type_id),
        }


class TestMileageLockedContract:
    def test_preview_has_bounded_offset_pagination_in_openapi(self) -> None:
        from jai.main import app

        parameters = app.openapi()["paths"]["/api/v1/mileage-expenses/rate-recalculation/preview"][
            "post"
        ]["parameters"]
        by_name = {parameter["name"]: parameter for parameter in parameters}
        assert {"type": "integer", "maximum": 500, "minimum": 1}.items() <= by_name["limit"][
            "schema"
        ].items()
        assert {"type": "integer", "minimum": 0}.items() <= by_name["offset"]["schema"].items()

    def test_rate_adjustment_contract_keeps_old_and_new_rule_scope_snapshots(self) -> None:
        fields = MileageRateAdjustmentRead.model_fields
        assert {
            "old_rate_transport_type_id",
            "new_rate_transport_type_id",
            "old_rate_transport_type_name",
            "new_rate_transport_type_name",
        } <= fields.keys()
