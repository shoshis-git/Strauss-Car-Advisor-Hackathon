"""Request / response models of the HTTP API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from ..agent.state import (FIELD_LABELS, ConversationState, CurrentSituation, EmployeeProfile,
                           TargetChoice)

# Session ids are server-generated uuid4 hex (128 random bits) - never chosen by the client.
SESSION_ID_PATTERN = r"^[0-9a-f]{32}$"

# precheck keys meant for the language model only, never sent to the browser
_PRECHECK_INTERNAL = ("instruction",)


class StateView(BaseModel):
    display_name: Optional[str] = None
    profile: EmployeeProfile
    current: CurrentSituation
    target: TargetChoice
    missing_info: list[str]
    missing_labels: list[str]
    waiting_for_rep: bool
    rep_question: Optional[str]
    last_comparison: Optional[dict[str, Any]]
    precheck: Optional[dict[str, Any]] = None

    @classmethod
    def of(cls, s: ConversationState) -> "StateView":
        return cls(display_name=s.display_name,
                   profile=s.profile, current=s.current, target=s.target,
                   missing_info=s.missing_info,
                   missing_labels=[FIELD_LABELS[f] for f in s.missing_info],
                   waiting_for_rep=s.waiting_for_rep, rep_question=s.rep_question,
                   last_comparison=s.last_comparison,
                   precheck=({k: v for k, v in s.precheck.items() if k not in _PRECHECK_INTERNAL}
                             if s.precheck else None))


class MessageView(BaseModel):
    """A chat message as the employee sees it (internal tool messages are never returned)."""
    role: str
    content: str
    created_at: str


# --- employee ---------------------------------------------------------------- #
class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(
        None, pattern=SESSION_ID_PATTERN,
        description="אופציונלי. בהודעה הראשונה לא שולחים - השרת פותח שיחה ומחזיר session_id; "
                    "בהודעות הבאות שולחים את המזהה שהתקבל.")
    message: str = Field(..., min_length=1, max_length=4000)


CHAT_EXAMPLES = {
    "first": {"summary": "הודעה ראשונה (בלי session_id)",
              "value": {"message": "אני מנהל אזור מכירות, שכר בסיס 16,000"}},
    "next": {"summary": "הודעה בשיחה קיימת",
             "value": {"session_id": "0123456789abcdef0123456789abcdef",
                       "message": "פנסיה 6%, השתלמות 2.5%"}},
}


class ChatResponse(BaseModel):
    session_id: str
    bot_message: str
    state: StateView
    comparison: Optional[dict[str, Any]] = Field(
        None, description="תוצאת החישוב - רק כשכלי החישוב הופעל בתור הזה")
    frozen: bool
    llm_fallback: bool = Field(False, description="התור נענה במצב הדטרמיניסטי כי ה-LLM לא זמין")
    notice: Optional[str] = None


class AlternativeBody(BaseModel):
    """Quick switch: either a catalogue car or a waiver."""
    car_id: Optional[str] = Field(None, pattern=r"^CAR-\d{2}$")
    waiver: bool = False

    @model_validator(mode="after")
    def _exactly_one(self) -> "AlternativeBody":
        if bool(self.car_id) == self.waiver:
            raise ValueError("send either car_id or waiver=true")
        return self


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
    employee_name: Optional[str]
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
    employee_name: Optional[str]
    bot_message: str
    comparison: Optional[dict[str, Any]]
    waiting_for_rep: bool
    llm_fallback: bool = False
    notice: Optional[str] = None
