"""Tools the advisor agent can call. Each tool reads/updates the ConversationState
and returns a JSON-serialisable dict; all numbers come from the deterministic engine."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from ..data_loader.config_loader import AppConfig, Car, Employee
from ..engine.comparator import compare
from ..engine.eligibility import AlternativeRequest, check_eligibility, tax_value_of_use
from .state import FIELD_LABELS, ConversationState, compute_missing

WAIVER = "waiver"


# --------------------------------------------------------------------------- #
# Resolvers (free text -> catalogue / occupation entries)
# --------------------------------------------------------------------------- #
def occupation_variants(name: str) -> list[str]:
    """'מנהל/ת מוצר' -> ['מנהל/ת מוצר', 'מנהל מוצר', 'מנהלת מוצר']."""
    words = name.split()
    masc = [w.split("/")[0] for w in words]
    fem = [w.split("/")[0] + w.split("/")[1] if "/" in w else w for w in words]
    return list(dict.fromkeys([name, " ".join(masc), " ".join(fem)]))


def resolve_occupation(config: AppConfig, text: str) -> Optional[str]:
    """Longest occupation (any gender form) mentioned in text."""
    best: Optional[tuple[int, str]] = None
    for occ in config.occupations:
        for v in occupation_variants(occ.name):
            if v in text and (best is None or len(v) > best[0]):
                best = (len(v), occ.name)
    return best[1] if best else None


def resolve_car(config: AppConfig, text: str) -> Optional[Car]:
    """Strict: car id ('CAR-10'), exact model name or exact alias. No partial matches."""
    t = " ".join(text.split())
    m = re.fullmatch(r"(?i)car-?(\d{1,2})", t)
    if m:
        try:
            return config.car(f"CAR-{int(m.group(1)):02d}")
        except KeyError:
            return None
    for car in config.cars:
        if any(n.casefold() == t.casefold() for n in config.car_names(car)):
            return car
    return None


def car_not_found_error(config: AppConfig, field: str, raw: str) -> str:
    """Rejection message; names catalogue cars mentioned inside the text as suggestions."""
    msg = f"{field}: הרכב {raw!r} לא נמצא בקטלוג"
    hints = list(dict.fromkeys(f"{c.model_name} ({c.car_id})"
                               for _, c in find_cars_in_text(config, raw)))
    if hints:
        msg += f" - האם הכוונה ל-{' / '.join(hints)}? יש לאשר מול העובד"
    return msg


# Hebrew one-letter prefixes glued to a word: מה-קורולה, לטסלה, בטוסון, והאטו ...
_HEB_PREFIX = r"(?:[ומלבהשכ]{1,3}-?)?"


def find_cars_in_text(config: AppConfig, text: str) -> list[tuple[int, Car]]:
    """All catalogue cars mentioned (by model name or alias), as (position, car).

    Longest names win, so 'קורולה קרוס' is not read as 'קורולה'."""
    low = text.casefold()
    names = sorted(((n.casefold(), car) for car in config.cars for n in config.car_names(car)),
                   key=lambda nc: -len(nc[0]))
    hits: list[tuple[int, int, Car]] = []
    for name, car in names:
        pattern = r"(?<![\w'׳])" + _HEB_PREFIX + "(" + re.escape(name) + r")(?![\w'׳])"
        for m in re.finditer(pattern, low):
            s, e = m.start(1), m.end(1)
            if not any(hs < e and s < he for hs, he, _ in hits):
                hits.append((s, e, car))
    return [(s, c) for s, _, c in sorted(hits, key=lambda h: h[0])]


# --------------------------------------------------------------------------- #
# Engine bridge
# --------------------------------------------------------------------------- #
def build_employee(state: ConversationState, config: AppConfig) -> Employee:
    p, cur = state.profile, state.current
    maintenance = cur.current_maintenance
    if cur.status == WAIVER and maintenance is None and p.occupation:
        maintenance = config.occupation(p.occupation).waiver_maintenance
    return Employee(
        employee_id=state.conversation_id,
        name="",
        national_id="",
        occupation=p.occupation or "",
        group=p.eligibility_group or "",
        base_salary=p.base_salary or 0.0,
        commissions=p.commissions_bonuses or 0.0,
        bonus=0.0,
        pension_employee_pct=p.pension_pct or 0.0,
        study_fund_employee_pct=p.hishtalmut_pct or 0.0,
        credit_points=p.credit_points or 0.0,
        children_under_23=p.children_count or 0,
        current_car_raw=cur.current_car_code or WAIVER,
        current_participation=cur.current_participation or 0.0,
        question="",
        current_car_id=cur.current_car_code if cur.status == "car" else None,
        current_maintenance=maintenance if cur.status == WAIVER else None,
    )


def _car_summary(car: Car) -> dict[str, Any]:
    return {
        "car_id": car.car_id,
        "model_name": car.model_name,
        "manufacturer": car.manufacturer_name,
        "group": car.group,
        "drive": car.drive,
        "registration_year": car.registration_year,
        "value_of_use": tax_value_of_use(car),
        "default_participation": car.default_employee_participation,
    }


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
NUMERIC_FIELDS = {
    "base_salary": ("profile", float), "commissions_bonuses": ("profile", float),
    "pension_pct": ("profile", float), "hishtalmut_pct": ("profile", float),
    "credit_points": ("profile", float), "children_count": ("profile", int),
    "current_participation": ("current", float), "current_maintenance": ("current", float),
    "target_participation": ("target", float),
}


def update_profile(state: ConversationState, config: AppConfig, **fields: Any) -> dict:
    """Apply facts the employee stated. Unknown/invalid values are rejected, never guessed."""
    updated: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    p, cur, tgt = state.profile, state.current, state.target

    for name, (section, typ) in NUMERIC_FIELDS.items():
        if fields.get(name) is None:
            continue
        try:
            value = typ(fields[name])
        except (TypeError, ValueError):
            errors.append(f"{name}: ערך לא מספרי {fields[name]!r}")
            continue
        if value < 0 or (name.endswith("_pct") and value > 100):
            errors.append(f"{name}: ערך לא תקין {value}")
            continue
        setattr(getattr(state, section), name, value)
        updated[name] = value

    if fields.get("occupation"):
        occ = resolve_occupation(config, str(fields["occupation"]))
        if occ is None:
            errors.append(f"occupation: העיסוק {fields['occupation']!r} לא נמצא בטבלת העיסוקים")
        else:
            p.occupation = updated["occupation"] = occ
            occ_group = config.occupation(occ).eligibility_group
            if p.eligibility_group is None and not fields.get("eligibility_group"):
                p.eligibility_group = updated["eligibility_group"] = occ_group
            elif (fields.get("eligibility_group") or p.eligibility_group) != occ_group:
                warnings.append(f"לפי טבלת העיסוקים, קבוצת הזכאות של '{occ}' היא {occ_group}")

    if fields.get("eligibility_group"):
        g = str(fields["eligibility_group"]).strip().rstrip("'׳")
        if g not in config.rules.group_order:
            errors.append(f"eligibility_group: קבוצה לא קיימת {g!r} "
                          f"(קיימות: {', '.join(config.rules.group_order)})")
        else:
            p.eligibility_group = updated["eligibility_group"] = g

    if fields.get("current_car"):
        raw = str(fields["current_car"])
        if raw == WAIVER:
            cur.status, cur.current_car_code = WAIVER, None
            cur.current_participation = 0.0
            updated["current_car"] = WAIVER
        else:
            car = resolve_car(config, raw)
            if car is None:
                errors.append(car_not_found_error(config, "current_car", raw))
            else:
                cur.status, cur.current_car_code = "car", car.car_id
                updated["current_car"] = car.car_id

    if fields.get("target_action"):
        action = fields["target_action"]
        if action not in ("replace", WAIVER):
            errors.append(f"target_action: ערך לא תקין {action!r}")
        else:
            tgt.target_action = updated["target_action"] = action
            if action == WAIVER:
                tgt.target_car_code = None

    if fields.get("target_car"):
        car = resolve_car(config, str(fields["target_car"]))
        if car is None:
            errors.append(car_not_found_error(config, "target_car", str(fields["target_car"])))
        else:
            tgt.target_action, tgt.target_car_code = "replace", car.car_id
            updated["target_car"] = car.car_id

    if updated:
        state.last_comparison = None  # inputs changed -> previous result is stale
    state.missing_info = compute_missing(state, config)
    return {
        "ok": not errors,
        "updated": updated,
        "errors": errors,
        "warnings": warnings,
        "missing_info": state.missing_info,
        "missing_labels": [FIELD_LABELS[f] for f in state.missing_info],
    }


def get_allowed_cars(state: ConversationState, config: AppConfig) -> dict:
    """Cars the employee may choose (own group or lower), family track and waiver."""
    p = state.profile
    if p.eligibility_group is None:
        return {"ok": False, "error": "חסרה קבוצת זכאות או עיסוק", "missing": ["eligibility_group"]}
    emp = build_employee(state, config)
    allowed, family = [], []
    family_reason = None
    for car in config.cars:
        res = check_eligibility(config, emp, AlternativeRequest.car(car.car_id))
        if car.car_id in config.rules.family_track.cars:
            family_reason = res.reason
            if res.is_eligible:
                family.append(_car_summary(car))
        elif res.is_eligible:
            allowed.append(_car_summary(car))

    if p.occupation:
        w = check_eligibility(config, emp, AlternativeRequest.waiver())
        waiver = {"allowed": w.is_eligible, "reason": w.reason,
                  "maintenance": w.scenario.maintenance_allowance if w.is_eligible else 0}
    else:
        waiver = {"allowed": None, "reason": "כדי לבדוק ויתור צריך לדעת את העיסוק"}

    if p.children_count is None:
        family_track = {"eligible": None, "cars": [],
                        "reason": "כדי לבדוק מסלול ברוכת ילדים צריך את מספר הילדים עד גיל 23"}
    else:
        family_track = {"eligible": bool(family), "cars": family, "reason": family_reason}
    return {"ok": True, "group": p.eligibility_group, "budget": config.rules.budget(p.eligibility_group),
            "allowed_cars": allowed, "family_track": family_track, "waiver": waiver}


def calculate_comparison(state: ConversationState, config: AppConfig) -> dict:
    """Run the deterministic comparator - only when every required field is known."""
    state.missing_info = compute_missing(state, config)
    if state.missing_info:
        return {"ok": False, "missing_info": state.missing_info,
                "missing_labels": [FIELD_LABELS[f] for f in state.missing_info]}
    tgt = state.target
    request = (AlternativeRequest.waiver() if tgt.target_action == WAIVER
               else AlternativeRequest.car(tgt.target_car_code))
    c = compare(config, build_employee(state, config), request,
                target_participation=tgt.target_participation)
    result = {
        "ok": True,
        "is_eligible": c.is_eligible,
        "reason": c.reason,
        "current_label": c.current_label,
        "alternative_label": c.alternative_label,
        "net_today": c.net_today,
        "net_alt": c.net_alt,
        "net_diff": c.net_diff,
        "tax_today": c.tax_today,
        "tax_alt": c.tax_alt,
        "btl_health_today": c.btl_health_today,
        "btl_health_alt": c.btl_health_alt,
        "electric_gross": c.electric_gross,
        "family_gap_deduction": c.family_gap_deduction,
        "explanation": c.explanation,
    }
    state.last_comparison = result
    return result


def ask_representative(state: ConversationState, config: AppConfig,
                       question: str, field: Optional[str] = None) -> dict:
    """Freeze the conversation until a representative answers `question`."""
    state.waiting_for_rep = True
    state.rep_question = question
    state.rep_field = field
    return {"ok": True, "status": "waiting_for_rep", "question": question, "field": field}


# --------------------------------------------------------------------------- #
# Registry + JSON schemas (provider-neutral; llm.to_openai_tools converts them)
# --------------------------------------------------------------------------- #
TOOLS: dict[str, Callable[..., dict]] = {
    "update_profile": update_profile,
    "get_allowed_cars": get_allowed_cars,
    "calculate_comparison": calculate_comparison,
    "ask_representative": ask_representative,
}

_num = {"type": "number"}
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "update_profile",
        "description": (
            "שמירת נתונים שהעובד מסר במפורש בשיחה. יש להעביר רק שדות שהעובד אמר - "
            "לעולם לא לנחש. רכב אפשר לציין בקוד (CAR-10), בשם הדגם מהקטלוג או בכינוי "
            "עברי נפוץ (למשל 'אטו 3'); רכב שלא בקטלוג יידחה. "
            "current_car='waiver' אם אין לעובד רכב והוא מקבל אחזקה."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "eligibility_group": {"type": "string", "description": "קבוצת זכאות: א/ב/ג/ד"},
                "occupation": {"type": "string", "description": "העיסוק כפי שנאמר"},
                "base_salary": _num,
                "commissions_bonuses": {**_num, "description": "סך עמלות ובונוסים חודשי"},
                "pension_pct": {**_num, "description": "אחוז הפרשת עובד לפנסיה, למשל 6"},
                "hishtalmut_pct": {**_num, "description": "אחוז הפרשת עובד לקרן השתלמות"},
                "credit_points": _num,
                "children_count": {"type": "integer", "description": "ילדים עד גיל 23"},
                "current_car": {"type": "string"},
                "current_participation": _num,
                "current_maintenance": {**_num, "description": "אחזקת רכב חודשית אם אין רכב"},
                "target_action": {"type": "string", "enum": ["replace", "waiver"]},
                "target_car": {"type": "string"},
            },
        },
    },
    {
        "name": "get_allowed_cars",
        "description": "רשימת הרכבים שהעובד רשאי לבחור (כולל שווי שימוש רשמי), "
                       "זכאות למסלול ברוכת ילדים וזכאות לוויתור.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calculate_comparison",
        "description": "חישוב נטו היום מול נטו בחלופה, ההפרש וההסבר. "
                       "אם חסרים נתונים - מחזיר את רשימת החסרים.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask_representative",
        "description": (
            "העברת שאלה לנציג/ת שכר כשחסר נתון שהעובד לא אמור לדעת "
            "(למשל השתתפות חריגה ברכב מסוים). השיחה מוקפאת עד לתשובה."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "field": {"type": "string",
                          "enum": ["target_participation", "current_participation", "other"]},
            },
            "required": ["question"],
        },
    },
]


def execute_tool(name: str, args: dict[str, Any], state: ConversationState,
                 config: AppConfig) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {name!r}"}
    try:
        return fn(state, config, **(args or {}))
    except TypeError as e:  # bad arguments from the model
        return {"ok": False, "error": f"bad arguments: {e}"}
