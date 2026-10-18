"""Conversation state of the car advisor + its SQLite persistence."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..data_loader import paths
from ..data_loader.config_loader import AppConfig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmployeeProfile(BaseModel):
    eligibility_group: Optional[str] = None
    occupation: Optional[str] = None
    base_salary: Optional[float] = None
    commissions_bonuses: Optional[float] = None
    pension_pct: Optional[float] = None
    hishtalmut_pct: Optional[float] = None
    credit_points: Optional[float] = None
    children_count: Optional[int] = None


class CurrentSituation(BaseModel):
    status: Optional[Literal["car", "waiver"]] = None
    current_car_code: Optional[str] = None
    current_participation: Optional[float] = None
    current_maintenance: Optional[float] = None  # when status == "waiver"


class TargetChoice(BaseModel):
    target_action: Optional[Literal["replace", "waiver"]] = None
    target_car_code: Optional[str] = None
    target_participation: Optional[float] = None  # None = catalogue default


class Message(BaseModel):
    role: Literal["employee", "assistant", "representative", "tool"]
    content: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    created_at: str = Field(default_factory=_now)


class ConversationState(BaseModel):
    conversation_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    profile: EmployeeProfile = Field(default_factory=EmployeeProfile)
    current: CurrentSituation = Field(default_factory=CurrentSituation)
    target: TargetChoice = Field(default_factory=TargetChoice)
    missing_info: list[str] = Field(default_factory=list)
    waiting_for_rep: bool = False
    rep_question: Optional[str] = None
    rep_field: Optional[str] = None  # state field the representative's answer fills
    last_comparison: Optional[dict[str, Any]] = None
    messages: list[Message] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def add_message(self, role: str, content: str, **kw: Any) -> Message:
        msg = Message(role=role, content=content, **kw)
        self.messages.append(msg)
        return msg

    def tool_calls(self) -> list[Message]:
        return [m for m in self.messages if m.role == "tool"]


# --------------------------------------------------------------------------- #
# Required information (in the order the advisor collects it)
# --------------------------------------------------------------------------- #
FIELD_LABELS: dict[str, str] = {
    "base_salary": "שכר בסיס",
    "eligibility_group": "קבוצת זכאות (או עיסוק)",
    "commissions_bonuses": "עמלות ובונוסים (0 אם אין)",
    "pension_pct": "אחוז הפרשת עובד לפנסיה",
    "hishtalmut_pct": "אחוז הפרשת עובד לקרן השתלמות (0 אם אין)",
    "credit_points": "נקודות זיכוי",
    "current_car_code": "הרכב הנוכחי (או שאין רכב ומקבלים אחזקה)",
    "current_participation": "ההשתתפות החודשית ברכב הנוכחי",
    "occupation": "העיסוק",
    "target_action": "החלופה המבוקשת (רכב אחר או ויתור)",
    "target_car_code": "הרכב המבוקש",
    "children_count": "מספר הילדים עד גיל 23",
}
PHASES: list[list[str]] = [
    ["base_salary", "eligibility_group"],
    ["commissions_bonuses", "pension_pct", "hishtalmut_pct", "credit_points"],
    ["current_car_code", "current_participation", "occupation",
     "target_action", "target_car_code", "children_count"],
]


def compute_missing(state: ConversationState, config: AppConfig) -> list[str]:
    """Fields still required before a comparison can be calculated."""
    p, cur, tgt = state.profile, state.current, state.target
    missing: list[str] = []
    for f in ("base_salary", "eligibility_group", "commissions_bonuses",
              "pension_pct", "hishtalmut_pct", "credit_points"):
        if getattr(p, f) is None:
            missing.append(f)
    if cur.status is None:
        missing.append("current_car_code")
    elif cur.status == "car" and cur.current_participation is None:
        missing.append("current_participation")
    needs_occupation = tgt.target_action == "waiver" or (
        cur.status == "waiver" and cur.current_maintenance is None)
    if needs_occupation and p.occupation is None:
        missing.append("occupation")
    if tgt.target_action is None:
        missing.append("target_action")
    elif tgt.target_action == "replace":
        if tgt.target_car_code is None:
            missing.append("target_car_code")
        elif (tgt.target_car_code in config.rules.family_track.cars
              and p.children_count is None):
            missing.append("children_count")
    return missing


def next_phase_fields(missing: list[str]) -> list[str]:
    """The missing fields of the earliest phase that still has gaps."""
    for phase in PHASES:
        gaps = [f for f in phase if f in missing]
        if gaps:
            return gaps
    return []


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class StateStore:
    """Conversations persisted as JSON documents in SQLite (app_data.db by default)."""

    def __init__(self, db_path: Path = paths.APP_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "conversation_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, "
                "waiting_for_rep INTEGER NOT NULL, updated_at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def save(self, state: ConversationState) -> None:
        state.updated_at = _now()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO conversations VALUES (?, ?, ?, ?)",
                    (state.conversation_id, state.model_dump_json(),
                     int(state.waiting_for_rep), state.updated_at),
                )
        finally:
            conn.close()

    def load(self, conversation_id: str) -> ConversationState:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM conversations WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(conversation_id)
        return ConversationState.model_validate(json.loads(row[0]))

    def waiting_for_rep(self) -> list[str]:
        """Conversation ids waiting for a representative (the rep's inbox)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT conversation_id FROM conversations WHERE waiting_for_rep=1 "
                "ORDER BY updated_at"
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]
