"""Conversation flow: employee turn -> (LLM <-> tools)* -> reply, with a
representative hand-off that freezes the conversation until answered.

    employee msg ──► frozen? ──yes──► "waiting for rep" reply (no LLM, no tools)
                       │no
                       ▼
                 ┌─► LLM step ──tool calls──► execute tools ──► waiting_for_rep? ──yes──► hand-off reply
                 └────────────────────────────────┘                    │no
                       │text only                                       (loop)
                       ▼
                    reply + save state

    rep answer ──► fill rep_field, unfreeze ──► same loop (e.g. calculate)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..data_loader.config_loader import AppConfig, get_config
from .formatting import fmt_ils, format_comparison
from .llm import REP_PREFIX, LLMClient, LLMError, make_llm
from .prompts import build_system_prompt
from .state import (FIELD_LABELS, ConversationState, CurrentSituation, EmployeeProfile, StateStore,
                    TargetChoice, compute_missing, is_blocked, participation_unknown_field)
from .tools import TOOL_SPECS, execute_tool

MAX_TOOL_ROUNDS = 8
FROZEN_REPLY = ("השאלה שלך הועברה לנציג/ת שכר ואנחנו ממתינים לתשובה: «{q}». "
                "אחזור אליך עם החישוב ברגע שתתקבל תשובה.")
HANDOFF_REPLY = "העברתי את השאלה לנציג/ת שכר: «{q}». אחזור אליך ברגע שתתקבל תשובה."
UNKNOWN_PARTICIPATION_REPLY = (
    "לצורך חישוב מדויק נדרש גובה ההשתתפות הרשמי שנקבע עבורך {car} - לא אנחש אותו ולא אשתמש "
    "בברירת מחדל. פתחתי פנייה לנציג/ה לצורך קבלת גובה ההשתתפות המדויק. "
    "מיד עם קבלת הנתון נמשיך בחישוב.")
REP_NUMERIC_FIELDS = ("target_participation", "current_participation")
FALLBACK_NOTICE = ("מודל השפה אינו זמין כרגע (מכסה או תקלה זמנית), ולכן התשובה נוצרה במצב "
                   "הדטרמיניסטי. החישובים עצמם זהים - הם תמיד מתבצעים במנוע הקבוע.")

log = logging.getLogger("strauss.agent")
_DEFAULT = object()


@dataclass
class AgentReply:
    text: str
    state: ConversationState
    tools_called: list[str] = field(default_factory=list)
    frozen: bool = False
    llm_fallback: bool = False  # this turn was answered by the deterministic fallback
    notice: Optional[str] = None


def llm_transcript(state: ConversationState) -> list[dict[str, Any]]:
    """Text history for the LLM (facts live in the state snapshot of the system prompt)."""
    out: list[dict[str, Any]] = []
    for m in state.messages[state.case_start:]:  # previous cases never reach the model
        if m.role == "tool":
            continue
        role = "assistant" if m.role == "assistant" else "user"
        text = f"{REP_PREFIX} {m.content}" if m.role == "representative" else m.content
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    return out


class AdvisorAgent:
    def __init__(self, config: Optional[AppConfig] = None, store: Optional[StateStore] = None,
                 llm: Optional[LLMClient] = None, fallback_llm: Any = _DEFAULT,
                 fallback_cooldown_s: Optional[float] = None):
        """fallback_llm: used when the primary LLM fails. Default: NONE - an LLM failure is
        reported to the employee with its real cause. Opt in with LLM_FALLBACK=rules
        (deterministic brain answers instead) or pass a client explicitly."""
        from .rule_based import RuleBasedLLM

        self.config = config or get_config()
        self.store = store or StateStore()
        self.llm = llm or make_llm(self.config)
        if fallback_llm is _DEFAULT:
            wanted = os.environ.get("LLM_FALLBACK", "none").lower() == "rules"
            fallback_llm = (RuleBasedLLM(self.config)
                            if wanted and not isinstance(self.llm, RuleBasedLLM) else None)
        self.fallback_llm: Optional[LLMClient] = fallback_llm
        # after a transient failure (e.g. HTTP 429 quota) skip the primary for a while
        self.fallback_cooldown_s = (float(os.environ.get("LLM_FALLBACK_COOLDOWN_S", 300))
                                    if fallback_cooldown_s is None else fallback_cooldown_s)
        self._primary_down_until = 0.0

    # ------------------------------------------------------------------ #
    def start_conversation(self) -> ConversationState:
        state = ConversationState()
        state.missing_info = compute_missing(state, self.config)
        self.store.save(state)
        return state

    def handle_employee_message(self, conversation_id: str, text: str) -> AgentReply:
        state = self.store.load(conversation_id)
        state.add_message("employee", text)
        if state.waiting_for_rep:
            reply = FROZEN_REPLY.format(q=state.rep_question)
            state.add_message("assistant", reply)
            self.store.save(state)
            return AgentReply(reply, state, [], frozen=True)
        return self._run_turn(state, unknown=participation_unknown_field(text, state))

    def handle_rep_answer(self, conversation_id: str, answer: str,
                          value: Optional[float] = None) -> AgentReply:
        """Representative answers the pending question; the conversation resumes."""
        state = self.store.load(conversation_id)
        if not state.waiting_for_rep:
            raise ValueError(f"conversation {conversation_id} is not waiting for a representative")
        state.add_message("representative", answer)
        if state.rep_field in REP_NUMERIC_FIELDS:
            if value is None:
                m = re.search(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", answer)
                if m is None:
                    raise ValueError("representative answer has no numeric value for "
                                     f"{state.rep_field}")
                value = float(m.group(0).replace(",", ""))
            section = state.target if state.rep_field == "target_participation" else state.current
            setattr(section, state.rep_field, value)
            state.last_comparison = None
            if state.participation_unknown == state.rep_field:
                state.participation_unknown = None  # the official value has arrived
        state.waiting_for_rep, state.rep_question, state.rep_field = False, None, None
        state.missing_info = compute_missing(state, self.config)
        return self._run_turn(state)

    def _fill_from_employee(self, state: ConversationState, emp) -> None:
        """Profile and today's situation from the employee record (sample sheet / HR)."""
        state.display_name = f"{emp.name} ({emp.employee_id})"
        state.profile = EmployeeProfile(
            eligibility_group=emp.group, occupation=emp.occupation,
            base_salary=emp.base_salary, commissions_bonuses=emp.commissions + emp.bonus,
            pension_pct=emp.pension_employee_pct, hishtalmut_pct=emp.study_fund_employee_pct,
            credit_points=emp.credit_points, children_count=emp.children_under_23,
        )
        state.current = (
            CurrentSituation(status="car", current_car_code=emp.current_car_id,
                             current_participation=emp.current_participation)
            if emp.current_car_id else
            CurrentSituation(status="waiver", current_participation=0.0,
                             current_maintenance=emp.current_maintenance))
        state.target = TargetChoice()
        state.last_comparison = None
        state.waiting_for_rep, state.rep_question, state.rep_field = False, None, None
        state.precheck = None
        state.missing_info = compute_missing(state, self.config)

    def start_employee_session(self, employee_id: str) -> AgentReply:
        """A NEW conversation for an identified employee: their record is loaded up front,
        so the advisor opens personally and only the requested alternative is still asked.
        Deterministic greeting (no LLM call)."""
        emp = self.config.employee(employee_id)  # KeyError if unknown
        state = ConversationState()
        self._fill_from_employee(state, emp)
        p, cur = state.profile, state.current
        if cur.status == "car":
            car = self.config.car(cur.current_car_code)
            today = (f"רכב החברה שלך היום: {car.model_name} ({car.registration_year}), "
                     f"השתתפות {fmt_ils(cur.current_participation)} ₪ בחודש")
        else:
            today = (f"היום אין לך רכב חברה ואת/ה מקבל/ת אחזקת רכב של "
                     f"{fmt_ils(cur.current_maintenance or 0)} ₪")
        pay = [f"שכר בסיס {fmt_ils(p.base_salary)} ₪"]
        if p.commissions_bonuses:
            pay.append(f"עמלות ובונוסים {fmt_ils(p.commissions_bonuses)} ₪")
        pay += [f"פנסיה {p.pension_pct:g}%", f"קרן השתלמות {p.hishtalmut_pct:g}%",
                f"{p.credit_points:g} נקודות זיכוי"]
        first_name = emp.name.split()[0]
        reply = (f"שלום {first_name}! זיהיתי אותך כ{p.occupation} (קבוצת זכאות {p.eligibility_group}).\n"
                 f"טענתי מהמערכת את נתוני השכר שלך: {', '.join(pay)}.\n"
                 f"{today}.\n"
                 "מה תרצה/י לבדוק? למשל מעבר לרכב חשמלי, רכב אחר מהקטלוג או ויתור על הרכב. "
                 "אם משהו מהנתונים השתנה - כתוב/כתבי לי ואעדכן.")
        state.add_message("assistant", reply)
        self.store.save(state)
        return AgentReply(reply, state, [])

    def load_demo_employee(self, conversation_id: str, employee_id: str) -> AgentReply:
        """Fill the state from a sample employee and compute their original question.

        Deterministic (no LLM): profile and current car come from the sample sheet,
        the requested alternative is parsed from the employee's own question."""
        from .rule_based import extract_fields  # deterministic Hebrew parser

        emp = self.config.employee(employee_id)  # KeyError if unknown
        try:
            state = self.store.load(conversation_id)
        except KeyError:
            state = ConversationState(conversation_id=conversation_id)
        self._fill_from_employee(state, emp)

        state.add_message("employee", emp.question)
        target = {k: v for k, v in extract_fields(emp.question, state, self.config).items()
                  if k in ("target_car", "target_action")}
        called = []
        for name, args in (("update_profile", target), ("calculate_comparison", {})):
            result = execute_tool(name, args, state, self.config)
            state.add_message("tool", json.dumps(result, ensure_ascii=False),
                              tool_name=name, tool_input=args)
            called.append(name)
        if state.last_comparison:
            reply = format_comparison(state.last_comparison)
        else:
            reply = "טענתי את פרטי העובד/ת. לא זיהיתי את החלופה המבוקשת - לאיזה רכב לעבור, או לוותר?"
        state.add_message("assistant", reply)
        self.store.save(state)
        return AgentReply(reply, state, called)

    def apply_payslip(self, conversation_id: str, values: dict[str, Any]) -> AgentReply:
        """Values the employee confirmed from their payslip: saved with the same tool (and
        audit trail) as chat answers, then the advisor continues the conversation."""
        state = self.store.load(conversation_id)
        if state.waiting_for_rep:
            raise RuntimeError("conversation is waiting for a representative")
        shown = []
        for k, v in values.items():
            label = FIELD_LABELS[k].split(" (")[0]
            shown.append(f"{label}: {v}" if isinstance(v, str)
                         else f"{label}: {v:g}%" if k.endswith("_pct")
                         else f"{label}: {v:g}" if k == "credit_points"
                         else f"{label}: {fmt_ils(v)} ₪")
        state.add_message("employee", "📎 אישרתי את הנתונים מתלוש השכר: " + ", ".join(shown))
        if "current_participation" in values and state.participation_unknown == "current_participation":
            state.participation_unknown = None  # the payslip deduction is the official value
        result = execute_tool("update_profile", dict(values), state, self.config)
        state.add_message("tool", json.dumps(result, ensure_ascii=False),
                          tool_name="update_profile", tool_input=dict(values))
        return self._run_turn(state)

    def set_alternative(self, conversation_id: str, car_id: Optional[str] = None,
                        waiver: bool = False) -> AgentReply:
        """Quick switch from the UI: set the requested alternative and recalculate.
        Deterministic - no LLM; uses the same tools and leaves the same audit trail."""
        state = self.store.load(conversation_id)
        if state.waiting_for_rep:
            raise RuntimeError("conversation is waiting for a representative")
        if waiver:
            args, label = {"target_action": "waiver"}, "ויתור על הרכב"
        else:
            args, label = {"target_car": car_id}, self.config.car(car_id).model_name
        state.add_message("employee", f"בדיקת חלופה: {label}")
        called = []
        results = {}
        for name, a in (("update_profile", args), ("calculate_comparison", {})):
            results[name] = execute_tool(name, a, state, self.config)
            state.add_message("tool", json.dumps(results[name], ensure_ascii=False),
                              tool_name=name, tool_input=a)
            called.append(name)
        calc = results["calculate_comparison"]
        if calc.get("ok"):
            reply = format_comparison(calc)
        else:
            reply = "כדי לחשב את החלופה חסרים עוד נתונים: " + ", ".join(calc["missing_labels"])
        state.add_message("assistant", reply)
        self.store.save(state)
        return AgentReply(reply, state, called)

    # ------------------------------------------------------------------ #
    def _mark_unknown(self, state: ConversationState, field_name: Optional[str]) -> None:
        """The employee said they do not know this participation: it becomes a required,
        rep-only value (the calculation will not fall back to the catalogue default)."""
        section = state.current if field_name == "current_participation" else state.target
        if field_name and getattr(section, field_name) is None:
            state.participation_unknown = field_name
            state.missing_info = compute_missing(state, self.config)

    def _escalate_unknown_participation(self, state: ConversationState,
                                        called: list[str]) -> Optional[str]:
        """Deterministic hand-off - not left to the language model: as soon as the car is
        known, an unknown participation goes to the representative's screen."""
        f = state.participation_unknown
        if not f or state.waiting_for_rep or is_blocked(state):
            return None
        if f == "target_participation":
            car_id = state.target.target_car_code
            if state.target.target_action != "replace" or not car_id or \
                    state.target.target_participation is not None:
                return None
        else:
            car_id = state.current.current_car_code
            if state.current.status != "car" or not car_id or \
                    state.current.current_participation is not None:
                return None
        car = self.config.car(car_id)
        where = "ברכב המבוקש" if f == "target_participation" else "ברכב הנוכחי"
        args = {"question": f"העובד/ת אינו/ה יודע/ת מה גובה ההשתתפות החודשית {where} "
                            f"{car.model_name} ({car.car_id}). מה ההשתתפות שנקבעה לו/ה?",
                "field": f}
        result = execute_tool("ask_representative", args, state, self.config)
        state.add_message("tool", json.dumps(result, ensure_ascii=False),
                          tool_name="ask_representative", tool_input=args)
        called.append("ask_representative")
        return UNKNOWN_PARTICIPATION_REPLY.format(car=f"{where} ({car.model_name})")

    def _run_turn(self, state: ConversationState, unknown: Optional[str] = None) -> AgentReply:
        self._mark_unknown(state, unknown)
        state.missing_info = compute_missing(state, self.config)
        transcript = llm_transcript(state)
        called: list[str] = []
        reply: Optional[str] = None
        llm = self.llm
        if self.fallback_llm is not None and time.monotonic() < self._primary_down_until:
            llm = self.fallback_llm  # primary is cooling down after a transient failure

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                step = llm.step(system=build_system_prompt(state, self.config),
                                messages=transcript, tools=TOOL_SPECS, state=state)
            except LLMError as e:
                if llm is self.fallback_llm or self.fallback_llm is None:
                    raise
                # same transcript (incl. tools already run this turn) -> fallback continues it
                log.warning("primary LLM failed (status %s), using fallback: %s",
                            e.status, str(e)[:300])
                if e.transient:
                    self._primary_down_until = time.monotonic() + self.fallback_cooldown_s
                llm = self.fallback_llm
                continue
            if not step.tool_calls:
                reply = step.text
                break
            transcript.append(step.assistant_message)
            case_start = state.case_start
            for call in step.tool_calls:
                if state.waiting_for_rep:  # frozen mid-step: skip the remaining calls
                    result = {"ok": False, "error": "השיחה מוקפאת עד לתשובת נציג/ה"}
                else:
                    result = execute_tool(call.name, call.input, state, self.config)
                    called.append(call.name)
                    # a reset (new case) must not drop what this very message said
                    self._mark_unknown(state, unknown)
                payload = json.dumps(result, ensure_ascii=False)
                state.add_message("tool", payload, tool_name=call.name, tool_input=call.input)
                transcript.append({"role": "tool", "tool_call_id": call.id, "content": payload})
            if state.waiting_for_rep:
                reply = HANDOFF_REPLY.format(q=state.rep_question)
                break
            if state.case_start != case_start:
                # new case: the model continues from the new case's messages only, so
                # values from the previous case cannot be re-sent from its history
                transcript = llm_transcript(state)
        if reply is None:
            reply = "מצטער/ת, לא הצלחתי להשלים את הבקשה. אפשר לנסח שוב?"
        reply = self._escalate_unknown_participation(state, called) or reply

        state.add_message("assistant", reply)
        self.store.save(state)
        degraded = llm is not self.llm
        return AgentReply(reply, state, called, frozen=state.waiting_for_rep,
                          llm_fallback=degraded, notice=FALLBACK_NOTICE if degraded else None)
