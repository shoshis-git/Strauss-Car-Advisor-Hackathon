"""Deterministic payroll engine: eligibility, net-pay calculation and comparison."""
from .calculator import Payslip, PayslipInput, calculate_scenario, compute_payslip
from .comparator import Comparison, compare
from .eligibility import AlternativeRequest, EligibilityResult, Scenario, check_eligibility

__all__ = [
    "Payslip", "PayslipInput", "calculate_scenario", "compute_payslip",
    "Comparison", "compare",
    "AlternativeRequest", "EligibilityResult", "Scenario", "check_eligibility",
]
