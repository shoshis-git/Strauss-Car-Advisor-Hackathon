"""Deterministic monthly net-pay calculator (rules.netMethod).

No rounding anywhere in here (rules.netMethod.rounding.during = "none");
rounding to whole shekels happens only for display, in comparator.py.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..data_loader.config_loader import ContributionTable, Employee, NetMethod, Rules
from .eligibility import Scenario

GROSS_UP_TOLERANCE = 1e-7  # binary-search precision for the electricity gross-up (₪)


@dataclass(frozen=True)
class PayslipInput:
    base_salary: float
    commissions: float
    bonus: float
    pension_pct: float
    study_fund_pct: float
    credit_points: float
    value_of_use: float = 0.0
    participation: float = 0.0
    maintenance_allowance: float = 0.0
    electric_gross: float = 0.0
    family_gap_deduction: float = 0.0


@dataclass(frozen=True)
class Payslip:
    cash_base: float
    pension_base: float
    pension_emp: float
    study_fund_emp: float
    taxable_income: float
    gross_tax: float
    credit_points_value: float
    pension_tax_credit: float
    income_tax: float
    national_insurance: float
    health: float
    real_cash_gross: float
    net: float

    @property
    def btl_and_health(self) -> float:
        return self.national_insurance + self.health


def bracket_tax(income: float, net_method: NetMethod) -> float:
    """Progressive tax by netMethod.brackets (monthly limits)."""
    tax, lower = 0.0, 0.0
    for b in net_method.brackets:
        upper = income if b.up_to is None else min(income, b.up_to)
        if upper > lower:
            tax += (upper - lower) * b.rate
        if b.up_to is None or income <= b.up_to:
            break
        lower = b.up_to
    return tax


def two_tier_contribution(income: float, table: ContributionTable) -> float:
    capped = min(income, table.ceiling)
    low = min(capped, table.threshold) * table.low_rate
    high = max(0.0, capped - table.threshold) * table.high_rate
    return low + high


def compute_payslip(inp: PayslipInput, rules: Rules) -> Payslip:
    nm = rules.net_method
    if nm.contributions_base != "gross":
        raise NotImplementedError(f"contributionsBase={nm.contributions_base!r}")
    if nm.participation.treatment != "net_deduction":
        raise NotImplementedError(f"participation.treatment={nm.participation.treatment!r}")

    cash_base = inp.base_salary + inp.commissions + inp.bonus
    pension_base = cash_base + (
        inp.maintenance_allowance if rules.waiver.maintenance_is_pensionable else 0.0)
    pension_emp = pension_base * inp.pension_pct / 100.0
    study_fund_emp = pension_base * inp.study_fund_pct / 100.0

    value_of_use = inp.value_of_use - (
        inp.participation if nm.participation.reduces_value_of_use else 0.0)
    taxable_income = (
        cash_base
        + (inp.maintenance_allowance if rules.waiver.maintenance_is_taxable else 0.0)
        + inp.electric_gross
        + value_of_use
    )

    gross_tax = bracket_tax(taxable_income, nm)
    credit_points_value = inp.credit_points * nm.credit_point_value
    pc = nm.pension_credit
    recognised_pension = min(pension_emp, pc.income_ceiling * pc.max_contribution_rate)
    pension_tax_credit = recognised_pension * pc.rate
    income_tax = max(0.0, gross_tax - credit_points_value - pension_tax_credit)

    national_insurance = two_tier_contribution(taxable_income, nm.national_insurance)
    health = two_tier_contribution(taxable_income, nm.health)

    real_cash_gross = cash_base + inp.maintenance_allowance + inp.electric_gross
    net = (real_cash_gross - income_tax - national_insurance - health
           - pension_emp - study_fund_emp - inp.participation - inp.family_gap_deduction)

    return Payslip(
        cash_base=cash_base, pension_base=pension_base, pension_emp=pension_emp,
        study_fund_emp=study_fund_emp, taxable_income=taxable_income, gross_tax=gross_tax,
        credit_points_value=credit_points_value, pension_tax_credit=pension_tax_credit,
        income_tax=income_tax, national_insurance=national_insurance, health=health,
        real_cash_gross=real_cash_gross, net=net,
    )


def electric_gross_amount(inp: PayslipInput, rules: Rules) -> float:
    """Gross electricity refund so that the employee nets exactly electric.netBonus."""
    el = rules.electric
    if el.method == "gross":
        return float(el.net_bonus)
    if el.method != "gross_up":
        raise NotImplementedError(f"electric.method={el.method!r}")

    base_inp = replace(inp, electric_gross=0.0)
    base_net = compute_payslip(base_inp, rules).net

    def net_gain(gross: float) -> float:
        return compute_payslip(replace(base_inp, electric_gross=gross), rules).net - base_net

    lo, hi = 0.0, float(el.net_bonus)
    while net_gain(hi) < el.net_bonus:
        lo, hi = hi, hi * 2
    while hi - lo > GROSS_UP_TOLERANCE:
        mid = (lo + hi) / 2
        if net_gain(mid) < el.net_bonus:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    payslip_input: PayslipInput
    payslip: Payslip

    @property
    def electric_gross(self) -> float:
        return self.payslip_input.electric_gross


def employee_input(employee: Employee, scenario: Scenario) -> PayslipInput:
    return PayslipInput(
        base_salary=employee.base_salary,
        commissions=employee.commissions,
        bonus=employee.bonus,
        pension_pct=employee.pension_employee_pct,
        study_fund_pct=employee.study_fund_employee_pct,
        credit_points=employee.credit_points,
        value_of_use=scenario.value_of_use,
        participation=scenario.participation,
        maintenance_allowance=scenario.maintenance_allowance,
        family_gap_deduction=scenario.family_gap_deduction,
    )


def calculate_scenario(employee: Employee, scenario: Scenario, rules: Rules) -> ScenarioResult:
    inp = employee_input(employee, scenario)
    if scenario.is_electric:
        inp = replace(inp, electric_gross=electric_gross_amount(inp, rules))
    return ScenarioResult(scenario, inp, compute_payslip(inp, rules))
