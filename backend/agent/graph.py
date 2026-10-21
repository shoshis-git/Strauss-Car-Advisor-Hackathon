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
from .llm import REP_PREFIX, LLMClient, make_llm
from .prompts import build_system_prompt
from .state import ConversationState, StateStore, compute_missing
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
