"""Step 4 - HTTP API (FastAPI TestClient): login + signed tokens, employee chat, sample
questions, rep hand-off, security.

Every employee call starts with POST /api/auth/login and sends the token it returns
(Authorization: Bearer ...); the token is bound to one conversation."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agent.rule_based import RuleBasedLLM
from backend.api.auth import LoginThrottle, issue_token
from backend.data_loader.config_loader import get_config
from backend.main import create_app

CONFIG = get_config()

E001_CHAT = [
    "שלום, שכר הבסיס שלי 18,500 ואני מנהלת מוצר",
    "אין לי עמלות או בונוס. אני מפרישה 6% לפנסיה ו-2.5% לקרן השתלמות, ויש לי 2.75 נקודות זיכוי",
    "היום יש לי קורולה קרוס עם השתתפות 300 ₪, ואני שוקלת לעבור לאטו 3",
]
E004_CHAT = [
    "שכר בסיס 16,000 ועמלות 3,000, אני מנהל אזור מכירות",
    "פנסיה 6%, השתלמות 2.5%, 2.25 נקודות זיכוי",
    "היום יש לי טוסון עם השתתפות 400",
]
SPECIAL_PARTICIPATION = "אני רוצה לעבור ל-EV3 אבל סוכמה לי השתתפות מיוחדת ואני לא יודע כמה"

REP_KEY = "test-rep-key-0123456789"
REP = {"X-Rep-Token": REP_KEY}
SECRET = "test-secret-key-0123456789-abcdefghijklmnop"
DEMO_CODE = "1234"  # data/demo_access.json
UNKNOWN_SID = "0" * 32


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "api.db"


def make_app(db_path, llm=None, **kw):
    return create_app(db_path=db_path, llm=llm or RuleBasedLLM(CONFIG),
                      secret_key=SECRET, **kw)


@pytest.fixture
def client(db_path):
    with TestClient(make_app(db_path, rep_token=REP_KEY)) as c:
        yield c


# --------------------------------------------------------------------------- #
# Helpers: every conversation starts with a login
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    sid: str
    headers: dict[str, str]
    login: dict[str, Any]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client, emp_id=None, code=DEMO_CODE) -> Session:
    body = {"guest": True} if emp_id is None else {"emp_id": emp_id, "code": code}
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    return Session(j["session_id"], bearer(j["token"]), j)


def guest(client) -> Session:
    return login(client)


def demo(client, emp_id) -> tuple[Session, dict]:
    """Log in as the sample employee and ask their original question from the sheet."""
    s = login(client, emp_id)
    r = client.post(f"/api/demo/{emp_id}", headers=s.headers)
    assert r.status_code == 200, r.text
    return s, r.json()


def chat(client, s: Session, message):
    r = client.post("/api/chat", json={"message": message}, headers=s.headers)
    assert r.status_code == 200, r.text
    return r.json()


def converse(client, s: Session, messages):
    body = None
    for m in messages:
        body = chat(client, s, m)
    return body


def session(client, s: Session, **params):
    r = client.get(f"/api/session/{s.sid}", headers=s.headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def tickets(client):
    r = client.get("/api/rep/tickets", headers=REP)
    assert r.status_code == 200, r.text
    return r.json()


def rep_answer(client, sid, **body):
    return client.post(f"/api/rep/tickets/{sid}/answer", json=body, headers=REP)


# --------------------------------------------------------------------------- #
# Server startup, CORS, catalogue
# --------------------------------------------------------------------------- #
def test_lifespan_loads_all_data(client):
    h = client.get("/api/health").json()
    assert h == {"status": "ok", "rules_version": CONFIG.rules.version, "deterministic": True}
    tax_db = client.app.state.tax_db
    assert (len(CONFIG.cars), len(CONFIG.occupations), tax_db.count()) == (20, 14, 77_988)


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:3000"])
def test_cors_allows_local_dev_servers_and_the_auth_header(client, origin):
    r = client.options("/api/chat", headers={"Origin": origin,
                                             "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers":
                                                 "content-type, authorization"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


def test_cors_rejects_foreign_origin(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_catalog(client):
    cat = client.get("/api/catalog").json()
    assert len(cat["cars"]) == 20 and len(cat["occupations"]) == 14
    assert len(cat["demo_employees"]) == 6
    # demo accounts for the login screen (fictional people, published code)
    assert {e["demo_code"] for e in cat["demo_employees"]} == {DEMO_CODE}
    assert set(cat["demo_employees"][0]) == {"employee_id", "name", "occupation", "demo_code"}
    atto = next(c for c in cat["cars"] if c["car_id"] == "CAR-10")
    assert atto["value_of_use"] == 3130
    # internal fields are not published
    assert not {"aliases", "list_price", "company_monthly_cost"} & atto.keys()
    assert set(cat["occupations"][0]) == {"name", "eligibility_group"}
    assert [g["group"] for g in cat["groups"]] == ["א", "ב", "ג", "ד"]


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def test_employee_login_preloads_the_record_and_greets_personally(client):
    s = login(client, "E-001")
    j = s.login
    assert re.fullmatch(r"[0-9a-f]{32}", s.sid)
    assert j["kind"] == "employee" and j["employee_name"] == "נועה לוי"
    st = j["state"]
    assert st["display_name"] == "נועה לוי (E-001)"
    p = st["profile"]
    assert (p["occupation"], p["eligibility_group"], p["base_salary"]) == ("מנהל/ת מוצר", "ג", 18500)
    assert (p["pension_pct"], p["hishtalmut_pct"], p["credit_points"]) == (6, 2.5, 2.75)
    assert st["current"] == {"status": "car", "current_car_code": "CAR-09",
                             "current_participation": 300, "current_maintenance": None}
    # only the requested alternative is still missing
    assert st["missing_info"] == ["target_action"]
    assert "נועה" in j["bot_message"] and "COROLLA CROSS" in j["bot_message"]
    assert j["token"].startswith("v1.") and j["token_type"] == "bearer"
    # the greeting is part of the conversation (also after a refresh)
    msgs = session(client, s)["messages"]
    assert [m["role"] for m in msgs] == ["assistant"] and msgs[0]["content"] == j["bot_message"]
    # the next login returns to the same (most recent) conversation
    assert login(client, "E-001").sid == s.sid


def test_employee_login_then_only_the_alternative_is_asked(client):
    s = login(client, "E-001")
    body = chat(client, s, "אני רוצה לעבור לאטו 3")
    ref = CONFIG.reference_calculation("E-001")
    c = body["comparison"]
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff)


def test_employee_without_a_car_is_preloaded_with_the_allowance(client):
    st = login(client, "E-003").login["state"]
    assert st["current"]["status"] == "waiver" and st["current"]["current_maintenance"] == 1500
    assert "אחזקת רכב" in login(client, "E-003").login["bot_message"]


def test_guest_login_opens_an_empty_conversation(client):
    s = guest(client)
    j = s.login
    assert j["kind"] == "guest" and j["employee_name"] is None
    assert j["state"]["profile"]["base_salary"] is None and j["state"]["display_name"] is None
    assert "base_salary" in j["state"]["missing_info"]
    assert j["bot_message"]


@pytest.mark.parametrize("body", [
    {"emp_id": "E-001", "code": "0000"},   # wrong code
    {"emp_id": "E-999", "code": DEMO_CODE},  # unknown employee - same answer
])
def test_login_refuses_wrong_credentials(client, body):
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 401
    assert r.json()["detail"] == "מספר עובד או קוד שגויים"
    assert "token" not in r.text


@pytest.mark.parametrize("body", [
    {},                                                   # neither employee nor guest
    {"guest": True, "emp_id": "E-001", "code": DEMO_CODE},  # both
    {"emp_id": "E-001"},                                  # no code
    {"code": DEMO_CODE},                                  # no employee number
    {"emp_id": "1", "code": DEMO_CODE},                   # bad employee number
    {"guest": True, "role": "admin"},                     # unknown field
])
def test_login_validation(client, body):
    assert client.post("/api/auth/login", json=body).status_code == 422


def test_login_is_locked_after_repeated_wrong_codes(client):
    for _ in range(LoginThrottle.MAX_FAILURES):
        assert client.post("/api/auth/login",
                           json={"emp_id": "E-002", "code": "9999"}).status_code == 401
    # now even the right code is refused for a while
    r = client.post("/api/auth/login", json={"emp_id": "E-002", "code": DEMO_CODE})
    assert r.status_code == 429
    # other employee numbers are not affected
    assert client.post("/api/auth/login",
                       json={"emp_id": "E-001", "code": DEMO_CODE}).status_code == 200


# --------------------------------------------------------------------------- #
# My conversations
# --------------------------------------------------------------------------- #
def conversations(client, s: Session) -> list[dict]:
    r = client.get("/api/conversations", headers=s.headers)
    assert r.status_code == 200, r.text
    return r.json()


def new_conversation(client, s: Session) -> Session:
    r = client.post("/api/conversations", headers=s.headers)
    assert r.status_code == 200, r.text
    j = r.json()
    return Session(j["session_id"], bearer(j["token"]), j)


def test_new_conversation_keeps_the_earlier_ones(client):
    first = login(client, "E-001")
    chat(client, first, "אני רוצה לעבור לאטו 3")
    second = new_conversation(client, first)
    assert second.sid != first.sid
    j = second.login
    assert j["kind"] == "employee" and j["employee_name"] == "נועה לוי"
    # the new conversation also starts with the employee's record
    assert j["state"]["missing_info"] == ["target_action"] and "נועה" in j["bot_message"]
    # the new token is bound to the new conversation only
    assert client.get(f"/api/session/{first.sid}", headers=second.headers).status_code == 403
    rows = conversations(client, second)
    assert [c["session_id"] for c in rows] == [second.sid, first.sid]  # newest first
    assert [c["title"] for c in rows] == ["שיחה חדשה", "מעבר ל-ATTO 3"]
    assert set(rows[0]) == {"session_id", "title", "created_at", "updated_at", "waiting_for_rep"}


def test_open_an_earlier_conversation(client):
    first = login(client, "E-001")
    chat(client, first, "אני רוצה לעבור לאטו 3")
    second = new_conversation(client, first)
    r = client.post(f"/api/conversations/{first.sid}/open", headers=second.headers)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["session_id"] == first.sid and "+661" in j["bot_message"]
    reopened = bearer(j["token"])
    msgs = client.get(f"/api/session/{first.sid}", headers=reopened).json()["messages"]
    assert [m["role"] for m in msgs] == ["assistant", "employee", "assistant"]


def test_login_returns_to_the_most_recent_conversation(client):
    first = login(client, "E-001")
    second = new_conversation(client, first)
    chat(client, second, "אני רוצה לעבור לאטו 3")
    # opening an older conversation does not change which one is the most recent
    client.post(f"/api/conversations/{first.sid}/open", headers=second.headers)
    again = login(client, "E-001")
    assert again.sid == second.sid and "+661" in again.login["bot_message"]
    assert again.login["state"]["last_comparison"]["net_diff"] == 661


def test_another_employees_conversations_are_not_listed_or_opened(client):
    noa = login(client, "E-001")
    david = login(client, "E-002")
    assert [c["session_id"] for c in conversations(client, david)] == [david.sid]
    r = client.post(f"/api/conversations/{noa.sid}/open", headers=david.headers)
    assert r.status_code == 404
    g = guest(client)
    assert client.post(f"/api/conversations/{g.sid}/open",
                       headers=david.headers).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("get", "/api/conversations"),
    ("post", "/api/conversations"),
    ("post", f"/api/conversations/{UNKNOWN_SID}/open"),
])
def test_conversations_are_for_logged_in_employees_only(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    r = getattr(client, method)(path, headers=guest(client).headers)
    assert r.status_code == 403
    assert r.json()["detail"] == "רק עובד שנכנס עם מספר עובד וקוד יכול לשמור כמה שיחות"


def test_conversations_of_an_older_database_are_kept(db_path):
    """A database from before conversations had an owner gets the column on startup."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, "
                 "state_json TEXT NOT NULL, waiting_for_rep INTEGER NOT NULL, "
                 "updated_at TEXT NOT NULL)")
    conn.commit()
    conn.close()
    with TestClient(make_app(db_path)) as c:
        s = login(c, "E-001")
        assert [r["session_id"] for r in conversations(c, s)] == [s.sid]


# --------------------------------------------------------------------------- #
# Employee chat
# --------------------------------------------------------------------------- #
def test_full_chat_cycle(client):
    s = guest(client)
    first = chat(client, s, E001_CHAT[0])
    assert first["session_id"] == s.sid
    assert first["comparison"] is None and not first["frozen"]
    assert first["state"]["profile"]["base_salary"] == 18500
    assert first["state"]["profile"]["eligibility_group"] == "ג"
    assert "pension_pct" in first["state"]["missing_info"]

    second = chat(client, s, E001_CHAT[1])
    assert second["session_id"] == s.sid
    assert second["state"]["profile"]["credit_points"] == 2.75

    last = chat(client, s, E001_CHAT[2])
    ref = CONFIG.reference_calculation("E-001")
    c = last["comparison"]
    assert "tools_called" not in last
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff)
    assert c["explanation"] == ref.explanation
    assert last["state"]["missing_info"] == []
    assert "+661" in last["bot_message"]


def test_chat_with_explicit_own_session_id(client):
    s = guest(client)
    r = client.post("/api/chat", json={"session_id": s.sid, "message": E001_CHAT[0]},
                    headers=s.headers)
    assert r.status_code == 200 and r.json()["session_id"] == s.sid


def test_session_history_survives_refresh_and_restart(db_path, client):
    s = guest(client)
    converse(client, s, E001_CHAT)
    # a brand-new server process on the same database and key (browser refresh / restart)
    with TestClient(make_app(db_path)) as fresh:
        r = fresh.get(f"/api/session/{s.sid}", headers=s.headers)
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["assistant"] + ["employee", "assistant"] * 3
    assert msgs[1]["content"] == E001_CHAT[0]
    assert r.json()["state"]["last_comparison"]["net_diff"] == 661
    # internal tool messages are never returned, even when asked for
    with_tools = session(client, s, include_tools=True)
    assert all(set(m) == {"role", "content", "created_at"} for m in with_tools["messages"])
    assert all(m["role"] != "tool" for m in with_tools["messages"])


class FailingProviderLLM:
    """Fails the way Gemini did: 402 prepay credits depleted (or another status)."""

    def __init__(self, status=402):
        self.status = status
        self.model = "gemini-3.8-flash"

    def step(self, **kw):
        from backend.agent.llm import LLMError
        raise LLMError(f"LLM HTTP {self.status}: Your prepayment credits are depleted.",
                       status=self.status)


def test_llm_failure_is_503_with_real_cause_and_cors(db_path, monkeypatch):
    """Default: no model-less answers - the employee gets the real reason."""
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    with TestClient(make_app(db_path, llm=FailingProviderLLM(402))) as c:
        s = guest(c)  # logging in needs no model
        r = c.post("/api/chat", json={"message": "שלום"},
                   headers={**s.headers, "Origin": "http://localhost:5173"})
    assert r.status_code == 503
    body = r.json()
    assert "קרדיט" in body["detail"] and "provider_status" not in body
    # readable by the browser (an unhandled 500 would have no CORS header)
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_llm_check_endpoint_reports_cause(db_path):
    with TestClient(make_app(db_path, llm=FailingProviderLLM(429), rep_token=REP_KEY)) as c:
        assert c.get("/api/llm/check").status_code == 401  # paid call: rep key only
        r = c.get("/api/llm/check", headers=REP).json()
    assert r["ok"] is False and r["model"] == "gemini-3.8-flash"
    [p] = r["providers"]
    assert p["provider_status"] == 429 and "מכסת" in p["message"]
    assert "api_key" not in str(r) and "base_url" not in str(r)


def test_llm_check_endpoint_checks_every_provider_in_the_chain(db_path):
    from backend.agent.llm import FailoverLLM
    chain = FailoverLLM([FailingProviderLLM(429), RuleBasedLLM(CONFIG)])
    with TestClient(make_app(db_path, llm=chain, rep_token=REP_KEY)) as c:
        r = c.get("/api/llm/check", headers=REP).json()
    assert r["ok"] is True  # the backup works
    assert [p["ok"] for p in r["providers"]] == [False, True]
    assert r["providers"][0]["provider_status"] == 429


def test_opt_in_fallback_answers_deterministically(db_path, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK", "rules")
    with TestClient(make_app(db_path, llm=FailingProviderLLM(429))) as c:
        s = guest(c)
        r = c.post("/api/chat", json={"message": "שכר בסיס 14,200 קבוצה ג'"}, headers=s.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["llm_fallback"] is True and body["notice"]
    assert body["state"]["profile"]["base_salary"] == 14200


def test_chat_errors(client):
    s = guest(client)
    assert client.post("/api/chat", json={"message": ""}, headers=s.headers).status_code == 422
    assert client.post("/api/chat", json={"session_id": "bad id!", "message": "x"},
                       headers=s.headers).status_code == 422
    # ids are server-generated uuid4 hex: client-made ids are rejected
    assert client.post("/api/chat", json={"session_id": "demo-1", "message": "x"},
                       headers=s.headers).status_code == 422
    assert client.get("/api/session/bad-id", headers=s.headers).status_code == 422


def test_token_for_a_deleted_conversation_is_404(client):
    s = guest(client)
    token = issue_token(SECRET, UNKNOWN_SID, "guest")  # validly signed, no such conversation
    assert client.post("/api/chat", json={"message": "x"}, headers=bearer(token)).status_code == 404
    assert client.get(f"/api/session/{UNKNOWN_SID}", headers=bearer(token)).status_code == 404
    assert session(client, s)["session_id"] == s.sid


# --------------------------------------------------------------------------- #
# Sample employees: their original question from the challenge sheet
# --------------------------------------------------------------------------- #
def test_load_demo_e001(client):
    s, body = demo(client, "E-001")
    ref = CONFIG.reference_calculation("E-001")
    c = body["comparison"]
    assert body["session_id"] == s.sid  # the logged-in conversation, not a new one
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (11032, 11693, 661)
    assert (c["tax_today"], c["tax_alt"], c["electric_gross"]) == (
        ref.tax_current, ref.tax_alternative, ref.electric_refund_gross)
    assert c["current_label"] == "COROLLA CROSS (2025)" and c["alternative_label"] == "ATTO 3 (2025)"
    st = body["state"]
    assert st["profile"]["occupation"] == "מנהל/ת מוצר" and st["missing_info"] == []
    msgs = session(client, s)["messages"]
    assert msgs[0]["role"] == "assistant"  # the login greeting
    assert msgs[1]["content"] == CONFIG.employee("E-001").question


@pytest.mark.parametrize("ref", CONFIG.reference_calculations, ids=lambda r: r.employee_id)
def test_load_demo_all_employees_match_reference(client, ref):
    c = demo(client, ref.employee_id)[1]["comparison"]
    assert (c["net_today"], c["net_alt"], c["net_diff"], c["family_gap_deduction"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff, ref.family_track_employee_share)


def test_load_demo_then_continue_chatting(client):
    s, _ = demo(client, "E-004")
    body = chat(client, s, "ואם אני אעבור לקורולה?")
    assert body["state"]["target"]["target_car_code"] == "CAR-07"
    assert body["comparison"]["alternative_label"] == "COROLLA (2026)"


def test_comparison_has_line_by_line_breakdown(client):
    c = demo(client, "E-001")[1]["comparison"]
    t, a = c["today"], c["alternative"]
    assert (t["label"], t["value_of_use"], t["participation"], t["electric_gross"]) == (
        "COROLLA CROSS (2025)", 4070, 300, 0)
    assert (a["label"], a["value_of_use"], a["is_electric"], a["electric_gross"]) == (
        "ATTO 3 (2025)", 3130, True, 440)
    assert (t["net"], a["net"]) == (c["net_today"], c["net_alt"])
    assert (t["income_tax"], a["btl_health"]) == (c["tax_today"], c["btl_health_alt"])
    waiver = demo(client, "E-005")[1]["comparison"]["alternative"]
    assert (waiver["kind"], waiver["maintenance_allowance"], waiver["value_of_use"]) == (
        "waiver", 2000, 0)
    family = demo(client, "E-002")[1]["comparison"]["alternative"]
    assert family["family_gap_deduction"] == 1150


def test_options_ranking_and_quick_switch(client):
    s = login(client, "E-001")  # group ג, 2 children, may waive
    opts = client.get(f"/api/session/{s.sid}/options", headers=s.headers).json()
    ids = {c["car_id"] for c in opts["allowed_cars"]}
    assert "CAR-13" in ids and "CAR-14" not in ids          # ג yes, ד no
    assert opts["family_track"]["eligible"] is False
    assert opts["waiver"]["allowed"] and opts["waiver"]["maintenance"] == 2000

    # all my options: right after login - no alternative asked yet
    rank = client.get(f"/api/session/{s.sid}/ranking", headers=s.headers).json()
    assert rank["ok"] and rank["net_today"] == 11032
    assert rank["best"]["kind"] == "waiver" and rank["best"]["net_diff"] == 3199
    atto = next(o for o in rank["options"] if o["car_id"] == "CAR-10")
    assert atto["net_diff"] == 661

    r = client.post(f"/api/session/{s.sid}/alternative", json={"car_id": "CAR-13"},
                    headers=s.headers).json()
    assert r["state"]["target"]["target_car_code"] == "CAR-13"
    assert r["comparison"]["alternative"]["label"] == "MAZDA 3 (2025)"
    assert r["comparison"]["net_today"] == 11032       # today unchanged
    w = client.post(f"/api/session/{s.sid}/alternative", json={"waiver": True},
                    headers=s.headers).json()
    assert w["comparison"]["alternative"]["kind"] == "waiver"
    msgs = session(client, s)["messages"]
    assert msgs[-2]["content"] == "בדיקת חלופה: ויתור על הרכב"


def test_quick_switch_to_disallowed_car_shows_reason(client):
    s = login(client, "E-001")
    r = client.post(f"/api/session/{s.sid}/alternative", json={"car_id": "CAR-14"},
                    headers=s.headers).json()
    assert r["comparison"]["is_eligible"] is False and r["comparison"]["alternative"] is None
    assert "מעל הקבוצה שלך" in r["bot_message"]


def test_quick_switch_errors(client):
    s = login(client, "E-001")
    url = f"/api/session/{s.sid}/alternative"
    assert client.post(url, json={}, headers=s.headers).status_code == 422
    assert client.post(url, json={"car_id": "CAR-01", "waiver": True},
                       headers=s.headers).status_code == 422
    assert client.post(url, json={"car_id": "CAR-99"}, headers=s.headers).status_code == 404


def test_new_car_resets_rep_approved_participation(client):
    s = guest(client)
    converse(client, s, [*E004_CHAT, SPECIAL_PARTICIPATION])
    rep_answer(client, s.sid, answer_text="350")
    r = client.post(f"/api/session/{s.sid}/alternative", json={"car_id": "CAR-09"},
                    headers=s.headers).json()
    assert r["state"]["target"]["target_participation"] is None   # 350 was for EV3 only
    assert r["comparison"]["alternative"]["participation"] == 300  # COROLLA CROSS default


def test_ticket_shows_the_logged_in_employee_name(client):
    s = login(client, "E-004")
    chat(client, s, SPECIAL_PARTICIPATION)
    t = tickets(client)[0]
    assert t["employee_name"] == "יוסי מזרחי (E-004)"
    assert t["profile"]["base_salary"] == 16000  # from the preloaded record


def test_load_demo_errors(client):
    s = login(client, "E-001")
    assert client.post("/api/demo/E-999", headers=s.headers).status_code == 404
    assert client.post("/api/demo/bad", headers=s.headers).status_code == 422
    # only the logged-in employee's own sample question
    assert client.post("/api/demo/E-002", headers=s.headers).status_code == 403
    assert client.post("/api/demo/E-001", headers=guest(client).headers).status_code == 403
    # the old route (client-chosen session id) no longer exists
    assert client.post(f"/api/session/{UNKNOWN_SID}/load_demo/E-001").status_code == 404


# --------------------------------------------------------------------------- #
# Representative (human in the loop)
# --------------------------------------------------------------------------- #
def test_rep_flow_freeze_ticket_answer(client):
    s = guest(client)
    converse(client, s, E004_CHAT)
    assert tickets(client) == []

    frozen = chat(client, s, SPECIAL_PARTICIPATION)
    assert frozen["frozen"] and frozen["state"]["waiting_for_rep"]
    assert frozen["comparison"] is None
    # in the meantime: today's net (engine), the alternative follows the answer
    ref = CONFIG.reference_calculation("E-004")
    preview = frozen["state"]["today_preview"]
    assert preview["today"]["net"] == ref.net_current and preview["alternative_label"] == "EV3"
    assert frozen["state"]["last_comparison"] is None
    assert "בינתיים, הנטו שלך היום" in frozen["bot_message"] and "10,693" in frozen["bot_message"]

    ts = tickets(client)
    assert len(ts) == 1
    t = ts[0]
    assert t["session_id"] == s.sid and "EV3" in t["rep_question"]
    assert t["rep_field"] == "target_participation"
    assert t["target_car"]["car_id"] == "CAR-11" and t["current_car"]["model_name"] == "TUCSON"
    assert t["profile"]["base_salary"] == 16000
    assert t["last_employee_message"] == SPECIAL_PARTICIPATION

    still = chat(client, s, "יש תשובה?")  # employee writes while frozen
    assert still["frozen"] and "10,693" in still["bot_message"]

    ans = rep_answer(client, s.sid, answer_text="ההשתתפות שאושרה היא 350 ₪")
    assert ans.status_code == 200, ans.text
    a = ans.json()
    assert a["ok"] and not a["waiting_for_rep"] and "state" not in a  # no full state to the rep
    assert a["employee_name"] is None  # guest, not an identified employee
    st = session(client, s)["state"]
    assert st["target"]["target_participation"] == 350
    assert st["today_preview"] is None  # the full comparison replaced it
    assert a["comparison"]["net_today"] == ref.net_current
    assert a["comparison"]["net_alt"] == ref.net_alternative - 50  # 350 instead of 300
    assert "11,850" in a["bot_message"]
    assert tickets(client) == []

    roles = [m["role"] for m in session(client, s)["messages"]]
    assert "representative" in roles


def test_agreed_otherwise_withdraws_the_catalogue_result_until_the_rep_answers(client):
    s = login(client, "E-001")
    first = chat(client, s, "אני רוצה לעבור לאטו 3")
    assert first["comparison"]["alternative_participation_source"] == "company_catalogue_default"
    assert "לפי קטלוג הרכבים של החברה: 300 ₪" in first["bot_message"]

    frozen = chat(client, s, "אצלי סוכם אחרת ואני לא יודעת כמה")
    st = frozen["state"]
    assert frozen["frozen"] and st["missing_info"] == ["target_participation"]
    # the +661 computed with the catalogue amount is not shown as a result any more
    assert st["last_comparison"] is None and st["today_preview"]["today"]["net"] == 11032

    a = rep_answer(client, s.sid, answer_text="השתתפות 350 ₪, בתוקף מ-1/1").json()
    assert a["comparison"]["net_alt"] == 11693 - 50
    assert a["comparison"]["alternative_participation_source"] == "approved_by_representative"
    assert "כפי שאישר/ה הנציג/ה" in a["bot_message"]


def test_rep_answer_with_explicit_participation_field(client):
    s = guest(client)
    converse(client, s, [*E004_CHAT, SPECIAL_PARTICIPATION])
    a = rep_answer(client, s.sid, answer_text="אושר", participation=300).json()
    assert a["comparison"]["net_alt"] == CONFIG.reference_calculation("E-004").net_alternative


def test_rep_answer_errors(client):
    s = guest(client)
    converse(client, s, E004_CHAT)
    r = rep_answer(client, s.sid, answer_text="350")
    assert r.status_code == 409  # not waiting
    chat(client, s, SPECIAL_PARTICIPATION)
    r = rep_answer(client, s.sid, answer_text="צריך לבדוק")
    assert r.status_code == 422  # no number -> conversation stays frozen
    assert session(client, s)["state"]["waiting_for_rep"]
    assert rep_answer(client, UNKNOWN_SID, answer_text="1").status_code == 404
    assert rep_answer(client, s.sid, answer_text="x", participation=-5).status_code == 422


# --------------------------------------------------------------------------- #
# Security: conversations are readable only with their own signed token
# --------------------------------------------------------------------------- #
def _session_routes(sid):
    """Every conversation route, with a valid body - only the token is wrong."""
    return [
        ("get", f"/api/session/{sid}", None),
        ("get", f"/api/session/{sid}/options", None),
        ("get", f"/api/session/{sid}/ranking", None),
        ("post", f"/api/session/{sid}/alternative", {"waiver": True}),
        ("post", "/api/chat", {"session_id": sid, "message": "שלום"}),
        ("post", "/api/payslip/apply", {"session_id": sid, "values": {"base_salary": 9000}}),
    ]


@pytest.mark.parametrize("bad_auth", [
    {},                                                  # no token
    {"Authorization": "Bearer "},                        # empty
    {"Authorization": "Bearer not-a-token"},             # garbage
    {"Authorization": "Basic dXNlcjpwYXNz"},             # another scheme
], ids=["missing", "empty", "garbage", "basic"])
def test_session_routes_reject_requests_without_a_valid_token(client, bad_auth):
    s = login(client, "E-001")
    for method, url, body in _session_routes(s.sid):
        r = getattr(client, method)(url, json=body, headers=bad_auth) if body else \
            getattr(client, method)(url, headers=bad_auth)
        assert r.status_code == 401, (url, r.status_code)
        assert "salary" not in r.text and "18500" not in r.text
    assert client.post("/api/payslip/extract", content=b"%PDF-1.4",
                       headers={"Content-Type": "application/pdf", **bad_auth}).status_code == 401
    # nothing was changed in the conversation
    st = session(client, s)["state"]
    assert st["target"]["target_action"] is None and len(session(client, s)["messages"]) == 1


def test_another_users_token_cannot_read_or_change_a_conversation(client):
    victim = login(client, "E-001")
    attacker = guest(client)
    for method, url, body in _session_routes(victim.sid):
        r = getattr(client, method)(url, json=body, headers=attacker.headers) if body else \
            getattr(client, method)(url, headers=attacker.headers)
        assert r.status_code == 403, (url, r.status_code)
        assert "18,500" not in r.text and "18500" not in r.text
    assert session(client, victim)["state"]["target"]["target_action"] is None


def test_tampered_forged_or_expired_tokens_are_rejected(client):
    victim = login(client, "E-001")
    attacker = guest(client)
    url = f"/api/session/{victim.sid}"
    version, body, sig = attacker.headers["Authorization"].split()[1].split(".")

    # 1) the attacker's own payload, edited to point at the victim's session
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["sid"] = victim.sid
    edited = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    assert client.get(url, headers=bearer(f"{version}.{edited}.{sig}")).status_code == 401
    # 2) a token for the victim signed with another key
    forged = issue_token("some-other-secret-key-0123456789abcdef", victim.sid, "employee", "E-001")
    assert client.get(url, headers=bearer(forged)).status_code == 401
    # 3) a correctly signed but expired token
    expired = issue_token(SECRET, victim.sid, "employee", "E-001", ttl_s=-1)
    assert client.get(url, headers=bearer(expired)).status_code == 401
    # the real token still works
    assert client.get(url, headers=victim.headers).status_code == 200


def test_tokens_from_a_server_with_another_key_are_rejected(db_path, client):
    s = login(client, "E-001")
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG),
                               secret_key="a-completely-different-key-0123456789")) as other:
        assert other.get(f"/api/session/{s.sid}", headers=s.headers).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-Rep-Token": "wrong"}, {"X-Rep-Token": ""}])
def test_rep_endpoints_require_the_rep_key(client, headers):
    s = guest(client)
    converse(client, s, [*E004_CHAT, SPECIAL_PARTICIPATION])
    assert client.get("/api/rep/tickets", headers=headers).status_code == 401
    r = client.post(f"/api/rep/tickets/{s.sid}/answer", json={"answer_text": "0"}, headers=headers)
    assert r.status_code == 401
    assert session(client, s)["state"]["waiting_for_rep"]  # untouched


def test_an_employee_token_is_not_a_rep_key(client):
    s = login(client, "E-001")
    assert client.get("/api/rep/tickets", headers=s.headers).status_code == 401


def test_rep_screen_closed_when_no_key_configured(db_path, monkeypatch):
    monkeypatch.setenv("REP_API_KEY", "")
    with TestClient(make_app(db_path)) as c:
        assert c.get("/api/rep/tickets").status_code == 503
        assert c.get("/api/rep/tickets", headers={"X-Rep-Token": ""}).status_code == 503
        assert c.get("/api/llm/check").status_code == 503


def test_rep_key_from_environment(db_path, monkeypatch):
    monkeypatch.setenv("REP_API_KEY", "env-rep-key-abcdefghij")
    with TestClient(make_app(db_path)) as c:
        ok = c.get("/api/rep/tickets", headers={"X-Rep-Token": "env-rep-key-abcdefghij"})
        assert ok.status_code == 200


def test_secret_key_from_environment(db_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "env-secret-key-0123456789-abcdefghijkl")
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG))) as c:
        s = login(c, "E-001")
    token = s.headers["Authorization"].split()[1]
    # the same key signs the same way: a second process accepts the token
    assert token == issue_token("env-secret-key-0123456789-abcdefghijkl", s.sid, "employee",
                                "E-001", now=s.login["expires_at"] - 12 * 3600)


def test_precheck_internal_instruction_is_not_exposed(client):
    s = guest(client)
    body = chat(client, s, "אני מפתח תוכנה ורוצה לקחת טסלה מודל 3")
    pre = body["state"]["precheck"]
    assert pre["eligible"] is False and pre["alternatives"]
    assert "instruction" not in pre
    assert "instruction" not in session(client, s)["state"]["precheck"]


def test_security_headers(client):
    s = guest(client)
    r = client.post("/api/chat", json={"message": "שלום"}, headers=s.headers)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_swagger_shows_session_id_as_optional_and_bearer_auth(client):
    schema = client.get("/openapi.json").json()
    assert schema["components"]["schemas"]["ChatRequest"]["required"] == ["message"]
    examples = (schema["paths"]["/api/chat"]["post"]["requestBody"]["content"]
                ["application/json"]["examples"])
    assert examples["first"]["value"] == {"message": "אני מנהל אזור מכירות, שכר בסיס 16,000"}
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/chat"]["post"]["security"] == [{"HTTPBearer": []}]


def test_docs_can_be_disabled(db_path, monkeypatch):
    monkeypatch.setenv("API_DOCS", "0")
    with TestClient(make_app(db_path)) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
