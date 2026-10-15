"""Step 1 - data layer: Tax Authority lookup, rules, catalogue, occupations, employees."""
from __future__ import annotations

import json

import pytest

from backend.data_loader import paths
from backend.data_loader.config_loader import get_config
from backend.data_loader.tax_loader import get_tax_db, get_value_of_use

CONFIG = get_config()
CARS = CONFIG.cars


@pytest.fixture(scope="module")
def tax_db():
    return get_tax_db()


# --------------------------------------------------------------------------- #
# Tax Authority database
# --------------------------------------------------------------------------- #
def test_tax_db_loaded_all_records(tax_db):
    assert tax_db.count() == 77_988


def test_catalogue_has_20_cars():
    assert [c.car_id for c in CARS] == [f"CAR-{i:02d}" for i in range(1, 21)]


@pytest.mark.parametrize("car", CARS, ids=lambda c: f"{c.car_id}-{c.model_name}")
def test_catalogue_car_found_in_tax_db(car):
    """1. Every catalogue car is found via the official 5-field key."""
    rec = get_value_of_use(car.manufacturer_code, car.model_code, car.registration_year)
    assert rec is not None, f"{car.car_id} not found in Tax Authority CSV"
    assert (rec.tax_year, rec.vehicle_type_code) == (2026, 1)
    assert rec.manufacturer_code == car.manufacturer_code
    assert rec.model_code == car.model_code
    assert rec.registration_year == car.registration_year


@pytest.mark.parametrize("car", CARS, ids=lambda c: f"{c.car_id}-{c.model_name}")
def test_value_of_use_matches_catalogue(car):
    """2. CSV value-of-use equals the catalogue's 'שווי שימוש חודשי' exactly."""
    rec = get_value_of_use(car.manufacturer_code, car.model_code, car.registration_year)
    assert rec.value_of_use == car.value_of_use_monthly


@pytest.mark.parametrize("car", CARS, ids=lambda c: f"{c.car_id}-{c.model_name}")
def test_vehicle_details_match_catalogue(car):
    """List price, description and drive type (via rules.driveField) agree too."""
    rec = get_value_of_use(car.manufacturer_code, car.model_code, car.registration_year)
    assert rec.list_price == car.list_price
    assert rec.model_description == car.model_description
    assert rec.drive_name == car.drive


def test_vehicle_type_filter_atto3():
    """BYD ATTO 3 key exists for vehicle types 1 and 2 - default must be type 1."""
    default = get_value_of_use(1014, 3, 2025)
    assert default.value_of_use == 3130
    assert default.commercial_name == "ATTO 3"
    assert get_value_of_use(1014, 3, 2025, vehicle_type_code=2).value_of_use == 6950


def test_missing_vehicle_returns_none():
    assert get_value_of_use(99999, 99999, 2025) is None


def test_drive_mapping_from_rules():
    assert CONFIG.rules.value_of_use.drive_names == {
        0: "בנזין", 1: "היברידי", 2: "פלאג-אין", 3: "חשמלי"}


# --------------------------------------------------------------------------- #
# Occupations / employees / reference calculations
# --------------------------------------------------------------------------- #
def test_14_occupations_loaded():
    occ = CONFIG.occupations
    assert len(occ) == 14
    assert len({o.name for o in occ}) == 14
    for o in occ:
        assert o.eligibility_group in CONFIG.rules.group_order
        if o.car_required:
            assert not o.waiver_allowed and o.waiver_maintenance == 0
        else:
            assert o.waiver_allowed and o.waiver_maintenance > 0
    assert sorted({o.waiver_maintenance for o in occ if o.waiver_allowed}) == [
        1200, 1500, 2000, 2600, 3000]
    assert CONFIG.occupation("מנהל/ת חטיבה").waiver_maintenance == 3000
    assert CONFIG.occupation("נהג/ת הפצה").car_required is True


def test_6_employees_loaded():
    emps = CONFIG.employees
    assert [e.employee_id for e in emps] == [f"E-{i:03d}" for i in range(1, 7)]
    for e in emps:
        assert e.name and e.question and e.base_salary > 0
        assert (e.current_car_id is None) != (e.current_maintenance is None)
    e1 = CONFIG.employee("E-001")
    assert (e1.occupation, e1.group, e1.base_salary, e1.credit_points) == (
        "מנהל/ת מוצר", "ג", 18500, 2.75)
    assert e1.current_car_id == "CAR-09"  # COROLLA CROSS
    e3 = CONFIG.employee("E-003")
    assert e3.current_car_id is None and e3.current_maintenance == 1500
    assert CONFIG.employee("E-002").children_under_23 == 5
    assert CONFIG.employee("E-006").bonus == 2000


def test_reference_calculations_loaded():
    refs = CONFIG.reference_calculations
    assert [r.employee_id for r in refs] == [f"E-{i:03d}" for i in range(1, 7)]
    for r in refs:
        assert r.net_alternative - r.net_current == r.net_diff
    r1 = CONFIG.reference_calculation("E-001")
    assert (r1.net_current, r1.net_alternative, r1.electric_refund_gross) == (11032, 11693, 440)
    assert CONFIG.reference_calculation("E-002").family_track_employee_share == 1150


# --------------------------------------------------------------------------- #
# rules.json
# --------------------------------------------------------------------------- #
def test_rules_loaded_completely():
    r = CONFIG.rules
    assert r.budgets == {"א": 3500, "ב": 3700, "ג": 4500, "ד": 5200}
    assert r.group_order == ["א", "ב", "ג", "ד"]
    assert r.choose_from == "own_group_or_lower"
    assert r.unused_budget == "not_paid"

    assert (r.electric.net_bonus, r.electric.method) == (250, "gross_up")

    ft = r.family_track
    assert (ft.min_children, ft.child_max_age, ft.gap_split_employee) == (4, 23, 0.5)
    assert ft.cars == ["CAR-19", "CAR-20"]
    assert [c.model_name for c in CONFIG.family_track_cars()] == ["CARNIVAL", "SANTA FE"]

    assert r.waiver.allowed_by == "occupation"
    assert r.waiver.maintenance_is_taxable is True
    assert r.waiver.maintenance_is_pensionable is False

    nm = r.net_method
    assert (nm.basis, nm.tax_year, nm.credit_point_value) == ("monthly", 2025, 242)
    assert [(b.up_to, b.rate) for b in nm.brackets] == [
        (7010, 0.10), (10060, 0.14), (16150, 0.20), (22440, 0.31),
        (46690, 0.35), (60130, 0.47), (None, 0.50)]
    assert (nm.national_insurance.threshold, nm.national_insurance.ceiling,
            nm.national_insurance.low_rate, nm.national_insurance.high_rate) == (
        7522, 50695, 0.0104, 0.07)
    assert (nm.health.low_rate, nm.health.high_rate) == (0.0323, 0.0517)
    assert (nm.pension_credit.rate, nm.pension_credit.max_contribution_rate,
            nm.pension_credit.income_ceiling) == (0.35, 0.07, 9500)
    assert nm.contributions_base == "gross"
    assert nm.participation.treatment == "net_deduction"
    assert nm.participation.reduces_value_of_use is False
    assert (nm.rounding.during, nm.rounding.display) == ("none", "shekel")

    assert r.value_of_use.key == ["שנת מס", "שנת רישום", "קוד סוג רכב", "קוד תוצר", "קוד דגם"]


def test_rules_match_source_json_exactly():
    """Every value in rules.json survives the round-trip through the model."""
    with open(paths.RULES_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    dumped = CONFIG.rules.model_dump(mode="json", by_alias=True)
    dumped["netMethod"]["brackets"] = [[b["upTo"], b["rate"]]
                                       for b in dumped["netMethod"]["brackets"]]
    assert dumped == raw


def test_unified_config_export(tmp_path):
    from backend.data_loader.config_loader import export_unified_config
    out = export_unified_config(CONFIG, tmp_path / "config.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"rules", "cars", "occupations", "employees",
                         "reference_calculations"}
    assert len(data["cars"]) == 20 and len(data["occupations"]) == 14
