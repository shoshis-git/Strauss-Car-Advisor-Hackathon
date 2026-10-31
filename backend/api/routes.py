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
from ..agent.payslip import PayslipError, PayslipUnreadable, extract_payslip, vision_enabled
from ..agent.state import ConversationState
from ..data_loader.config_loader import AppConfig
from ..engine.eligibility import tax_value_of_use
from .auth import (FORBIDDEN, Claims, LoginThrottle, ensure_owner, issue_token, require_claims,
                   session_claims, verify_token)
from .schemas import (CHAT_EXAMPLES, SESSION_ID_PATTERN, AlternativeBody, CarView, ChatRequest,
                      ChatResponse, ConversationSummary, LoginRequest, LoginResponse, MessageView, PayslipApplyRequest,
                      PayslipExtractResponse, RepAnswerRequest, RepAnswerResponse,
                      SessionResponse, StateView, Ticket)

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
    """Diagnose the running language model(s) - one real (paid) request to each provider
    in the chain (LLM_PROVIDER, LLM_PROVIDER_2, ...). Rep key required. API keys are never
    shown, not even masked."""
    from ..agent.llm_check import probe_client

    llm = _agent(request).llm
    providers = []
    for client in getattr(llm, "clients", [llm]):
        r = probe_client(client)
        providers.append({"provider": getattr(client, "label", type(client).__name__),
                          "ok": r["ok"], "seconds": r["seconds"],
                          **({"tool_calls": r["tool_calls"], "reply_preview": r["text"][:120]}
                             if r["ok"] else
                             {"provider_status": r["status"], "message": r["message"]})})
    return {"llm": type(llm).__name__, "model": getattr(llm, "model", None),
            "ok": any(p["ok"] for p in providers), "providers": providers}


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
        # demo accounts for the login screen - fictional people; their access code is
        # published on purpose (judges log in with one click). Pilot: SSO instead.
        "demo_employees": [{"employee_id": e.employee_id, "name": e.name,
                            "occupation": e.occupation,
                            "demo_code": request.app.state.demo_access.code_for(e.employee_id)}
                           for e in cfg.employees],
    }


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
LOGIN_FAILED = "מספר עובד או קוד שגויים"
LOGIN_LOCKED = "יותר מדי ניסיונות כניסה שגויים. נסו שוב בעוד כמה דקות."


def _last_assistant_message(state: ConversationState) -> str:
    return next((m.content for m in reversed(state.messages) if m.role == "assistant"), "")


def _token_response(request: Request, state: ConversationState, kind: str,
                    emp_id: Optional[str], bot_message: str) -> LoginResponse:
    """A signed token bound to this conversation, and what the screen needs to show it."""
    secret = request.app.state.secret_key
    name = _config(request).employee(emp_id).name if emp_id else None
    token = issue_token(secret, state.conversation_id, kind, emp_id)
    return LoginResponse(token=token, expires_at=verify_token(secret, token).exp,
                         session_id=state.conversation_id, kind=kind, employee_name=name,
                         bot_message=bot_message, state=StateView.of(state))


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Employee: `emp_id` + `code` - back to their most recent conversation, or a new one
    with the employee's record loaded up front (occupation, group, pay, current car).
    Guest: `guest: true` - an empty conversation. Returns a signed token bound to that
    conversation."""
    agent, cfg = _agent(request), _config(request)
    throttle: LoginThrottle = request.app.state.login_throttle
    if body.guest:
        state = agent.start_conversation()
        greeting = GUEST_GREETING
        state.add_message("assistant", greeting)
        agent.store.save(state)
        kind, emp_id = "guest", None
    else:
        emp_id = body.emp_id
        if throttle.locked(emp_id):
            raise HTTPException(429, LOGIN_LOCKED)
        known = emp_id in {e.employee_id for e in cfg.employees}
        # same answer for an unknown number and a wrong code: nothing to enumerate
        if not (known and request.app.state.demo_access.check(emp_id, body.code)):
            throttle.failed(emp_id)
            raise HTTPException(401, LOGIN_FAILED)
        throttle.succeeded(emp_id)
        latest = agent.store.list_for_employee(emp_id, limit=1)
        if latest:
            state = latest[0]
            greeting = _last_assistant_message(state)
        else:
            reply = agent.start_employee_session(emp_id)
            state, greeting = reply.state, reply.text
        kind = "employee"
    return _token_response(request, state, kind, emp_id, greeting)


GUEST_GREETING = ("שלום! אני יועץ הרכב. ספר/י לי על התפקיד, השכר והרכב שלך היום, ואחשב "
                  "מה יקרה לנטו שלך אם תעבור/י לרכב אחר, לרכב חשמלי או תוותר/י על הרכב. "
                  "אפשר גם להעלות תלוש שכר.")


# --------------------------------------------------------------------------- #
# My conversations (employees who logged in with a number and a code)
# --------------------------------------------------------------------------- #
EMPLOYEES_ONLY = "רק עובד שנכנס עם מספר עובד וקוד יכול לשמור כמה שיחות"
_TITLE_LEN = 40


def _employee_claims(claims: Claims = Depends(require_claims)) -> Claims:
    if claims.kind != "employee" or not claims.emp_id:
        raise HTTPException(403, EMPLOYEES_ONLY)
    return claims


def _conversation_title(state: ConversationState, cfg: AppConfig) -> str:
    """The requested alternative; otherwise the start of the employee's first message."""
    tgt = state.target
    if tgt.target_action == "waiver":
        return "ויתור על הרכב"
    if tgt.target_action == "replace" and tgt.target_car_code:
        try:
            return f"מעבר ל-{cfg.car(tgt.target_car_code).model_name}"
        except KeyError:
            pass
    first = next((m.content.strip() for m in state.messages if m.role == "employee"), "")
    if not first:
        return "שיחה חדשה"
    return first if len(first) <= _TITLE_LEN else first[:_TITLE_LEN].rstrip() + "…"


@router.get("/conversations", response_model=list[ConversationSummary])
def my_conversations(request: Request,
                     claims: Claims = Depends(_employee_claims)) -> list[ConversationSummary]:
    """The logged-in employee's conversations, most recent first."""
    cfg = _config(request)
    return [ConversationSummary(session_id=s.conversation_id,
                                title=_conversation_title(s, cfg),
                                created_at=s.created_at, updated_at=s.updated_at,
                                waiting_for_rep=s.waiting_for_rep)
            for s in _agent(request).store.list_for_employee(claims.emp_id)]


@router.post("/conversations", response_model=LoginResponse)
def new_conversation(request: Request,
                     claims: Claims = Depends(_employee_claims)) -> LoginResponse:
    """A new conversation with the employee's record loaded; the earlier ones are kept.
    Returns a token bound to the new conversation."""
    reply = _agent(request).start_employee_session(claims.emp_id)
    return _token_response(request, reply.state, "employee", claims.emp_id, reply.text)


@router.post("/conversations/{session_id}/open", response_model=LoginResponse)
def open_conversation(request: Request, session_id: str = SessionId,
                      claims: Claims = Depends(_employee_claims)) -> LoginResponse:
    """Back to one of my earlier conversations: a token bound to it."""
    state = _load(_agent(request), session_id)
    # another employee's conversation (or a guest one) looks the same as a missing one
    if state.owner_emp_id is None or not hmac.compare_digest(state.owner_emp_id,
                                                             claims.emp_id):
        raise HTTPException(404, "session not found")
    return _token_response(request, state, "employee", claims.emp_id,
                           _last_assistant_message(state))


def _own_session(claims: Claims, body_session_id: Optional[str]) -> str:
    """The conversation of the token; an explicit id in the body must be the same one."""
    if body_session_id is not None:
        ensure_owner(claims, body_session_id)
    return claims.sid


# --------------------------------------------------------------------------- #
# Employee screen (every route: a signed token that owns the conversation)
# --------------------------------------------------------------------------- #
@router.post("/chat", response_model=ChatResponse)
def chat(body: Annotated[ChatRequest, Body(openapi_examples=CHAT_EXAMPLES)],
         request: Request, claims: Claims = Depends(require_claims)) -> ChatResponse:
    """A message in the token's conversation (opened at login)."""
    agent = _agent(request)
    session_id = _own_session(claims, body.session_id)
    _load(agent, session_id)  # 404 if the conversation no longer exists
    with _locks(request)(session_id):
        reply = agent.handle_employee_message(session_id, body.message)
    return _chat_response(reply)


@router.get("/session/{session_id}", response_model=SessionResponse,
            dependencies=[Depends(session_claims)])
def get_session(request: Request, session_id: str = SessionId) -> SessionResponse:
    state = _load(_agent(request), session_id)
    messages = [MessageView(role=m.role, content=m.content, created_at=m.created_at)
                for m in state.messages if m.role != "tool"]
    return SessionResponse(session_id=session_id, state=StateView.of(state), messages=messages,
                           created_at=state.created_at, updated_at=state.updated_at)


@router.post("/demo/{emp_id}", response_model=ChatResponse)
def load_demo(request: Request, emp_id: str = Path(..., pattern=r"^E-\d{3}$"),
              claims: Claims = Depends(require_claims)) -> ChatResponse:
    """Ask the sample employee's original question (from the challenge sheet) in the
    token's conversation and compute it. Only the logged-in employee's own record."""
    agent, cfg = _agent(request), _config(request)
    try:
        cfg.employee(emp_id)
    except KeyError:
        raise HTTPException(404, f"demo employee {emp_id} not found") from None
    if claims.kind != "employee" or claims.emp_id != emp_id:
        raise HTTPException(403, FORBIDDEN)
    _load(agent, claims.sid)
    with _locks(request)(claims.sid):
        reply = agent.load_demo_employee(claims.sid, emp_id)
    return _chat_response(reply)


@router.get("/session/{session_id}/options", dependencies=[Depends(session_claims)])
def session_options(request: Request, session_id: str = SessionId) -> dict[str, Any]:
    """Cars this employee may choose (own group or lower), family track and waiver."""
    from ..agent.tools import get_allowed_cars
    return get_allowed_cars(_load(_agent(request), session_id), _config(request))


@router.get("/session/{session_id}/ranking", dependencies=[Depends(session_claims)])
def session_ranking(request: Request, session_id: str = SessionId) -> dict[str, Any]:
    """All my options: net pay for every permitted alternative, best first (engine only,
    no LLM; the conversation is not changed)."""
    from ..agent.tools import rank_alternatives
    return rank_alternatives(_load(_agent(request), session_id), _config(request))


@router.post("/session/{session_id}/alternative", response_model=ChatResponse,
             dependencies=[Depends(session_claims)])
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
                                                                "format": "binary"}}}}},
    dependencies=[Depends(require_claims)])  # a paid model call: logged-in users only
async def payslip_extract(request: Request, vision: bool = False) -> dict[str, Any]:
    """Body: the payslip PDF itself (Content-Type: application/pdf). The file is read in
    memory and discarded; identifying details are masked before the language model sees
    the text. Returns proposed values with the payslip line each came from.

    A PDF without readable text (scan, glyph-code fonts) comes back as `unreadable`; the
    page image is sent to the vision model only when the request repeats the upload with
    `?vision=1`, i.e. after the employee consented (an image cannot be redacted)."""
    llm = _agent(request).llm
    if vision and not vision_enabled(llm):
        raise HTTPException(403, "קריאת תלושים מתמונה כבויה בשרת (PAYSLIP_VISION)")
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
        res = await run_in_threadpool(extract_payslip, bytes(data), llm, _config(request),
                                      vision)
    except PayslipUnreadable as e:  # a decision for the employee, not an error
        return {"fields": [], "not_found": [], "rejected": [], "source": None, "checks": [],
                "unreadable": str(e), "vision_available": vision_enabled(llm)}
    except PayslipError as e:
        raise HTTPException(e.status, str(e)) from None
    return res.as_dict()


@router.post("/payslip/apply", response_model=ChatResponse)
def payslip_apply(body: PayslipApplyRequest, request: Request,
                  claims: Claims = Depends(require_claims)) -> ChatResponse:
    """Save the values the employee confirmed (possibly corrected) and continue the chat
    of the token's conversation."""
    agent = _agent(request)
    session_id = _own_session(claims, body.session_id)
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
