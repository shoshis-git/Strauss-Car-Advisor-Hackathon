"""Compare an employee's current situation with a requested alternative."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ..data_loader.config_loader import AppConfig, Employee, Rules
from .calculator import ScenarioResult, calculate_scenario
from .eligibility import (AlternativeRequest, Scenario, ValueOfUseLookup, check_eligibility,
                          current_scenario, tax_value_of_use)


def display_round(value: float, rules: Rules) -> int:
    """Round for display per rules.netMethod.rounding.display (half-up, not banker's)."""
    if rules.net_method.rounding.display != "shekel":
        raise NotImplementedError(f"rounding.display={rules.net_method.rounding.display!r}")
    return int(Decimal(repr(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Comparison:
    employee_id: str
    name: str
    current_label: str
    alternative_label: str
    is_eligible: bool
    reason: str
    # Display values (whole shekels); None when the alternative is not eligible.
    net_today: int
    tax_today: int
    btl_health_today: int
    net_alt: Optional[int] = None
    net_diff: Optional[int] = None
    tax_alt: Optional[int] = None
    btl_health_alt: Optional[int] = None
    electric_gross: Optional[int] = None
    family_gap_deduction: Optional[int] = None
    explanation: str = ""
    # Full-precision detail
    today: Optional[ScenarioResult] = None
    alternative: Optional[ScenarioResult] = None


def _ils(x: float) -> str:
    return f"{x:,.0f}"


def explain(before: Scenario, after: Scenario, rules: Rules) -> str:
    """Short Hebrew explanation of what changed, like the 'למה' column."""
    parts: list[str] = []
    if before.kind == "car" and after.kind == "car":
        if before.value_of_use != after.value_of_use:
            parts.append(f"שווי שימוש {_ils(before.value_of_use)} → {_ils(after.value_of_use)} ₪")
    elif before.kind == "waiver" and after.kind == "car":
        parts.append(f"נוסף שווי שימוש {_ils(after.value_of_use)} ₪ "
                     f"ובוטלה אחזקה {_ils(before.maintenance_allowance)} ₪")
    elif before.kind == "car" and after.kind == "waiver":
        taxable = " (חייבת במס)" if rules.waiver.maintenance_is_taxable else ""
        parts.append(f"בוטל שווי שימוש {_ils(before.value_of_use)} ₪, "
                     f"נוספה אחזקה {_ils(after.maintenance_allowance)} ₪{taxable}")
    elif before.maintenance_allowance != after.maintenance_allowance:
        parts.append(f"אחזקה {_ils(before.maintenance_allowance)} → "
                     f"{_ils(after.maintenance_allowance)} ₪")

    el = rules.electric
    el_text = (f"{_ils(el.net_bonus)} ₪ נטו החזר חשמל (מגולם)" if el.method == "gross_up"
               else f"{_ils(el.net_bonus)} ₪ ברוטו החזר חשמל")
    if after.is_electric and not before.is_electric:
        parts.append(f"+{el_text}")
    elif before.is_electric and not after.is_electric:
        parts.append(f"-{el_text}")

    if before.participation != after.participation:
        parts.append(f"השתתפות {_ils(before.participation)} → {_ils(after.participation)} ₪")
    if before.family_gap_deduction != after.family_gap_deduction:
        parts.append(f"חלק העובד בפער המסלול המשפחתי {_ils(after.family_gap_deduction)} ₪")
    return " · ".join(parts)


def compare(
    config: AppConfig,
    employee: Employee | str,
    request: AlternativeRequest,
    value_of_use: ValueOfUseLookup = tax_value_of_use,
    target_participation: Optional[float] = None,
) -> Comparison:
    """target_participation overrides the catalogue default (e.g. approved by a rep)."""
    if isinstance(employee, str):
        employee = config.employee(employee)
    rules = config.rules
    r = lambda v: display_round(v, rules)  # noqa: E731

    today_sc = current_scenario(config, employee, value_of_use)
    today = calculate_scenario(employee, today_sc, rules)
    elig = check_eligibility(config, employee, request, value_of_use)

    common = dict(
        employee_id=employee.employee_id,
        name=employee.name,
        current_label=today_sc.label,
        is_eligible=elig.is_eligible,
        reason=elig.reason,
        net_today=r(today.payslip.net),
        tax_today=r(today.payslip.income_tax),
        btl_health_today=r(today.payslip.btl_and_health),
        today=today,
    )
    if not elig.is_eligible:
        label = request.car_id if request.kind == "car" else "ויתור"
        return Comparison(alternative_label=label or "", **common)

    alt_sc = elig.scenario
    if target_participation is not None and alt_sc.kind == "car":
        alt_sc = replace(alt_sc, participation=target_participation)
    alt = calculate_scenario(employee, alt_sc, rules)
    net_alt = r(alt.payslip.net)
    return Comparison(
        alternative_label=alt_sc.label,
        net_alt=net_alt,
        net_diff=net_alt - common["net_today"],
        tax_alt=r(alt.payslip.income_tax),
        btl_health_alt=r(alt.payslip.btl_and_health),
        electric_gross=r(alt.electric_gross),
        family_gap_deduction=r(alt_sc.family_gap_deduction),
        explanation=explain(today_sc, alt_sc, rules),
        alternative=alt,
        **common,
    )
