"""Request / response models of the HTTP API."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


# --- login ------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    """Employee: emp_id + code. Guest: guest=true (an empty conversation)."""
    model_config = ConfigDict(extra="forbid")
    emp_id: Optional[str] = Field(None, pattern=r"^E-\d{3}$", examples=["E-001"])
    code: Optional[str] = Field(None, min_length=1, max_length=64)
    guest: bool = False

    @model_validator(mode="after")
    def _employee_or_guest(self) -> "LoginRequest":
        if self.guest == bool(self.emp_id or self.code):
            raise ValueError("send either emp_id + code, or guest=true")
        if not self.guest and not (self.emp_id and self.code):
            raise ValueError("emp_id and code are both required")
        return self


class LoginResponse(BaseModel):
    token: str = Field(description="לשלוח בכל בקשה: Authorization: Bearer <token>")
    token_type: Literal["bearer"] = "bearer"
    expires_at: int
    session_id: str
    kind: Literal["employee", "guest"]
    employee_name: Optional[str] = None
    bot_message: str
    state: "StateView"


# --- employee ---------------------------------------------------------------- #
class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(
        None, pattern=SESSION_ID_PATTERN,
        description="אופציונלי - השיחה נקבעת לפי הטוקן. אם נשלח, חייב להיות זהה לשיחה "
                    "שבטוקן.")
    message: str = Field(..., min_length=1, max_length=4000)


CHAT_EXAMPLES = {
    "first": {"summary": "הודעה (השיחה נקבעת לפי הטוקן)",
              "value": {"message": "אני מנהל אזור מכירות, שכר בסיס 16,000"}},
    "next": {"summary": "הודעה עם מזהה שיחה מפורש",
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


# --- payslip ------------------------------------------------------------------ #
class PayslipField(BaseModel):
    field: str
    label: str
    value: float | str
    evidence: str = Field(description="השורה מהתלוש (אחרי השחרה) שממנה נלקח הערך")


class PayslipCheck(BaseModel):
    ok: bool
    text: str


class PayslipExtractResponse(BaseModel):
    """Proposed values only - nothing is saved until the employee confirms them."""
    fields: list[PayslipField]
    not_found: list[str]
    rejected: list[str]
    source: Optional[Literal["text", "image"]] = Field(
        description="text = נקרא מהטקסט של ה-PDF ואומת מולו; image = נקרא מתמונת העמוד "
                    "במודל ראייה (אין טקסט עצמאי - לכן checks); null = לא נקרא (unreadable)")
    checks: list[PayslipCheck] = Field(
        default_factory=list, description="בדיקות החשבון של התלוש עצמו (רק בקריאה מתמונה)")
    unreadable: Optional[str] = Field(
        None, description="אין בקובץ טקסט קריא - הסבר לעובד. אפשר לשלוח שוב עם ?vision=1 "
                          "אחרי שהעובד הסכים לשלוח את תמונת התלוש למודל")
    vision_available: bool = Field(
        False, description="האם השרת מאפשר קריאה מתמונה (PAYSLIP_VISION ומודל שתומך בתמונות)")


class PayslipValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occupation: Optional[str] = Field(None, min_length=1, max_length=100)
    base_salary: Optional[float] = Field(None, gt=0, le=1_000_000)
    commissions_bonuses: Optional[float] = Field(None, ge=0, le=1_000_000)
    pension_pct: Optional[float] = Field(None, ge=0, le=100)
    hishtalmut_pct: Optional[float] = Field(None, ge=0, le=100)
    credit_points: Optional[float] = Field(None, ge=0, le=30)
    current_participation: Optional[float] = Field(None, ge=0, le=100_000)


class PayslipApplyRequest(BaseModel):
    session_id: Optional[str] = Field(
        None, pattern=SESSION_ID_PATTERN,
        description="אופציונלי - השיחה נקבעת לפי הטוקן; אם נשלח, חייב להיות זהה לה")
    values: PayslipValues

    @model_validator(mode="after")
    def _not_empty(self) -> "PayslipApplyRequest":
        if not self.values.model_dump(exclude_none=True):
            raise ValueError("no values to apply")
        return self


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
