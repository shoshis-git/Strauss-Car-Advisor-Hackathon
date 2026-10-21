"""Request / response models of the HTTP API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from ..agent.state import (FIELD_LABELS, ConversationState, CurrentSituation, EmployeeProfile,
                           TargetChoice)

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class StateView(BaseModel):
    profile: EmployeeProfile
    current: CurrentSituation
    target: TargetChoice
    missing_info: list[str]
    missing_labels: list[str]
    waiting_for_rep: bool
    rep_question: Optional[str]
    last_comparison: Optional[dict[str, Any]]

    @classmethod
    def of(cls, s: ConversationState) -> "StateView":
        return cls(profile=s.profile, current=s.current, target=s.target,
                   missing_info=s.missing_info,
                   missing_labels=[FIELD_LABELS[f] for f in s.missing_info],
                   waiting_for_rep=s.waiting_for_rep, rep_question=s.rep_question,
                   last_comparison=s.last_comparison)


class MessageView(BaseModel):
    role: str
    content: str
    created_at: str
    tool_name: Optional[str] = None


# --- employee ---------------------------------------------------------------- #
class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(None, pattern=SESSION_ID_PATTERN)
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    session_id: str
    bot_message: str
    state: StateView
    comparison: Optional[dict[str, Any]] = Field(
        None, description="תוצאת החישוב - רק כשכלי החישוב הופעל בתור הזה")
    tools_called: list[str]
    frozen: bool


class SessionResponse(BaseModel):
    session_id: str
    state: StateView
    messages: list[MessageView]
    created_at: str
    updated_at: str


# --- representative ---------------------------------------------------------- #
class CarView(BaseModel):
    car_id: str
    model_name: str
    group: str
    drive: str
    default_participation: float


class Ticket(BaseModel):
    session_id: str
    rep_question: str
    rep_field: Optional[str]
    waiting_since: str
    profile: EmployeeProfile
    current_car: Optional[CarView]
    current_participation: Optional[float]
    target_action: Optional[str]
    target_car: Optional[CarView]
    last_employee_message: Optional[str]


class RepAnswerRequest(BaseModel):
    answer_text: str = Field(..., min_length=1, max_length=2000,
                             examples=["ההשתתפות שאושרה היא 350 ₪"])
    participation: Optional[float] = Field(
        None, ge=0, description="ערך מפורש לשדה שנשאל; אם חסר - נלקח המספר מתוך answer_text")


class RepAnswerResponse(BaseModel):
    ok: bool
    session_id: str
    bot_message: str
    comparison: Optional[dict[str, Any]]
    state: StateView
