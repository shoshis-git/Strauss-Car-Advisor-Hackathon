"""Step 4 - HTTP API (FastAPI TestClient): employee chat, demo loader, rep hand-off."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from backend.agent.rule_based import RuleBasedLLM
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


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "api.db"


REP_KEY = "test-rep-key-0123456789"
REP = {"X-Rep-Token": REP_KEY}
UNKNOWN_SID = "0" * 32


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG),
                               rep_token=REP_KEY)) as c:
        yield c


def demo(client, emp_id):
    """Load a demo employee into a NEW server-generated session."""
    r = client.post(f"/api/demo/{emp_id}")
    assert r.status_code == 200, r.text
    return r.json()


def tickets(client):
    r = client.get("/api/rep/tickets", headers=REP)
    assert r.status_code == 200, r.text
    return r.json()


def rep_answer(client, sid, **body):
    return client.post(f"/api/rep/tickets/{sid}/answer", json=body, headers=REP)


def chat(client, message, session_id=None):
    r = client.post("/api/chat", json={"session_id": session_id, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


def converse(client, messages, session_id=None):
    body = None
    for m in messages:
        body = chat(client, m, session_id)
        session_id = body["session_id"]
    return body


# --------------------------------------------------------------------------- #
# Server startup, CORS, catalogue
# --------------------------------------------------------------------------- #
def test_lifespan_loads_all_data(client):
    h = client.get("/api/health").json()
    assert h == {"status": "ok", "rules_version": CONFIG.rules.version, "deterministic": True}
    tax_db = client.app.state.tax_db
    assert (len(CONFIG.cars), len(CONFIG.occupations), tax_db.count()) == (20, 14, 77_988)


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:3000"])
def test_cors_allows_local_dev_servers(client, origin):
    r = client.options("/api/chat", headers={"Origin": origin,
                                             "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


def test_cors_rejects_foreign_origin(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_catalog(client):
    cat = client.get("/api/catalog").json()
    assert len(cat["cars"]) == 20 and len(cat["occupations"]) == 14
    assert len(cat["demo_employees"]) == 6
    atto = next(c for c in cat["cars"] if c["car_id"] == "CAR-10")
    assert atto["value_of_use"] == 3130
    # internal fields are not published
    assert not {"aliases", "list_price", "company_monthly_cost"} & atto.keys()
    assert set(cat["occupations"][0]) == {"name", "eligibility_group"}
    assert [g["group"] for g in cat["groups"]] == ["א", "ב", "ג", "ד"]


# --------------------------------------------------------------------------- #
# Employee chat
# --------------------------------------------------------------------------- #
def test_full_chat_cycle(client):
    first = chat(client, E001_CHAT[0])
    sid = first["session_id"]
    assert sid and first["comparison"] is None and not first["frozen"]
    assert first["state"]["profile"]["base_salary"] == 18500
    assert first["state"]["profile"]["eligibility_group"] == "ג"
    assert "pension_pct" in first["state"]["missing_info"]

    second = chat(client, E001_CHAT[1], sid)
    assert second["session_id"] == sid
    assert second["state"]["profile"]["credit_points"] == 2.75

    last = chat(client, E001_CHAT[2], sid)
    ref = CONFIG.reference_calculation("E-001")
    c = last["comparison"]
    assert "tools_called" not in last
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff)
    assert c["explanation"] == ref.explanation
    assert last["state"]["missing_info"] == []
    assert "+661" in last["bot_message"]


def test_session_history_survives_refresh_and_restart(db_path, client):
    sid = converse(client, E001_CHAT)["session_id"]
    # a brand-new server process on the same database (browser refresh / restart)
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG))) as fresh:
        s = fresh.get(f"/api/session/{sid}").json()
    assert [m["role"] for m in s["messages"]] == ["employee", "assistant"] * 3
    assert s["messages"][0]["content"] == E001_CHAT[0]
    assert s["state"]["last_comparison"]["net_diff"] == 661
    # internal tool messages are never returned, even when asked for
    with_tools = client.get(f"/api/session/{sid}", params={"include_tools": True}).json()
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
    with TestClient(create_app(db_path=db_path, llm=FailingProviderLLM(402))) as c:
        r = c.post("/api/chat", json={"message": "שלום"},
                   headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 503
    body = r.json()
    assert "קרדיט" in body["detail"] and "provider_status" not in body
    # readable by the browser (an unhandled 500 would have no CORS header)
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_llm_check_endpoint_reports_cause(db_path):
    with TestClient(create_app(db_path=db_path, llm=FailingProviderLLM(429),
                               rep_token=REP_KEY)) as c:
        assert c.get("/api/llm/check").status_code == 401  # paid call: rep key only
        r = c.get("/api/llm/check", headers=REP).json()
    assert r["ok"] is False and r["model"] == "gemini-3.8-flash"
    [p] = r["providers"]
    assert p["provider_status"] == 429 and "מכסת" in p["message"]
    assert "api_key" not in str(r) and "base_url" not in str(r)


def test_llm_check_endpoint_checks_every_provider_in_the_chain(db_path):
    from backend.agent.llm import FailoverLLM
    chain = FailoverLLM([FailingProviderLLM(429), RuleBasedLLM(CONFIG)])
    with TestClient(create_app(db_path=db_path, llm=chain, rep_token=REP_KEY)) as c:
        r = c.get("/api/llm/check", headers=REP).json()
    assert r["ok"] is True  # the backup works
    assert [p["ok"] for p in r["providers"]] == [False, True]
    assert r["providers"][0]["provider_status"] == 429


def test_opt_in_fallback_answers_deterministically(db_path, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK", "rules")
    with TestClient(create_app(db_path=db_path, llm=FailingProviderLLM(429))) as c:
        r = c.post("/api/chat", json={"message": "שכר בסיס 14,200 קבוצה ג'"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_fallback"] is True and body["notice"]
    assert body["state"]["profile"]["base_salary"] == 14200


def test_chat_errors(client):
    assert client.post("/api/chat", json={"session_id": UNKNOWN_SID, "message": "x"}
                       ).status_code == 404
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"session_id": "bad id!", "message": "x"}).status_code == 422
    # ids are server-generated uuid4 hex: client-made ids are rejected
    assert client.post("/api/chat", json={"session_id": "demo-1", "message": "x"}).status_code == 422
    assert client.get(f"/api/session/{UNKNOWN_SID}").status_code == 404


# --------------------------------------------------------------------------- #
# Demo employees
# --------------------------------------------------------------------------- #
def test_load_demo_e001(client):
    body = demo(client, "E-001")
    ref = CONFIG.reference_calculation("E-001")
    c = body["comparison"]
    sid = body["session_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", sid)
    assert demo(client, "E-001")["session_id"] != sid  # always a new conversation
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (11032, 11693, 661)
    assert (c["tax_today"], c["tax_alt"], c["electric_gross"]) == (
        ref.tax_current, ref.tax_alternative, ref.electric_refund_gross)
    assert c["current_label"] == "COROLLA CROSS (2025)" and c["alternative_label"] == "ATTO 3 (2025)"
    st = body["state"]
    assert st["profile"]["occupation"] == "מנהל/ת מוצר" and st["missing_info"] == []
    msgs = client.get(f"/api/session/{sid}").json()["messages"]
    assert msgs[0]["content"] == CONFIG.employee("E-001").question


@pytest.mark.parametrize("ref", CONFIG.reference_calculations, ids=lambda r: r.employee_id)
def test_load_demo_all_employees_match_reference(client, ref):
    c = demo(client, ref.employee_id)["comparison"]
    assert (c["net_today"], c["net_alt"], c["net_diff"], c["family_gap_deduction"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff, ref.family_track_employee_share)


def test_load_demo_then_continue_chatting(client):
    sid = demo(client, "E-004")["session_id"]
    body = chat(client, "ואם אני אעבור לקורולה?", sid)
    assert body["state"]["target"]["target_car_code"] == "CAR-07"
    assert body["comparison"]["alternative_label"] == "COROLLA (2026)"


def test_comparison_has_line_by_line_breakdown(client):
    c = demo(client, "E-001")["comparison"]
    t, a = c["today"], c["alternative"]
    assert (t["label"], t["value_of_use"], t["participation"], t["electric_gross"]) == (
        "COROLLA CROSS (2025)", 4070, 300, 0)
    assert (a["label"], a["value_of_use"], a["is_electric"], a["electric_gross"]) == (
        "ATTO 3 (2025)", 3130, True, 440)
    assert (t["net"], a["net"]) == (c["net_today"], c["net_alt"])
    assert (t["income_tax"], a["btl_health"]) == (c["tax_today"], c["btl_health_alt"])
    waiver = demo(client, "E-005")["comparison"]["alternative"]
    assert (waiver["kind"], waiver["maintenance_allowance"], waiver["value_of_use"]) == (
        "waiver", 2000, 0)
    family = demo(client, "E-002")["comparison"]["alternative"]
    assert family["family_gap_deduction"] == 1150


def test_options_and_quick_switch(client):
    sid = demo(client, "E-001")["session_id"]  # group ג, 2 children, may waive
    opts = client.get(f"/api/session/{sid}/options").json()
    ids = {c["car_id"] for c in opts["allowed_cars"]}
    assert "CAR-13" in ids and "CAR-14" not in ids          # ג yes, ד no
    assert opts["family_track"]["eligible"] is False
    assert opts["waiver"]["allowed"] and opts["waiver"]["maintenance"] == 2000

    r = client.post(f"/api/session/{sid}/alternative", json={"car_id": "CAR-13"}).json()
    assert r["state"]["target"]["target_car_code"] == "CAR-13"
    assert r["comparison"]["alternative"]["label"] == "MAZDA 3 (2025)"
    assert r["comparison"]["net_today"] == 11032       # today unchanged
    w = client.post(f"/api/session/{sid}/alternative", json={"waiver": True}).json()
    assert w["comparison"]["alternative"]["kind"] == "waiver"
    msgs = client.get(f"/api/session/{sid}").json()["messages"]
    assert msgs[-2]["content"] == "בדיקת חלופה: ויתור על הרכב"


def test_quick_switch_to_disallowed_car_shows_reason(client):
    sid = demo(client, "E-001")["session_id"]
    r = client.post(f"/api/session/{sid}/alternative", json={"car_id": "CAR-14"}).json()
    assert r["comparison"]["is_eligible"] is False and r["comparison"]["alternative"] is None
    assert "מעל הקבוצה שלך" in r["bot_message"]


def test_quick_switch_errors(client):
    sid = demo(client, "E-001")["session_id"]
    assert client.post(f"/api/session/{sid}/alternative", json={}).status_code == 422
    assert client.post(f"/api/session/{sid}/alternative",
                       json={"car_id": "CAR-01", "waiver": True}).status_code == 422
    assert client.post(f"/api/session/{sid}/alternative",
                       json={"car_id": "CAR-99"}).status_code == 404
    assert client.post(f"/api/session/{UNKNOWN_SID}/alternative",
                       json={"waiver": True}).status_code == 404


def test_new_car_resets_rep_approved_participation(client):
    sid = converse(client, [*E004_CHAT, SPECIAL_PARTICIPATION])["session_id"]
    rep_answer(client, sid, answer_text="350")
    r = client.post(f"/api/session/{sid}/alternative", json={"car_id": "CAR-09"}).json()
    assert r["state"]["target"]["target_participation"] is None   # 350 was for EV3 only
    assert r["comparison"]["alternative"]["participation"] == 300  # COROLLA CROSS default


def test_ticket_shows_demo_employee_name(client):
    sid = demo(client, "E-004")["session_id"]
    chat(client, SPECIAL_PARTICIPATION, sid)
    t = tickets(client)[0]
    assert t["employee_name"] == "יוסי מזרחי (E-004)"


def test_load_demo_unknown_employee(client):
    assert client.post("/api/demo/E-999").status_code == 404
    assert client.post("/api/demo/bad").status_code == 422
    # the old route (client-chosen session id) no longer exists
    assert client.post(f"/api/session/{UNKNOWN_SID}/load_demo/E-001").status_code == 404


# --------------------------------------------------------------------------- #
# Representative (human in the loop)
# --------------------------------------------------------------------------- #
def test_rep_flow_freeze_ticket_answer(client):
    sid = converse(client, E004_CHAT)["session_id"]
    assert tickets(client) == []

    frozen = chat(client, SPECIAL_PARTICIPATION, sid)
    assert frozen["frozen"] and frozen["state"]["waiting_for_rep"]
    assert frozen["comparison"] is None

    ts = tickets(client)
    assert len(ts) == 1
    t = ts[0]
    assert t["session_id"] == sid and "EV3" in t["rep_question"]
    assert t["rep_field"] == "target_participation"
    assert t["target_car"]["car_id"] == "CAR-11" and t["current_car"]["model_name"] == "TUCSON"
    assert t["profile"]["base_salary"] == 16000
    assert t["last_employee_message"] == SPECIAL_PARTICIPATION

    still = chat(client, "יש תשובה?", sid)  # employee writes while frozen
    assert still["frozen"]

    ans = rep_answer(client, sid, answer_text="ההשתתפות שאושרה היא 350 ₪")
    assert ans.status_code == 200, ans.text
    a = ans.json()
    ref = CONFIG.reference_calculation("E-004")
    assert a["ok"] and not a["waiting_for_rep"] and "state" not in a  # no full state to the rep
    assert a["employee_name"] is None  # free chat, not a demo employee
    st = client.get(f"/api/session/{sid}").json()["state"]
    assert st["target"]["target_participation"] == 350
    assert a["comparison"]["net_today"] == ref.net_current
    assert a["comparison"]["net_alt"] == ref.net_alternative - 50  # 350 instead of 300
    assert "11,850" in a["bot_message"]
    assert tickets(client) == []

    roles = [m["role"] for m in client.get(f"/api/session/{sid}").json()["messages"]]
    assert "representative" in roles


def test_rep_answer_with_explicit_participation_field(client):
    sid = converse(client, [*E004_CHAT, SPECIAL_PARTICIPATION])["session_id"]
    a = rep_answer(client, sid, answer_text="אושר", participation=300).json()
    assert a["comparison"]["net_alt"] == CONFIG.reference_calculation("E-004").net_alternative


def test_rep_answer_errors(client):
    sid = converse(client, E004_CHAT)["session_id"]
    r = rep_answer(client, sid, answer_text="350")
    assert r.status_code == 409  # not waiting
    chat(client, SPECIAL_PARTICIPATION, sid)
    r = rep_answer(client, sid, answer_text="צריך לבדוק")
    assert r.status_code == 422  # no number -> conversation stays frozen
    assert client.get(f"/api/session/{sid}").json()["state"]["waiting_for_rep"]
    assert rep_answer(client, UNKNOWN_SID, answer_text="1").status_code == 404
    assert rep_answer(client, sid, answer_text="x", participation=-5).status_code == 422


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("headers", [{}, {"X-Rep-Token": "wrong"}, {"X-Rep-Token": ""}])
def test_rep_endpoints_require_the_rep_key(client, headers):
    sid = converse(client, [*E004_CHAT, SPECIAL_PARTICIPATION])["session_id"]
    assert client.get("/api/rep/tickets", headers=headers).status_code == 401
    r = client.post(f"/api/rep/tickets/{sid}/answer", json={"answer_text": "0"}, headers=headers)
    assert r.status_code == 401
    assert client.get(f"/api/session/{sid}").json()["state"]["waiting_for_rep"]  # untouched


def test_rep_screen_closed_when_no_key_configured(db_path, monkeypatch):
    monkeypatch.setenv("REP_API_KEY", "")
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG))) as c:
        assert c.get("/api/rep/tickets").status_code == 503
        assert c.get("/api/rep/tickets", headers={"X-Rep-Token": ""}).status_code == 503
        assert c.get("/api/llm/check").status_code == 503


def test_rep_key_from_environment(db_path, monkeypatch):
    monkeypatch.setenv("REP_API_KEY", "env-rep-key-abcdefghij")
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG))) as c:
        ok = c.get("/api/rep/tickets", headers={"X-Rep-Token": "env-rep-key-abcdefghij"})
        assert ok.status_code == 200


def test_precheck_internal_instruction_is_not_exposed(client):
    body = chat(client, "אני מפתח תוכנה ורוצה לקחת טסלה מודל 3")
    pre = body["state"]["precheck"]
    assert pre["eligible"] is False and pre["alternatives"]
    assert "instruction" not in pre
    pre = client.get(f"/api/session/{body['session_id']}").json()["state"]["precheck"]
    assert "instruction" not in pre


def test_security_headers(client):
    r = client.post("/api/chat", json={"message": "שלום"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_swagger_shows_session_id_as_optional(client):
    schema = client.get("/openapi.json").json()
    assert schema["components"]["schemas"]["ChatRequest"]["required"] == ["message"]
    examples = (schema["paths"]["/api/chat"]["post"]["requestBody"]["content"]
                ["application/json"]["examples"])
    assert examples["first"]["value"] == {"message": "אני מנהל אזור מכירות, שכר בסיס 16,000"}


def test_docs_can_be_disabled(db_path, monkeypatch):
    monkeypatch.setenv("API_DOCS", "0")
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG))) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
