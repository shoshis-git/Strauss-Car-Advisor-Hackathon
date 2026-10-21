"""Tools the advisor agent can call. Each tool reads/updates the ConversationState
and returns a JSON-serialisable dict; all numbers come from the deterministic engine."""
from __future__ import annotations

import difflib
import re
from typing import Any, Callable, Optional

from ..data_loader.config_loader import AppConfig, Car, Employee
from ..engine.comparator import compare, display_round
from ..engine.eligibility import AlternativeRequest, check_eligibility, tax_value_of_use
from .state import FIELD_LABELS, ConversationState, compute_missing, is_blocked

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


FUZZY_OCCUPATION_CUTOFF = 0.85  # 'נציג מבירות שטח' (typo) -> 'נציג/ת מכירות שטח'


def resolve_occupation(config: AppConfig, text: str, fuzzy: bool = False) -> Optional[str]:
    """Longest occupation (any gender form) mentioned in text.

    fuzzy=True (for a tool argument that should *be* an occupation): if nothing matches
    literally, accept a near-identical spelling - but only when exactly one occupation
    is that close, so a typo is fixed and an unknown job is still rejected."""
    best: Optional[tuple[int, str]] = None
    for occ in config.occupations:
        for v in occupation_variants(occ.name):
            if v in text and (best is None or len(v) > best[0]):
                best = (len(v), occ.name)
    if best or not fuzzy:
        return best[1] if best else None
    t = " ".join(text.split())
    close = {occ.name for occ in config.occupations for v in occupation_variants(occ.name)
             if difflib.SequenceMatcher(None, t, v).ratio() >= FUZZY_OCCUPATION_CUTOFF}
    return close.pop() if len(close) == 1 else None


def _norm_tokens(text: str) -> list[str]:
    return [w for w in re.split(r"[\s\-_/,.()]+", text.casefold()) if w]


def resolve_car(config: AppConfig, text: str) -> Optional[Car]:
    """Car id ('CAR-10'), exact model name/alias - or one unambiguous catalogue car whose
    name is accompanied only by brand words / its registration year ('BYD Seal',
    'טויוטה קורולה', 'SEAL 2025'). Anything else ('קורולה 2030', 'Model Y') is not
    guessed."""
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

    found = find_cars_in_text(config, t)
    if len({c.car_id for _, c in found}) != 1:
        return None
    car = found[0][1]
    rest = t.casefold()
    for name in sorted(config.car_names(car), key=len, reverse=True):
        rest = re.sub(r"(?<![\w'׳])[ומלבהשכ]{0,3}-?" + re.escape(name.casefold()) + r"(?![\w'׳])",
                      " ", rest)
    for brand in sorted(config.car_brand_words, key=len, reverse=True):
        rest = re.sub(r"(?<![\w'׳])" + re.escape(brand.casefold()) + r"(?![\w'׳])", " ", rest)
    leftover = [w for w in _norm_tokens(rest) if w != str(car.registration_year)]
    return car if not leftover else None


def car_not_found_error(config: AppConfig, field: str, raw: str) -> str:
    """Rejection message; names catalogue cars mentioned inside the text as candidates."""
    msg = f"{field}: הרכב {raw!r} לא נמצא בקטלוג"
    hints = list(dict.fromkeys(f"{c.model_name} ({c.car_id})"
                               for _, c in find_cars_in_text(config, raw)))
    if hints:
        msg += (f" - דגמים קרובים בקטלוג: {' / '.join(hints)}. "
                "אם העובד אכן התכוון לאחד מהם - שמור אותו לפי הקוד")
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
NEW_CASE_WARNING = ("זוהה עובד/תרחיש חדש (עיסוק שונה מהקודם): כל נתוני התרחיש הקודם אופסו. "
                    "שמור רק את מה שנאמר בהודעה הנוכחית ובקש מחדש את כל השאר - "
                    "אל תשתמש בערכים מהשיחה הקודמת.")

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

    # A different occupation than the one on file = another employee / scenario in the
    # same chat: nothing of the previous case may leak into it (pension, credit points,
    # the previous car's participation...).
    if fields.get("occupation") and state.profile.occupation:
        occ = resolve_occupation(config, str(fields["occupation"]), fuzzy=True)
        if occ is not None and occ != state.profile.occupation:
            state.reset_case()
            warnings.append(NEW_CASE_WARNING)
    p, cur, tgt = state.profile, state.current, state.target

    for name, (section, typ) in NUMERIC_FIELDS.items():
        if fields.get(name) is None:
            continue
        if name == state.participation_unknown:
            errors.append(f"{name}: העובד ציין שאינו יודע את ההשתתפות - הערך נקבע רק "
                          "בתשובת נציג/ה (ask_representative), לא בניחוש ולא מברירת המחדל")
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

    # The employee's group comes from their occupation (occupations table) whenever the
    # occupation is known; a different group - typically the *car's* group - is rejected.
    if fields.get("occupation"):
        occ = resolve_occupation(config, str(fields["occupation"]), fuzzy=True)
        if occ is None:
            errors.append(f"occupation: העיסוק {fields['occupation']!r} לא נמצא בטבלת העיסוקים "
                          f"(קיימים: {', '.join(o.name for o in config.occupations)})")
        else:
            p.occupation = updated["occupation"] = occ
            occ_group = config.occupation(occ).eligibility_group
            if p.eligibility_group != occ_group:
                p.eligibility_group = updated["eligibility_group"] = occ_group

    if fields.get("eligibility_group"):
        g = str(fields["eligibility_group"]).strip().rstrip("'׳")
        if g not in config.rules.group_order:
            errors.append(f"eligibility_group: קבוצה לא קיימת {g!r} "
                          f"(קיימות: {', '.join(config.rules.group_order)})")
        elif p.occupation and g != config.occupation(p.occupation).eligibility_group:
            occ_group = config.occupation(p.occupation).eligibility_group
            errors.append(f"eligibility_group: לא עודכן - קבוצת הזכאות של העובד נקבעת לפי "
                          f"העיסוק '{p.occupation}' והיא {occ_group}. "
                          f"(קבוצה {g} היא אולי קבוצת הרכב, לא של העובד)")
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
                if cur.current_car_code not in (None, car.car_id):
                    # a participation belongs to one car: never carry it to another one
                    # (a value given before any car was named - e.g. from the payslip - stays)
                    if "current_participation" not in updated:
                        cur.current_participation = None
                    cur.current_maintenance = None
                cur.status, cur.current_car_code = "car", car.car_id
                updated["current_car"] = car.car_id

    if fields.get("target_action"):
        action = fields["target_action"]
        if action not in ("replace", WAIVER):
            errors.append(f"target_action: ערך לא תקין {action!r}")
        else:
            tgt.target_action = updated["target_action"] = action
            if action == WAIVER:
                tgt.target_car_code, tgt.target_participation = None, None

    if fields.get("target_car"):
        car = resolve_car(config, str(fields["target_car"]))
        if car is None:
            errors.append(car_not_found_error(config, "target_car", str(fields["target_car"])))
        else:
            if tgt.target_car_code != car.car_id and "target_participation" not in updated:
                tgt.target_participation = None  # a rep-approved amount belongs to one car
            tgt.target_action, tgt.target_car_code = "replace", car.car_id
            updated["target_car"] = car.car_id

    if updated:
        state.last_comparison = None  # inputs changed -> previous result is stale
    state.precheck = eligibility_precheck(state, config)
    state.missing_info = compute_missing(state, config)
    result = {
        "ok": not errors,
        "updated": updated,
        "errors": errors,
        "warnings": warnings,
        "missing_info": state.missing_info,
        "missing_labels": [FIELD_LABELS[f] for f in state.missing_info],
    }
    if state.precheck is not None:
        result["eligibility"] = state.precheck
    return result


# --------------------------------------------------------------------------- #
# Fail-fast eligibility
# --------------------------------------------------------------------------- #
MAX_ALTERNATIVES = 4


def suggest_alternatives(state: ConversationState, config: AppConfig,
                         like: Optional[Car] = None) -> list[dict[str, Any]]:
    """Permitted cars, closest to what was asked first: same drive as `like`, then the
    highest permitted group, then lower value of use."""
    emp = build_employee(state, config)
    ranks = {g: i for i, g in enumerate(config.rules.group_order)}
    allowed = [c for c in config.cars
               if c.car_id not in config.rules.family_track.cars
               and c.car_id != state.current.current_car_code
               and check_eligibility(config, emp, AlternativeRequest.car(c.car_id)).is_eligible]
    allowed.sort(key=lambda c: (like is not None and c.drive != like.drive,
                                -ranks.get(c.group, 0), tax_value_of_use(c)))
    return [_car_summary(c) for c in allowed[:MAX_ALTERNATIVES]]


def eligibility_precheck(state: ConversationState, config: AppConfig) -> Optional[dict]:
    """Is the requested alternative possible? Decided as soon as the request and the
    occupation/group are known (no salary needed); None = cannot be decided yet."""
    p, tgt = state.profile, state.target
    emp = build_employee(state, config)
    if tgt.target_action == WAIVER:
        if not p.occupation:
            return None
        res = check_eligibility(config, emp, AlternativeRequest.waiver())
        requested, like = "ויתור על הרכב", None
    elif tgt.target_action == "replace" and tgt.target_car_code and p.eligibility_group:
        like = config.car(tgt.target_car_code)
        if like.car_id in config.rules.family_track.cars and p.children_count is None:
            return None  # family track depends on the number of children
        res = check_eligibility(config, emp, AlternativeRequest.car(like.car_id))
        requested = f"{like.model_name} ({like.car_id}, קבוצה {like.group})"
    else:
        return None
    out: dict[str, Any] = {"eligible": res.is_eligible, "requested": requested,
                           "reason": res.reason}
    if res.is_eligible and like is not None and like.car_id in config.rules.family_track.cars:
        # engine-computed family-track figures, so the model never does this arithmetic
        out["family_track"] = {
            "company_monthly_cost": like.company_monthly_cost,
            "group_budget": config.rules.budget(p.eligibility_group),
            "gap_above_budget": res.scenario.family_gap,
            "employee_share_monthly_net_deduction": res.scenario.family_gap_deduction,
            "plus_regular_participation": res.scenario.participation,
        }
    if not res.is_eligible:
        out["alternatives"] = suggest_alternatives(state, config, like)
        out["instruction"] = ("הבקשה אינה אפשרית: אל תמשיך לאסוף נתוני שכר. הסבר לעובד את "
                              "הסיבה והצע את החלופות המותרות.")
    return out


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
    """Run the deterministic comparator - only when every required field is known.
    (A blocked request with complete data still gets a result: today's net + the reason.)"""
    state.missing_info = compute_missing(state, config)
    gaps = compute_missing(state, config, ignore_block=True)
    if gaps:
        result: dict[str, Any] = {"ok": False, "missing_info": gaps,
                                  "missing_labels": [FIELD_LABELS[f] for f in gaps]}
        if is_blocked(state):
            result["eligibility"] = state.precheck
        return result
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
        # where the alternative's participation comes from - say so to the employee
        "alternative_participation_source": (
            None if tgt.target_action == WAIVER
            else "approved_by_representative" if tgt.target_participation is not None
            else "company_catalogue_default"),
        # line-by-line breakdown of both sides (display values, whole shekels)
        "today": _side_breakdown(c.today, config),
        "alternative": _side_breakdown(c.alternative, config) if c.alternative else None,
    }
    state.last_comparison = result
    return result


def _side_breakdown(res, config: AppConfig) -> dict[str, Any]:
    sc, slip = res.scenario, res.payslip
    r = lambda v: display_round(v, config.rules)  # noqa: E731
    return {
        "label": sc.label,
        "kind": sc.kind,
        "car_id": sc.car_id,
        "is_electric": sc.is_electric,
        "value_of_use": r(sc.value_of_use),
        "participation": r(sc.participation),
        "maintenance_allowance": r(sc.maintenance_allowance),
        "electric_gross": r(res.electric_gross),
        "family_gap_deduction": r(sc.family_gap_deduction),
        "income_tax": r(slip.income_tax),
        "btl_health": r(slip.btl_and_health),
        "net": r(slip.net),
    }


def start_new_case(state: ConversationState, config: AppConfig) -> dict:
    """The employee moves to a new question about another employee / scenario."""
    state.reset_case()
    state.missing_info = compute_missing(state, config)
    return {"ok": True, "status": "new_case", "note": NEW_CASE_WARNING,
            "missing_labels": [FIELD_LABELS[f] for f in state.missing_info]}


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
    "start_new_case": start_new_case,
}

_num = {"type": "number"}
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "update_profile",
        "description": (
            "שמירת נתונים שהעובד מסר במפורש בשיחה. יש להעביר רק שדות שהעובד אמר - "
            "לעולם לא לנחש. רכב אפשר לציין בקוד (CAR-10), בשם הדגם כפי שהעובד כתב "
            "(למשל 'BYD Seal' או 'אטו 3'); אם הרכב נשמר (מופיע ב-updated) - אין צורך לבקש "
            "אישור מהעובד. רכב שלא בקטלוג יידחה. "
            "current_car='waiver' אם אין לעובד רכב והוא מקבל אחזקה. "
            "התוצאה כוללת eligibility כשאפשר כבר לדעת אם הבקשה אפשרית."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "eligibility_group": {
                    "type": "string",
                    "description": "קבוצת הזכאות של העובד (א/ב/ג/ד) - רק אם העובד ציין אותה. "
                                   "לעולם לא קבוצת הרכב. אם העיסוק ידוע הקבוצה נקבעת ממנו."},
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
            "העברת שאלה לנציג/ת שכר כשחסר נתון שהעובד לא אמור לדעת או אינו יודע "
            "(למשל השתתפות חריגה ברכב מסוים, או 'אין לי מושג כמה ההשתתפות'). "
            "השיחה מוקפאת עד לתשובה."
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
    {
        "name": "start_new_case",
        "description": (
            "פתיחת תרחיש חדש באותה שיחה: העובד שואל על עובד אחר / מצב אחר ולא ממשיך את "
            "הקודם. מאפס את כל הנתונים (שכר, הפרשות, נקודות זיכוי, רכבים, השתתפויות). "
            "להפעיל לפני update_profile של התרחיש החדש."
        ),
        "input_schema": {"type": "object", "properties": {}},
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
