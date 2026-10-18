"""HTTP endpoints: employee screen, catalogue, representative (human-in-the-loop)."""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path, Request

from ..agent.graph import AdvisorAgent, AgentReply
from ..agent.state import ConversationState
from ..data_loader.config_loader import AppConfig
from ..engine.eligibility import tax_value_of_use
from .schemas import (SESSION_ID_PATTERN, CarView, ChatRequest, ChatResponse, MessageView,
                      RepAnswerRequest, RepAnswerResponse, SessionResponse, StateView, Ticket)

router = APIRouter(prefix="/api")

SessionId = Path(..., pattern=SESSION_ID_PATTERN)


class SessionLocks:
    """One lock per session, so two requests never interleave on the same state."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def __call__(self, session_id: str) -> threading.Lock:
        with self._guard:
            return self._locks[session_id]


def _agent(request: Request) -> AdvisorAgent:
    return request.app.state.agent


def _config(request: Request) -> AppConfig:
    return request.app.state.config


def _locks(request: Request) -> SessionLocks:
    return request.app.state.session_locks


def _load(agent: AdvisorAgent, session_id: str) -> ConversationState:
    try:
        return agent.store.load(session_id)
    except KeyError:
        raise HTTPException(404, f"session {session_id} not found") from None


def _comparison_if_calculated(reply: AgentReply) -> Optional[dict[str, Any]]:
    return reply.state.last_comparison if "calculate_comparison" in reply.tools_called else None


def _chat_response(reply: AgentReply) -> ChatResponse:
    return ChatResponse(session_id=reply.state.conversation_id, bot_message=reply.text,
                        state=StateView.of(reply.state),
                        comparison=_comparison_if_calculated(reply),
                        tools_called=reply.tools_called, frozen=reply.frozen)


def _car_view(config: AppConfig, car_id: Optional[str]) -> Optional[CarView]:
    if not car_id:
        return None
    car = config.car(car_id)
    return CarView(car_id=car.car_id, model_name=car.model_name, group=car.group,
                   drive=car.drive, default_participation=car.default_employee_participation)


# --------------------------------------------------------------------------- #
# Health / catalogue
# --------------------------------------------------------------------------- #
@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    cfg = _config(request)
    return {"status": "ok", "rules_version": cfg.rules.version, "cars": len(cfg.cars),
            "occupations": len(cfg.occupations),
            "tax_records": request.app.state.tax_db.count(),
            "llm": type(_agent(request).llm).__name__}


@router.get("/catalog")
def catalog(request: Request) -> dict[str, Any]:
    cfg = _config(request)
    return {
        "groups": [{"group": g, "budget": cfg.rules.budget(g)} for g in cfg.rules.group_order],
        "family_track": {"min_children": cfg.rules.family_track.min_children,
                         "child_max_age": cfg.rules.family_track.child_max_age,
                         "cars": cfg.rules.family_track.cars},
        "cars": [{
            "car_id": c.car_id, "group": c.group, "manufacturer": c.manufacturer_name,
            "model_name": c.model_name, "model_description": c.model_description,
            "registration_year": c.registration_year, "drive": c.drive,
            "list_price": c.list_price, "value_of_use": tax_value_of_use(c),
            "company_monthly_cost": c.company_monthly_cost,
            "default_participation": c.default_employee_participation,
            "aliases": cfg.car_aliases.get(c.car_id, []),
        } for c in cfg.cars],
        "occupations": [o.model_dump() for o in cfg.occupations],
        "demo_employees": [{"employee_id": e.employee_id, "name": e.name,
                            "occupation": e.occupation, "question": e.question}
                           for e in cfg.employees],
    }


# --------------------------------------------------------------------------- #
# Employee screen
# --------------------------------------------------------------------------- #
@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    agent = _agent(request)
    if body.session_id is None:
        session_id = agent.start_conversation().conversation_id
    else:
        session_id = body.session_id
        _load(agent, session_id)  # 404 for an unknown session
    with _locks(request)(session_id):
        reply = agent.handle_employee_message(session_id, body.message)
    return _chat_response(reply)


@router.get("/session/{session_id}", response_model=SessionResponse)
def get_session(request: Request, session_id: str = SessionId,
                include_tools: bool = False) -> SessionResponse:
    state = _load(_agent(request), session_id)
    messages = [MessageView(role=m.role, content=m.content, created_at=m.created_at,
                            tool_name=m.tool_name)
                for m in state.messages if include_tools or m.role != "tool"]
    return SessionResponse(session_id=session_id, state=StateView.of(state), messages=messages,
                           created_at=state.created_at, updated_at=state.updated_at)


@router.post("/session/{session_id}/load_demo/{emp_id}", response_model=ChatResponse)
def load_demo(request: Request, session_id: str = SessionId,
              emp_id: str = Path(..., pattern=r"^E-\d{3}$")) -> ChatResponse:
    """Load a sample employee (E-001..E-006) and compute their original question.
    Creates the session if it does not exist yet."""
    agent = _agent(request)
    with _locks(request)(session_id):
        try:
            reply = agent.load_demo_employee(session_id, emp_id)
        except KeyError:
            raise HTTPException(404, f"demo employee {emp_id} not found") from None
    return _chat_response(reply)


# --------------------------------------------------------------------------- #
# Representative screen (human in the loop)
# --------------------------------------------------------------------------- #
@router.get("/rep/tickets", response_model=list[Ticket])
def rep_tickets(request: Request) -> list[Ticket]:
    agent, cfg = _agent(request), _config(request)
    tickets = []
    for sid in agent.store.waiting_for_rep():
        s = agent.store.load(sid)
        last_emp = next((m.content for m in reversed(s.messages) if m.role == "employee"), None)
        tickets.append(Ticket(
            session_id=sid, rep_question=s.rep_question or "", rep_field=s.rep_field,
            waiting_since=s.updated_at, profile=s.profile,
            current_car=_car_view(cfg, s.current.current_car_code),
            current_participation=s.current.current_participation,
            target_action=s.target.target_action,
            target_car=_car_view(cfg, s.target.target_car_code),
            last_employee_message=last_emp,
        ))
    return tickets


@router.post("/rep/tickets/{session_id}/answer", response_model=RepAnswerResponse)
def rep_answer(body: RepAnswerRequest, request: Request,
               session_id: str = SessionId) -> RepAnswerResponse:
    agent = _agent(request)
    with _locks(request)(session_id):
        if not _load(agent, session_id).waiting_for_rep:
            raise HTTPException(409, f"session {session_id} is not waiting for a representative")
        try:
            reply = agent.handle_rep_answer(session_id, body.answer_text, body.participation)
        except ValueError as e:  # e.g. no number in the answer for a numeric field
            raise HTTPException(422, str(e)) from None
    return RepAnswerResponse(ok=True, session_id=session_id, bot_message=reply.text,
                             comparison=_comparison_if_calculated(reply),
                             state=StateView.of(reply.state))
