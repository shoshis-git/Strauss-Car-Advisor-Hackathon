"""Step 2 - engine: eligibility rules + net comparison vs. the 'חישובים' sheet."""
from __future__ import annotations

import re

import pytest

from backend.data_loader.config_loader import AppConfig, ReferenceCalculation, get_config
from backend.engine.calculator import calculate_scenario, compute_payslip, PayslipInput
from backend.engine.comparator import compare, display_round
from backend.engine.eligibility import (AlternativeRequest, REASON_OWN_GROUP,
                                        check_eligibility, current_scenario)

CONFIG = get_config()
REFS = CONFIG.reference_calculations

# (engine attribute, reference attribute, Excel column)
COMPARED_FIELDS = [
    ("net_today", "net_current", "נטו היום"),
    ("net_alt", "net_alternative", "נטו בחלופה"),
    ("net_diff", "net_diff", "הפרש"),
    ("tax_today", "tax_current", "מס היום"),
    ("tax_alt", "tax_alternative", "מס בחלופה"),
    ("btl_health_today", "ni_health_current", "ביטוח לאומי+בריאות היום"),
    ("btl_health_alt", "ni_health_alternative", "ביטוח לאומי+בריאות בחלופה"),
    ("electric_gross", "electric_refund_gross", "החזר חשמל ברוטו"),
    ("family_gap_deduction", "family_track_employee_share", "חלק העובד במסלול המשפחתי"),
]


def request_from_label(config: AppConfig, label: str) -> AlternativeRequest:
    """'ATTO 3 (2025)' -> car request; 'ויתור - ...' -> waiver request."""
    if label.startswith("ויתור"):
        return AlternativeRequest.waiver()
    model = re.sub(r"\s*\(\d{4}\)\s*$", "", label)
    car = config.car_by_model_name(model)
    assert car is not None, f"alternative {label!r} not in catalogue"
    return AlternativeRequest.car(car.car_id)


def run_reference(ref: ReferenceCalculation):
    return compare(CONFIG, ref.employee_id, request_from_label(CONFIG, ref.alternative_label))


# --------------------------------------------------------------------------- #
# Validation against the Excel reference sheet
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ref", REFS, ids=lambda r: r.employee_id)
def test_matches_reference_sheet(ref):
    result = run_reference(ref)
    assert result.is_eligible == ref.eligible
    assert result.reason == ref.reason
    assert result.current_label == ref.current_label
    assert result.alternative_label == ref.alternative_label
    mismatches = {
        col: (getattr(result, eng), getattr(ref, xl))
        for eng, xl, col in COMPARED_FIELDS
        if getattr(result, eng) != getattr(ref, xl)
    }
    assert not mismatches, f"{ref.employee_id} engine != excel: {mismatches}"


@pytest.mark.parametrize("ref", REFS, ids=lambda r: r.employee_id)
def test_explanation_matches_reference(ref):
    assert run_reference(ref).explanation == ref.explanation


@pytest.mark.parametrize("ref", REFS, ids=lambda r: r.employee_id)
def test_electric_gross_up_is_exactly_net_bonus(ref):
    """Unrounded: alt net with refund - alt net without refund == netBonus."""
    result = run_reference(ref)
    alt = result.alternative
    if not alt.scenario.is_electric:
        assert alt.electric_gross == 0
        return
    from dataclasses import replace
    without = compute_payslip(replace(alt.payslip_input, electric_gross=0.0), CONFIG.rules)
    assert alt.payslip.net - without.net == pytest.approx(
        CONFIG.rules.electric.net_bonus, abs=1e-6)


def test_electric_gross_method_uses_flat_amount():
    rules = CONFIG.rules.model_copy(
        update={"electric": CONFIG.rules.electric.model_copy(update={"method": "gross"})})
    emp = CONFIG.employee("E-001")
    elig = check_eligibility(CONFIG, emp, AlternativeRequest.car("CAR-10"))
    res = calculate_scenario(emp, elig.scenario, rules)
    assert res.electric_gross == rules.electric.net_bonus


def test_tax_and_contribution_building_blocks():
    """Hand-checked payslip: 10,000 salary, no car, 2.25 points, 6% pension."""
    slip = compute_payslip(PayslipInput(10000, 0, 0, 6, 0, 2.25), CONFIG.rules)
    assert slip.gross_tax == pytest.approx(7010 * 0.10 + 2990 * 0.14)
    assert slip.pension_tax_credit == pytest.approx(600 * 0.35)
    assert slip.national_insurance == pytest.approx(7522 * 0.0104 + 2478 * 0.07)
    assert slip.health == pytest.approx(7522 * 0.0323 + 2478 * 0.0517)
    capped = compute_payslip(PayslipInput(30000, 0, 0, 6, 0, 0), CONFIG.rules)
    assert capped.pension_tax_credit == pytest.approx(9500 * 0.07 * 0.35)  # 232.75


def test_display_rounding_half_up():
    assert display_round(10.5, CONFIG.rules) == 11
    assert display_round(11.5, CONFIG.rules) == 12
    assert display_round(-0.4, CONFIG.rules) == 0


# --------------------------------------------------------------------------- #
# Eligibility - positive
# --------------------------------------------------------------------------- #
def test_own_or_lower_group_allowed():
    emp = CONFIG.employee("E-001")  # group ג
    for car in CONFIG.cars:
        if car.group in ("א", "ב", "ג"):
            res = check_eligibility(CONFIG, emp, AlternativeRequest.car(car.car_id))
            assert res.is_eligible and res.reason == REASON_OWN_GROUP, car.car_id


def test_family_track_gap_from_catalogue_cost():
    emp = CONFIG.employee("E-002")  # group ב (3,700), 5 children
    carnival = check_eligibility(CONFIG, emp, AlternativeRequest.car("CAR-19")).scenario
    santa_fe = check_eligibility(CONFIG, emp, AlternativeRequest.car("CAR-20")).scenario
    assert (carnival.family_gap, carnival.family_gap_deduction) == (2300, 1150)
    assert (santa_fe.family_gap, santa_fe.family_gap_deduction) == (1900, 950)


def test_current_scenario_of_waiver_employee():
    sc = current_scenario(CONFIG, CONFIG.employee("E-003"))
    assert sc.kind == "waiver" and sc.maintenance_allowance == 1500 and sc.value_of_use == 0


# --------------------------------------------------------------------------- #
# Eligibility - negative
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("car_id", ["CAR-09", "CAR-14"])  # group ג, group ד
def test_negative_group_b_cannot_choose_higher_group(car_id):
    emp = CONFIG.employee("E-003")  # group ב
    res = check_eligibility(CONFIG, emp, AlternativeRequest.car(car_id))
    assert not res.is_eligible and res.scenario is None
    assert "מעל הקבוצה שלך" in res.reason
    cmp = compare(CONFIG, emp, AlternativeRequest.car(car_id))
    assert not cmp.is_eligible and cmp.net_alt is None


def test_negative_field_role_cannot_waive():
    emp = CONFIG.employee("E-002")  # נציג/ת מכירות שטח - רכב חובה
    res = check_eligibility(CONFIG, emp, AlternativeRequest.waiver())
    assert not res.is_eligible and res.scenario is None
    assert "חובה" in res.reason


def test_negative_family_track_requires_4_children():
    emp = CONFIG.employee("E-004")  # 3 children
    for car_id in CONFIG.rules.family_track.cars:
        res = check_eligibility(CONFIG, emp, AlternativeRequest.car(car_id))
        assert not res.is_eligible and res.scenario is None
        assert "יש לך 3" in res.reason
