"""Step 4 - HTTP API (FastAPI TestClient): employee chat, demo loader, rep hand-off."""
from __future__ import annotations

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


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path=db_path, llm=RuleBasedLLM(CONFIG))) as c:
        yield c


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
    assert h["status"] == "ok"
    assert (h["cars"], h["occupations"], h["tax_records"]) == (20, 14, 77_988)
    assert h["llm"] == "RuleBasedLLM"


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
    assert atto["value_of_use"] == 3130 and "אטו 3" in atto["aliases"]
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
    assert "calculate_comparison" in last["tools_called"]
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
    with_tools = client.get(f"/api/session/{sid}", params={"include_tools": True}).json()
    assert any(m["role"] == "tool" for m in with_tools["messages"])


def test_chat_errors(client):
    assert client.post("/api/chat", json={"session_id": "nope", "message": "x"}).status_code == 404
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"session_id": "bad id!", "message": "x"}).status_code == 422
    assert client.get("/api/session/unknown").status_code == 404


# --------------------------------------------------------------------------- #
# Demo employees
# --------------------------------------------------------------------------- #
def test_load_demo_e001(client):
    body = client.post("/api/session/demo-1/load_demo/E-001").json()
    ref = CONFIG.reference_calculation("E-001")
    c = body["comparison"]
    assert body["session_id"] == "demo-1"
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (11032, 11693, 661)
    assert (c["tax_today"], c["tax_alt"], c["electric_gross"]) == (
        ref.tax_current, ref.tax_alternative, ref.electric_refund_gross)
    assert c["current_label"] == "COROLLA CROSS (2025)" and c["alternative_label"] == "ATTO 3 (2025)"
    st = body["state"]
    assert st["profile"]["occupation"] == "מנהל/ת מוצר" and st["missing_info"] == []
    msgs = client.get("/api/session/demo-1").json()["messages"]
    assert msgs[0]["content"] == CONFIG.employee("E-001").question


@pytest.mark.parametrize("ref", CONFIG.reference_calculations, ids=lambda r: r.employee_id)
def test_load_demo_all_employees_match_reference(client, ref):
    c = client.post(f"/api/session/s-{ref.employee_id}/load_demo/{ref.employee_id}").json()[
        "comparison"]
    assert (c["net_today"], c["net_alt"], c["net_diff"], c["family_gap_deduction"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff, ref.family_track_employee_share)


def test_load_demo_then_continue_chatting(client):
    client.post("/api/session/demo-2/load_demo/E-004")
    body = chat(client, "ואם אני אעבור לקורולה?", "demo-2")
    assert body["state"]["target"]["target_car_code"] == "CAR-07"
    assert body["comparison"]["alternative_label"] == "COROLLA (2026)"


def test_load_demo_unknown_employee(client):
    assert client.post("/api/session/x/load_demo/E-999").status_code == 404
    assert client.post("/api/session/x/load_demo/bad").status_code == 422


# --------------------------------------------------------------------------- #
# Representative (human in the loop)
# --------------------------------------------------------------------------- #
def test_rep_flow_freeze_ticket_answer(client):
    sid = converse(client, E004_CHAT)["session_id"]
    assert client.get("/api/rep/tickets").json() == []

    frozen = chat(client, SPECIAL_PARTICIPATION, sid)
    assert frozen["frozen"] and frozen["state"]["waiting_for_rep"]
    assert frozen["comparison"] is None
    assert "ask_representative" in frozen["tools_called"]

    tickets = client.get("/api/rep/tickets").json()
    assert len(tickets) == 1
    t = tickets[0]
    assert t["session_id"] == sid and "EV3" in t["rep_question"]
    assert t["rep_field"] == "target_participation"
    assert t["target_car"]["car_id"] == "CAR-11" and t["current_car"]["model_name"] == "TUCSON"
    assert t["profile"]["base_salary"] == 16000
    assert t["last_employee_message"] == SPECIAL_PARTICIPATION

    still = chat(client, "יש תשובה?", sid)  # employee writes while frozen
    assert still["frozen"] and still["tools_called"] == []

    ans = client.post(f"/api/rep/tickets/{sid}/answer",
                      json={"answer_text": "ההשתתפות שאושרה היא 350 ₪"})
    assert ans.status_code == 200, ans.text
    a = ans.json()
    ref = CONFIG.reference_calculation("E-004")
    assert a["ok"] and not a["state"]["waiting_for_rep"]
    assert a["state"]["target"]["target_participation"] == 350
    assert a["comparison"]["net_today"] == ref.net_current
    assert a["comparison"]["net_alt"] == ref.net_alternative - 50  # 350 instead of 300
    assert "11,850" in a["bot_message"]
    assert client.get("/api/rep/tickets").json() == []

    roles = [m["role"] for m in client.get(f"/api/session/{sid}").json()["messages"]]
    assert "representative" in roles


def test_rep_answer_with_explicit_participation_field(client):
    sid = converse(client, [*E004_CHAT, SPECIAL_PARTICIPATION])["session_id"]
    a = client.post(f"/api/rep/tickets/{sid}/answer",
                    json={"answer_text": "אושר", "participation": 300}).json()
    assert a["comparison"]["net_alt"] == CONFIG.reference_calculation("E-004").net_alternative


def test_rep_answer_errors(client):
    sid = converse(client, E004_CHAT)["session_id"]
    r = client.post(f"/api/rep/tickets/{sid}/answer", json={"answer_text": "350"})
    assert r.status_code == 409  # not waiting
    chat(client, SPECIAL_PARTICIPATION, sid)
    r = client.post(f"/api/rep/tickets/{sid}/answer", json={"answer_text": "צריך לבדוק"})
    assert r.status_code == 422  # no number -> conversation stays frozen
    assert client.get(f"/api/session/{sid}").json()["state"]["waiting_for_rep"]
    assert client.post("/api/rep/tickets/nope/answer",
                       json={"answer_text": "1"}).status_code == 404
    assert client.post(f"/api/rep/tickets/{sid}/answer",
                       json={"answer_text": "x", "participation": -5}).status_code == 422
