"""Eligibility rules: which alternatives an employee may choose, and on what terms.

Every number (budgets, group order, family-track threshold and split, waiver
maintenance) comes from rules.json / the challenge workbook via AppConfig.
The result of a successful check is a `Scenario` - the monetary parameters the
payroll calculator needs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

from ..data_loader.config_loader import AppConfig, Car, Employee
from ..data_loader.tax_loader import get_value_of_use

# Drive label that entitles the employee to the electricity refund
# (catalogue 'הנעה' column / rules.valueOfUse.driveField).
ELECTRIC_DRIVE = "חשמלי"

REASON_OWN_GROUP = "הרכב מהקבוצה שלך או נמוכה ממנה"
REASON_WAIVER_OK = "העיסוק מאפשר ויתור"

ValueOfUseLookup = Callable[[Car], float]


@dataclass(frozen=True)
class Scenario:
    """Monetary parameters of one option (a car or a waiver)."""
    kind: Literal["car", "waiver"]
    label: str
    car_id: Optional[str] = None
    value_of_use: float = 0.0
    participation: float = 0.0
    maintenance_allowance: float = 0.0
    is_electric: bool = False
    family_gap: float = 0.0
    family_gap_deduction: float = 0.0


@dataclass(frozen=True)
class AlternativeRequest:
    kind: Literal["car", "waiver"]
    car_id: Optional[str] = None

    @classmethod
    def car(cls, car_id: str) -> "AlternativeRequest":
        return cls("car", car_id)

    @classmethod
    def waiver(cls) -> "AlternativeRequest":
        return cls("waiver")


@dataclass(frozen=True)
class EligibilityResult:
    is_eligible: bool
    reason: str
    scenario: Optional[Scenario] = None


def _require(value: str, supported: str, rule: str) -> None:
    if value != supported:
        raise NotImplementedError(f"rules.{rule}={value!r} is not supported (only {supported!r})")


def tax_value_of_use(car: Car) -> float:
    """Official value of use from the Tax Authority database."""
    rec = get_value_of_use(car.manufacturer_code, car.model_code, car.registration_year)
    if rec is None:
        raise LookupError(f"{car.car_id} ({car.model_name}) not found in Tax Authority data")
    return float(rec.value_of_use)


def car_label(car: Car) -> str:
    return f"{car.model_name} ({car.registration_year})"


def waiver_label(maintenance: float) -> str:
    return f"ויתור - אחזקת רכב {maintenance:.0f} ₪"


def family_gap(config: AppConfig, employee: Employee, car: Car) -> tuple[float, float]:
    """(gap above the group budget, employee's share of it) for family-track cars."""
    ft = config.rules.family_track
    if car.car_id not in ft.cars:
        return 0.0, 0.0
    _require(ft.price_source, "catalogue", "familyTrack.priceSource")
    gap = max(0.0, car.company_monthly_cost - config.rules.budget(employee.group))
    return gap, gap * ft.gap_split_employee


def car_scenario(
    config: AppConfig,
    employee: Employee,
    car: Car,
    participation: Optional[float] = None,
    value_of_use: ValueOfUseLookup = tax_value_of_use,
) -> Scenario:
    gap, deduction = family_gap(config, employee, car)
    return Scenario(
        kind="car",
        label=car_label(car),
        car_id=car.car_id,
        value_of_use=value_of_use(car),
        participation=(car.default_employee_participation
                       if participation is None else participation),
        is_electric=car.drive == ELECTRIC_DRIVE,
        family_gap=gap,
        family_gap_deduction=deduction,
    )


def waiver_scenario(maintenance: float) -> Scenario:
    return Scenario(kind="waiver", label=waiver_label(maintenance),
                    maintenance_allowance=maintenance)


def current_scenario(
    config: AppConfig, employee: Employee, value_of_use: ValueOfUseLookup = tax_value_of_use
) -> Scenario:
    """The employee's situation today (their car + current participation, or waiver)."""
    if employee.current_car_id:
        return car_scenario(config, employee, config.car(employee.current_car_id),
                            employee.current_participation, value_of_use)
    return waiver_scenario(employee.current_maintenance or 0.0)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_car_choice(
    config: AppConfig, employee: Employee, car: Car,
    value_of_use: ValueOfUseLookup = tax_value_of_use,
) -> EligibilityResult:
    """Regular catalogue car: own group or a lower one (rules.groupOrder)."""
    rules = config.rules
    _require(rules.choose_from, "own_group_or_lower", "chooseFrom")
    _require(rules.unused_budget, "not_paid", "unusedBudget")  # nothing added to pay
    if car.group not in rules.group_order:
        return EligibilityResult(False, f"הרכב {car.model_name} אינו בקבוצות התקציב הרגילות")
    if rules.group_rank(car.group) > rules.group_rank(employee.group):
        return EligibilityResult(
            False,
            f"הרכב {car.model_name} מקבוצה {car.group} - מעל הקבוצה שלך ({employee.group}); "
            "מותר לבחור רכב מהקבוצה שלך או נמוכה ממנה",
        )
    return EligibilityResult(True, REASON_OWN_GROUP,
                             car_scenario(config, employee, car, value_of_use=value_of_use))


def check_family_track(
    config: AppConfig, employee: Employee, car: Car,
    value_of_use: ValueOfUseLookup = tax_value_of_use,
) -> EligibilityResult:
    ft = config.rules.family_track
    n = employee.children_under_23
    if n < ft.min_children:
        return EligibilityResult(
            False,
            f"מסלול ברוכת ילדים מיועד להורים ל-{ft.min_children} ילדים ומעלה עד גיל "
            f"{ft.child_max_age} (יש לך {n})",
        )
    pct = f"{ft.gap_split_employee:.0%}"
    return EligibilityResult(
        True,
        f"מסלול ברוכת ילדים ({n} ילדים); על הפער מעל התקציב - {pct} עובד",
        car_scenario(config, employee, car, value_of_use=value_of_use),
    )


def check_waiver(config: AppConfig, employee: Employee) -> EligibilityResult:
    _require(config.rules.waiver.allowed_by, "occupation", "waiver.allowedBy")
    occ = config.occupation(employee.occupation)
    if occ.car_required:
        return EligibilityResult(
            False, f"בעיסוק '{occ.name}' הרכב הוא חובה (תפקיד שטח) - אין אפשרות לוותר")
    if not occ.waiver_allowed:
        return EligibilityResult(False, f"העיסוק '{occ.name}' אינו מאפשר ויתור על רכב")
    return EligibilityResult(True, REASON_WAIVER_OK, waiver_scenario(occ.waiver_maintenance))


def check_eligibility(
    config: AppConfig, employee: Employee, request: AlternativeRequest,
    value_of_use: ValueOfUseLookup = tax_value_of_use,
) -> EligibilityResult:
    if request.kind == "waiver":
        return check_waiver(config, employee)
    try:
        car = config.car(request.car_id)
    except KeyError:
        return EligibilityResult(False, f"הרכב {request.car_id} אינו בקטלוג")
    if car.car_id in config.rules.family_track.cars:
        return check_family_track(config, employee, car, value_of_use)
    return check_car_choice(config, employee, car, value_of_use)
