"""Deterministic stand-in for the LLM: Hebrew pattern extraction + a fixed policy.

It speaks the same protocol as AnthropicLLM (tool calls in, text out), so the
agent loop, tools and state handling are exercised identically in tests.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..data_loader.config_loader import AppConfig
from .formatting import fmt_ils, format_blocked, format_comparison
from .llm import REP_PREFIX, LLMStep, ToolCall, assistant_message
from .state import FIELD_LABELS, ConversationState, is_blocked, next_phase_fields
from .tools import WAIVER, find_cars_in_text, resolve_occupation

NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _first(patterns: list[str], text: str) -> Optional[float]:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return _num(m.group(1))
    return None


# words that may sit between a label and its amount: "עמלות משתנות כ-1,000"
AMOUNT_FILLER = (r"(?:\s+(?:משתנות|משתנים|חודשיות|חודשיים|בחודש|בערך|בסביבות|בסך|של|הן|הם))*"
                 r"\s*[:\-]?\s*(?:כ\s*-?\s*|כ־\s*)?")
# a shekel amount said on its own, e.g. "קבוצה ג, 14,200 ₪"
_BARE_SHEKELS = re.compile(NUM + r"\s*(?:₪|ש\"ח|ש״ח|שקל(?:ים)?)")
# any of these before the amount means it belongs to something else
_OTHER_MONEY = re.compile(r"עמל|בונוס|השתתפ|אחזק|שווי|פנסי|השתלמ|זיכוי|נטו|ברוטו|מחיר|עלות|תקציב|החזר")
MIN_BARE_SALARY = 2000  # smaller amounts are participations / allowances, not a salary


def _bare_salary(text: str) -> Optional[float]:
    """A single shekel amount with no label, as an answer to "what is your base salary"."""
    hits = [m for m in _BARE_SHEKELS.finditer(text)
            if not _OTHER_MONEY.search(text[max(0, m.start() - 25):m.start()])]
    if len(hits) != 1:
        return None  # none, or ambiguous - ask instead of guessing
    v = _num(hits[0].group(1))
    return v if v >= MIN_BARE_SALARY else None


TARGET_CUES = r"(לעבור|אעבור|להחליף|אחליף|לקחת|אקח|רוצה|מבקש|מבקשת|שוקל|שוקלת|מעוניין|מעוניינת|ל-|ל־)"
CURRENT_CUES = r"(יש לי|נוהג|נוהגת|נוסע|נוסעת|נוכחי|היום|כרגע)"
REP_CUES = r"(לא\s*(?:יודע|יודעת|בטוח|בטוחה)|מיוחדת|חריגה)"
LIST_CUES = r"(אילו רכבים|איזה רכבים|אילו רכבים|רשימת (?:ה)?רכבים|מה מותר לי|באיזה רכב(?:ים)? אפשר)"


def extract_fields(text: str, state: ConversationState, config: AppConfig) -> dict[str, Any]:
    """Fields explicitly stated in one employee message (nothing inferred)."""
    f: dict[str, Any] = {}

    v = _first([r"(?:שכר|משכורת)\s*(?:ה?בסיס\s*)?(?:שלי\s*)?(?:הוא\s*)?(?:של\s*)?[:\-]?\s*" + NUM], text)
    if v is None and state.profile.base_salary is None:
        v = _bare_salary(text)  # "קבוצה ג, 14,200 ₪" - the answer to the salary question
    if v is not None:
        f["base_salary"] = v

    m = re.search(r"קבוצ(?:ה|ת)\s*(?:זכאות\s*)?([אבגד])(?![א-ת])", text)
    if m:
        f["eligibility_group"] = m.group(1)
    occ = resolve_occupation(config, text)
    if occ:
        f["occupation"] = occ

    commissions = _first([r"עמלות" + AMOUNT_FILLER + NUM], text)
    bonus = _first([r"בונוס(?:ים)?" + AMOUNT_FILLER + NUM], text)
    if commissions is not None or bonus is not None:
        f["commissions_bonuses"] = (commissions or 0) + (bonus or 0)
    elif re.search(r"(?:אין|בלי|ללא)\s*(?:לי\s*)?(?:עמלות|בונוס)", text):
        f["commissions_bonuses"] = 0.0

    v = _first([NUM + r"\s*%\s*(?:הפרשה\s*)?(?:ל|ב)?פנסיה",
                r"פנסיה\s*(?:עובד\s*)?[:\-]?\s*" + NUM], text)
    if v is not None:
        f["pension_pct"] = v
    v = _first([NUM + r"\s*%\s*(?:ל|ב)?(?:קרן\s*)?(?:ה)?השתלמות",
                r"השתלמות\s*[:\-]?\s*" + NUM], text)
    if v is not None:
        f["hishtalmut_pct"] = v
    elif re.search(r"(?:אין|בלי|ללא)\s*(?:לי\s*)?(?:קרן\s*)?(?:ה)?השתלמות", text):
        f["hishtalmut_pct"] = 0.0

    v = _first([NUM + r"\s*נקודות(?:\s*זיכוי)?", r"נקודות\s*זיכוי\s*[:\-]?\s*" + NUM], text)
    if v is not None:
        f["credit_points"] = v

    v = _first([NUM + r"\s*ילדים"], text)
    if v is not None:
        f["children_count"] = int(v)
    elif re.search(r"ילד(?:ה)?\s*(?:אחד|אחת)", text):
        f["children_count"] = 1
    elif re.search(r"(?:אין\s*לי|בלי|ללא)\s*ילדים", text):
        f["children_count"] = 0

    # Current situation: waiver with maintenance
    if re.search(r"אין לי רכב|מקבל(?:ת)?\s*אחזקה|אני בוויתור", text):
        f["current_car"] = WAIVER
        v = _first([r"אחזקה\s*(?:של\s*)?" + NUM], text)
        if v is not None:
            f["current_maintenance"] = v
    if re.search(r"לוותר|אוותר", text):
        f["target_action"] = WAIVER

    # Cars mentioned: two -> 'from X to Y'; one -> classify by the words before it
    cars = find_cars_in_text(config, text)
    if len(cars) >= 2:
        f["current_car"], f["target_car"] = cars[0][1].car_id, cars[-1][1].car_id
    elif len(cars) == 1:
        pos, car = cars[0]
        before = text[max(0, pos - 30):pos]
        if re.search(TARGET_CUES, before):
            f["target_car"] = car.car_id
        elif re.search(CURRENT_CUES, before) or state.current.status is None:
            f["current_car"] = car.car_id
        else:
            f["target_car"] = car.car_id

    # Participation the employee pays today (a new-car participation is the rep's call)
    if not re.search(REP_CUES, text):
        v = _first([r"השתתפות\s*(?:של\s*)?(?:היום\s*)?(?:היא\s*)?[:\-]?\s*" + NUM,
                    NUM + r"\s*(?:ש\"ח|₪)?\s*השתתפות"], text)
        if v is not None:
            f["current_participation"] = v
        elif re.search(r"(?:בלי|ללא|אין)\s*(?:לי\s*)?השתתפות", text):
            f["current_participation"] = 0.0
    return f


def _last_user_text(messages: list[dict[str, Any]]) -> tuple[int, str]:
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m["role"] == "user" and isinstance(m["content"], str):
            return i, m["content"]
    return -1, ""


def _turn_results(messages: list[dict[str, Any]], start: int) -> dict[str, dict]:
    """tool name -> result, for tools already executed in the current turn."""
    names: dict[str, str] = {}
    results: dict[str, dict] = {}
    for m in messages[start + 1:]:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["function"]["name"]
        if m["role"] == "tool":
            results[names[m["tool_call_id"]]] = json.loads(m["content"])
    return results


_fmt = fmt_ils


class RuleBasedLLM:
    def __init__(self, config: AppConfig):
        self.config = config
        self._n = 0

    def _call(self, name: str, args: dict[str, Any]) -> ToolCall:
        self._n += 1
        return ToolCall(f"rb_{self._n}", name, args)

    def step(self, *, system, messages, tools, state: ConversationState) -> LLMStep:
        start, text = _last_user_text(messages)
        done = _turn_results(messages, start)
        calls: list[ToolCall] = []

        if not done:  # first step of the turn
            if not text.startswith(REP_PREFIX):
                fields = extract_fields(text, state, self.config)
                if fields:
                    calls.append(self._call("update_profile", fields))
                if re.search(LIST_CUES, text):
                    calls.append(self._call("get_allowed_cars", {}))
                if re.search(REP_CUES, text) and "השתתפות" in text:
                    calls.append(self._call("ask_representative", self._rep_question(state, fields)))
        if (not calls and "calculate_comparison" not in done and not state.waiting_for_rep
                and not state.missing_info and state.target.target_action
                and state.last_comparison is None):
            calls.append(self._call("calculate_comparison", {}))

        if calls:
            return LLMStep("", calls, assistant_message("", calls))
        reply = self._compose(state, done)
        return LLMStep(reply, [], assistant_message(reply, []))

    def _rep_question(self, state: ConversationState, fields: dict[str, Any]) -> dict[str, Any]:
        car_id = fields.get("target_car") or state.target.target_car_code
        car = f"ברכב {self.config.car(car_id).model_name} ({car_id})" if car_id else "ברכב המבוקש"
        return {"question": f"העובד/ת אינו/ה יודע/ת מה גובה ההשתתפות החודשית {car}. "
                            "מה ההשתתפות שאושרה?",
                "field": "target_participation"}

    def _compose(self, state: ConversationState, done: dict[str, dict]) -> str:
        parts: list[str] = []
        upd = done.get("update_profile")
        if upd:
            if upd["updated"]:
                parts.append("תודה, עדכנתי: " + ", ".join(
                    self._describe(k, v) for k, v in upd["updated"].items()) + ".")
            parts += [f"שימ/י לב: {e}" for e in upd["errors"] + upd["warnings"]]

        allowed = done.get("get_allowed_cars")
        if allowed and allowed.get("ok"):
            lines = [f"• {c['model_name']} ({c['car_id']}, קבוצה {c['group']}, {c['drive']}) - "
                     f"שווי שימוש {_fmt(c['value_of_use'])} ₪" for c in allowed["allowed_cars"]]
            parts.append(f"הרכבים המותרים לקבוצה {allowed['group']}:\n" + "\n".join(lines))
            parts.append("ויתור: " + allowed["waiver"]["reason"])
            parts.append("מסלול ברוכת ילדים: " + (allowed["family_track"]["reason"] or ""))

        calc = done.get("calculate_comparison")
        if calc and calc.get("ok"):
            parts.append(format_comparison(calc))
        elif is_blocked(state):
            parts.append(format_blocked(state.precheck))
        elif state.missing_info:
            ask = [FIELD_LABELS[f] for f in next_phase_fields(state.missing_info)]
            parts.append("כדי להמשיך אשמח לדעת: " + ", ".join(ask) + ".")
        return "\n".join(parts) or "איך אפשר לעזור?"

    def _describe(self, key: str, value: Any) -> str:
        label = FIELD_LABELS.get(key, key).split(" (")[0]
        if key in ("current_car", "target_car"):
            if value == WAIVER:
                return "אין רכב כיום (אחזקה)"
            label = "רכב נוכחי" if key == "current_car" else "רכב מבוקש"
            return f"{label}: {self.config.car(value).model_name}"
        if key == "target_action":
            return "חלופה: " + ("ויתור על הרכב" if value == WAIVER else "החלפת רכב")
        return f"{label}: {_fmt(value) if isinstance(value, (int, float)) else value}"
