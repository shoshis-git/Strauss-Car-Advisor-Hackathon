"""HTTP endpoints: employee screen, catalogue, representative (human-in-the-loop)."""
from __future__ import annotations

import hmac
import threading
import weakref
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.security import APIKeyHeader

from ..agent.graph import AdvisorAgent, AgentReply
from ..agent.payslip import MAX_BYTES as PAYSLIP_MAX_BYTES
from ..agent.payslip import PayslipError, extract_payslip
from ..agent.state import ConversationState
from ..data_loader.config_loader import AppConfig
from ..engine.eligibility import tax_value_of_use
from .schemas import (CHAT_EXAMPLES, SESSION_ID_PATTERN, AlternativeBody, CarView, ChatRequest,
                      ChatResponse, MessageView, PayslipApplyRequest, PayslipExtractResponse,
                      RepAnswerRequest, RepAnswerResponse, SessionResponse, StateView, Ticket)

SessionId = Path(..., pattern=SESSION_ID_PATTERN)

_rep_key = APIKeyHeader(name="X-Rep-Token", auto_error=False,
                        description="מפתח נציג (REP_API_KEY בקובץ .env)")


def require_rep(request: Request, token: Optional[str] = Security(_rep_key)) -> None:
    """Representative / admin endpoints: a shared secret from REP_API_KEY. Fail closed -
    when no key is configured, nobody gets in."""
    expected: Optional[str] = request.app.state.rep_token
    if not expected:
        raise HTTPException(503, "גישת נציג לא הוגדרה בשרת (REP_API_KEY בקובץ .env)")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "מפתח נציג חסר או שגוי", headers={"WWW-Authenticate": "X-Rep-Token"})


router = APIRouter(prefix="/api")
rep_router = APIRouter(prefix="/api", dependencies=[Depends(require_rep)])


class SessionLocks:
    """One lock per session, so two requests never interleave on the same state.
    Weak values: a lock disappears once no request holds it (no unbounded growth)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()

    def __call__(self, session_id: str) -> "_Lock":
        with self._guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = self._locks[session_id] = _Lock()
            return lock


class _Lock:
    """threading.Lock cannot be weakly referenced; a thin wrapper can."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *exc: Any) -> None:
        self._lock.release()


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
        raise HTTPException(404, "session not found") from None


def _comparison_if_calculated(reply: AgentReply) -> Optional[dict[str, Any]]:
    return reply.state.last_comparison if "calculate_comparison" in reply.tools_called else None


def _chat_response(reply: AgentReply) -> ChatResponse:
    return ChatResponse(session_id=reply.state.conversation_id, bot_message=reply.text,
                        state=StateView.of(reply.state),
                        comparison=_comparison_if_calculated(reply),
                        frozen=reply.frozen,
                        llm_fallback=reply.llm_fallback, notice=reply.notice)


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
    from ..agent.rule_based import RuleBasedLLM
    return {"status": "ok", "rules_version": _config(request).rules.version,
            "deterministic": isinstance(_agent(request).llm, RuleBasedLLM)}


@rep_router.get("/llm/check")
def llm_check(request: Request) -> dict[str, Any]:
    """Diagnose the configured language model with one real (paid) request - rep key
    required. The API key itself is never shown, not even masked."""
    from ..agent.llm import LLMError
    from ..agent.state import ConversationState
    from ..agent.tools import TOOL_SPECS

    llm = _agent(request).llm
    info: dict[str, Any] = {"llm": type(llm).__name__, "model": getattr(llm, "model", None)}
    try:
        step = llm.step(system="ענה במילה אחת.", tools=TOOL_SPECS, state=ConversationState(),
                        messages=[{"role": "user", "content": "שלום"}])
    except LLMError as e:
        return {**info, "ok": False, "provider_status": e.status, "message": e.user_message}
    return {**info, "ok": True, "reply_preview": step.text[:120],
            "tool_calls": [c.name for c in step.tool_calls]}


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
            "model_name": c.model_name, "registration_year": c.registration_year,
            "drive": c.drive, "value_of_use": tax_value_of_use(c),
            "default_participation": c.default_employee_participation,
        } for c in cfg.cars],
        "occupations": [{"name": o.name, "eligibility_group": o.eligibility_group}
                        for o in cfg.occupations],
        "demo_employees": [{"employee_id": e.employee_id, "name": e.name,
                            "occupation": e.occupation, "question": e.question}
                           for e in cfg.employees],
    }


# --------------------------------------------------------------------------- #
# Employee screen
# --------------------------------------------------------------------------- #
@router.post("/chat", response_model=ChatResponse)
def chat(body: Annotated[ChatRequest, Body(openapi_examples=CHAT_EXAMPLES)],
         request: Request) -> ChatResponse:
    """First message: send `message` only - the server opens a conversation and returns
    its `session_id`. Later messages: send that `session_id` too."""
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
def get_session(request: Request, session_id: str = SessionId) -> SessionResponse:
    state = _load(_agent(request), session_id)
    messages = [MessageView(role=m.role, content=m.content, created_at=m.created_at)
                for m in state.messages if m.role != "tool"]
    return SessionResponse(session_id=session_id, state=StateView.of(state), messages=messages,
                           created_at=state.created_at, updated_at=state.updated_at)


@router.post("/demo/{emp_id}", response_model=ChatResponse)
def load_demo(request: Request, emp_id: str = Path(..., pattern=r"^E-\d{3}$")) -> ChatResponse:
    """Open a NEW conversation (server-generated id) with a sample employee (E-001..E-006)
    and compute their original question."""
    agent, cfg = _agent(request), _config(request)
    try:
        cfg.employee(emp_id)
    except KeyError:
        raise HTTPException(404, f"demo employee {emp_id} not found") from None
    session_id = agent.start_conversation().conversation_id
    with _locks(request)(session_id):
        reply = agent.load_demo_employee(session_id, emp_id)
    return _chat_response(reply)


@router.get("/session/{session_id}/options")
def session_options(request: Request, session_id: str = SessionId) -> dict[str, Any]:
    """Cars this employee may choose (own group or lower), family track and waiver."""
    from ..agent.tools import get_allowed_cars
    return get_allowed_cars(_load(_agent(request), session_id), _config(request))


@router.post("/session/{session_id}/alternative", response_model=ChatResponse)
def set_alternative(body: AlternativeBody, request: Request,
                    session_id: str = SessionId) -> ChatResponse:
    """Quick switch: choose another car (or waiver) and recalculate immediately."""
    agent = _agent(request)
    _load(agent, session_id)
    with _locks(request)(session_id):
        try:
            reply = agent.set_alternative(session_id, body.car_id, body.waiver)
        except KeyError:
            raise HTTPException(404, f"car {body.car_id} not in catalogue") from None
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None
    return _chat_response(reply)


# --------------------------------------------------------------------------- #
# Payslip upload: extract (nothing stored) -> employee confirms -> apply
# --------------------------------------------------------------------------- #
_TOO_BIG = f"הקובץ גדול מדי (מעל {PAYSLIP_MAX_BYTES // (1024 * 1024)}MB)"


@router.post("/payslip/extract", response_model=PayslipExtractResponse, openapi_extra={
    "requestBody": {"required": True,
                    "content": {"application/pdf": {"schema": {"type": "string",
                                                                "format": "binary"}}}}})
async def payslip_extract(request: Request) -> dict[str, Any]:
    """Body: the payslip PDF itself (Content-Type: application/pdf). The file is read in
    memory and discarded; identifying details are masked before the language model sees
    the text. Returns proposed values with the payslip line each came from."""
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/pdf":
        raise HTTPException(415, "אפשר להעלות רק תלוש שכר בפורמט PDF")
    if int(request.headers.get("content-length") or 0) > PAYSLIP_MAX_BYTES:
        raise HTTPException(413, _TOO_BIG)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > PAYSLIP_MAX_BYTES:  # a lying / missing Content-Length
            raise HTTPException(413, _TOO_BIG)
    try:
        res = await run_in_threadpool(extract_payslip, bytes(data), _agent(request).llm,
                                      _config(request))
    except PayslipError as e:
        raise HTTPException(e.status, str(e)) from None
    return res.as_dict()


@router.post("/payslip/apply", response_model=ChatResponse)
def payslip_apply(body: PayslipApplyRequest, request: Request) -> ChatResponse:
    """Save the values the employee confirmed (possibly corrected) and continue the chat.
    Without session_id a new conversation is opened."""
    agent = _agent(request)
    if body.session_id is None:
        session_id = agent.start_conversation().conversation_id
    else:
        session_id = body.session_id
        _load(agent, session_id)
    with _locks(request)(session_id):
        try:
            reply = agent.apply_payslip(session_id, body.values.model_dump(exclude_none=True))
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None
    return _chat_response(reply)


# --------------------------------------------------------------------------- #
# Representative screen (human in the loop)
# --------------------------------------------------------------------------- #
@rep_router.get("/rep/tickets", response_model=list[Ticket])
def rep_tickets(request: Request) -> list[Ticket]:
    agent, cfg = _agent(request), _config(request)
    tickets = []
    for sid in agent.store.waiting_for_rep():
        s = agent.store.load(sid)
        last_emp = next((m.content for m in reversed(s.messages) if m.role == "employee"), None)
        tickets.append(Ticket(
            session_id=sid, employee_name=s.display_name,
            rep_question=s.rep_question or "", rep_field=s.rep_field,
            waiting_since=s.updated_at, profile=s.profile,
            current_car=_car_view(cfg, s.current.current_car_code),
            current_participation=s.current.current_participation,
            target_action=s.target.target_action,
            target_car=_car_view(cfg, s.target.target_car_code),
            last_employee_message=last_emp,
        ))
    return tickets


@rep_router.post("/rep/tickets/{session_id}/answer", response_model=RepAnswerResponse)
def rep_answer(body: RepAnswerRequest, request: Request,
               session_id: str = SessionId) -> RepAnswerResponse:
    agent = _agent(request)
    with _locks(request)(session_id):
        if not _load(agent, session_id).waiting_for_rep:
            raise HTTPException(409, "session is not waiting for a representative")
        try:
            reply = agent.handle_rep_answer(session_id, body.answer_text, body.participation)
        except ValueError as e:  # e.g. no number in the answer for a numeric field
            raise HTTPException(422, str(e)) from None
    return RepAnswerResponse(ok=True, session_id=session_id,
                             employee_name=reply.state.display_name, bot_message=reply.text,
                             comparison=_comparison_if_calculated(reply),
                             waiting_for_rep=reply.state.waiting_for_rep,
                             llm_fallback=reply.llm_fallback, notice=reply.notice)
