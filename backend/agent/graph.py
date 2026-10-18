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
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..data_loader.config_loader import AppConfig, get_config
from .formatting import format_comparison
from .llm import REP_PREFIX, LLMClient, make_llm
from .prompts import build_system_prompt
from .state import (ConversationState, CurrentSituation, EmployeeProfile, StateStore,
                    TargetChoice, compute_missing)
from .tools import TOOL_SPECS, execute_tool

MAX_TOOL_ROUNDS = 8
FROZEN_REPLY = ("השאלה שלך הועברה לנציג/ת שכר ואנחנו ממתינים לתשובה: «{q}». "
                "אחזור אליך עם החישוב ברגע שתתקבל תשובה.")
HANDOFF_REPLY = "העברתי את השאלה לנציג/ת שכר: «{q}». אחזור אליך ברגע שתתקבל תשובה."
REP_NUMERIC_FIELDS = ("target_participation", "current_participation")


@dataclass
class AgentReply:
    text: str
    state: ConversationState
    tools_called: list[str] = field(default_factory=list)
    frozen: bool = False


def llm_transcript(state: ConversationState) -> list[dict[str, Any]]:
    """Text history for the LLM (facts live in the state snapshot of the system prompt)."""
    out: list[dict[str, Any]] = []
    for m in state.messages:
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
                 llm: Optional[LLMClient] = None):
        self.config = config or get_config()
        self.store = store or StateStore()
        self.llm = llm or make_llm(self.config)

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
        return self._run_turn(state)

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
        state.waiting_for_rep, state.rep_question, state.rep_field = False, None, None
        state.missing_info = compute_missing(state, self.config)
        return self._run_turn(state)

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

    # ------------------------------------------------------------------ #
    def _run_turn(self, state: ConversationState) -> AgentReply:
        state.missing_info = compute_missing(state, self.config)
        transcript = llm_transcript(state)
        called: list[str] = []
        reply: Optional[str] = None

        for _ in range(MAX_TOOL_ROUNDS):
            step = self.llm.step(system=build_system_prompt(state, self.config),
                                 messages=transcript, tools=TOOL_SPECS, state=state)
            if not step.tool_calls:
                reply = step.text
                break
            transcript.append(step.assistant_message)
            for call in step.tool_calls:
                if state.waiting_for_rep:  # frozen mid-step: skip the remaining calls
                    result = {"ok": False, "error": "השיחה מוקפאת עד לתשובת נציג/ה"}
                else:
                    result = execute_tool(call.name, call.input, state, self.config)
                    called.append(call.name)
                payload = json.dumps(result, ensure_ascii=False)
                state.add_message("tool", payload, tool_name=call.name, tool_input=call.input)
                transcript.append({"role": "tool", "tool_call_id": call.id, "content": payload})
            if state.waiting_for_rep:
                reply = HANDOFF_REPLY.format(q=state.rep_question)
                break
        if reply is None:
            reply = "מצטער/ת, לא הצלחתי להשלים את הבקשה. אפשר לנסח שוב?"

        state.add_message("assistant", reply)
        self.store.save(state)
        return AgentReply(reply, state, called, frozen=state.waiting_for_rep)
