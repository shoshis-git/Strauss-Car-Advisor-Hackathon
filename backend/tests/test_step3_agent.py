"""Step 3 - agent behaviour: state extraction, tool use, representative hand-off.

Runs offline on the deterministic RuleBasedLLM (same tool-call protocol as a real
model). A live test runs only when LLM_PROVIDER/LLM_MODEL point at a real provider.
"""
from __future__ import annotations

import json
import os

import pytest

from backend.agent.graph import AdvisorAgent
from backend.agent.llm import (LLMError, LLMStep, OpenAICompatibleLLM, ToolCall,
                               assistant_message, load_env_file, make_llm)
from backend.agent.rule_based import RuleBasedLLM, extract_fields
from backend.agent.state import ConversationState, StateStore
from backend.agent.tools import TOOL_SPECS, find_cars_in_text, resolve_car, update_profile
from backend.data_loader.config_loader import get_config

CONFIG = get_config()


class SpyLLM:
    """Wraps an LLM and counts how many times it was consulted."""

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def step(self, **kw):
        self.calls += 1
        return self.inner.step(**kw)


class ScriptedLLM:
    """Replays fixed steps - simulates a model's tool-use decisions."""

    def __init__(self, steps: list[LLMStep]):
        self.steps = list(steps)

    def step(self, **kw):
        return self.steps.pop(0)


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "conversations.db")


@pytest.fixture
def spy():
    return SpyLLM(RuleBasedLLM(CONFIG))


@pytest.fixture
def agent(store, spy):
    return AdvisorAgent(CONFIG, store, spy)


def say(agent, conv_id, *texts):
    reply = None
    for t in texts:
        reply = agent.handle_employee_message(conv_id, t)
    return reply


# --------------------------------------------------------------------------- #
# 1. Extracting salary details from free text into the State
# --------------------------------------------------------------------------- #
def test_extracts_salary_and_group_into_state(agent, store):
    conv = agent.start_conversation().conversation_id
    reply = say(agent, conv, "שכר בסיס 14,200 קבוצה ג'")

    p = reply.state.profile
    assert (p.base_salary, p.eligibility_group) == (14200, "ג")
    assert reply.tools_called == ["update_profile"]
    assert "base_salary" not in reply.state.missing_info
    assert "eligibility_group" not in reply.state.missing_info
    # next phase requested politely: pension components and credit points
    assert {"pension_pct", "credit_points"} <= set(reply.state.missing_info)
    assert "פנסיה" in reply.text and "נקודות זיכוי" in reply.text
    # persisted to the database
    assert store.load(conv).profile.base_salary == 14200


def test_occupation_in_text_sets_group_from_occupation_table(agent):
    conv = agent.start_conversation().conversation_id
    st = say(agent, conv, "שלום, שכר הבסיס שלי 18,500 ואני מנהלת מוצר").state
    assert st.profile.occupation == "מנהל/ת מוצר"
    assert st.profile.eligibility_group == "ג"


@pytest.mark.parametrize("field", ["current_car", "target_car"])
@pytest.mark.parametrize("name", ["FERRARI F40", "CAR-99", "פרארי", "קורולה 2030"])
def test_unknown_car_is_rejected_not_guessed(field, name):
    """update_profile is fed a car that is not in the catalogue -> clear error, no guess."""
    state = ConversationState()
    result = update_profile(state, CONFIG, base_salary=14200, **{field: name})

    assert result["ok"] is False
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith(f"{field}: הרכב {name!r} לא נמצא בקטלוג")
    assert "current_car" not in result["updated"] and "target_car" not in result["updated"]
    assert result["updated"] == {"base_salary": 14200}  # valid fields still applied
    assert state.current.current_car_code is None and state.current.status is None
    assert state.target.target_car_code is None and state.target.target_action is None


def test_partial_car_name_is_suggested_not_applied():
    state = ConversationState()
    result = update_profile(state, CONFIG, target_car="קורולה 2030")
    assert not result["ok"] and state.target.target_car_code is None
    assert "האם הכוונה ל-COROLLA (CAR-07)" in result["errors"][0]


def test_unknown_car_from_model_is_reported_to_employee(store):
    """A model that tries to save a non-catalogue car gets the error back and must ask."""
    calls = [ToolCall("t1", "update_profile", {"target_car": "FERRARI F40"})]
    llm = ScriptedLLM([LLMStep("", calls, assistant_message("", calls)),
                       LLMStep("הרכב FERRARI F40 אינו בקטלוג. לאיזה רכב התכוונת?")])
    agent = AdvisorAgent(CONFIG, store, llm)
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "אני רוצה פרארי")
    tool_msg = [m for m in reply.state.messages if m.role == "tool"][-1]
    assert json.loads(tool_msg.content)["ok"] is False
    assert "לא נמצא בקטלוג" in tool_msg.content
    assert reply.state.target.target_car_code is None


# --------------------------------------------------------------------------- #
# 2. Allowed car requested -> calculate_comparison returns the difference
# --------------------------------------------------------------------------- #
E001_CHAT = [
    "שלום, שכר הבסיס שלי 18,500 ואני מנהלת מוצר",
    "אין לי עמלות או בונוס. אני מפרישה 6% לפנסיה ו-2.5% לקרן השתלמות, ויש לי 2.75 נקודות זיכוי",
    "היום יש לי COROLLA CROSS עם השתתפות 300 ₪, ואני שוקלת לעבור ל-ATTO 3",
]


def test_allowed_car_triggers_comparison_matching_reference(agent):
    conv = agent.start_conversation().conversation_id
    reply = say(agent, conv, *E001_CHAT)
    ref = CONFIG.reference_calculation("E-001")

    assert "calculate_comparison" in reply.tools_called
    st = reply.state
    assert st.missing_info == []
    assert (st.current.current_car_code, st.target.target_car_code) == ("CAR-09", "CAR-10")
    c = st.last_comparison
    assert c["is_eligible"] and c["reason"] == ref.reason
    assert (c["net_today"], c["net_alt"], c["net_diff"]) == (
        ref.net_current, ref.net_alternative, ref.net_diff)        # 11,032 → 11,693 (+661)
    assert c["explanation"] == ref.explanation
    assert "+661" in reply.text and "11,693" in reply.text


def test_comparison_not_run_while_data_missing(agent):
    conv = agent.start_conversation().conversation_id
    reply = say(agent, conv, "שכר בסיס 14,200 קבוצה ג'", "אני רוצה לעבור ל-ATTO 3")
    assert "calculate_comparison" not in reply.tools_called
    assert reply.state.last_comparison is None


def test_higher_group_car_is_refused(agent):
    conv = agent.start_conversation().conversation_id
    reply = say(agent, conv, *E001_CHAT[:2],
                "היום יש לי COROLLA CROSS עם השתתפות 300 ₪, ואני רוצה לעבור ל-MODEL 3")
    c = reply.state.last_comparison
    assert c["is_eligible"] is False and "מעל הקבוצה שלך" in c["reason"]
    assert "אינה אפשרית" in reply.text


def test_get_allowed_cars_lists_only_own_or_lower_groups(agent):
    conv = agent.start_conversation().conversation_id
    reply = say(agent, conv, "שכר בסיס 14,200 קבוצה ג'", "אילו רכבים מותרים לי?")
    assert "get_allowed_cars" in reply.tools_called
    assert "CAR-14" not in reply.text and "CAR-09" in reply.text  # ד excluded, ג included


# --------------------------------------------------------------------------- #
# 3. ask_representative freezes the conversation until the rep answers
# --------------------------------------------------------------------------- #
E004_CHAT = [
    "שכר בסיס 16,000 ועמלות 3,000, אני מנהל אזור מכירות",
    "פנסיה 6%, השתלמות 2.5%, 2.25 נקודות זיכוי",
    "היום יש לי TUCSON עם השתתפות 400",
]


def test_ask_representative_freezes_and_resumes(agent, store, spy):
    conv = agent.start_conversation().conversation_id
    say(agent, conv, *E004_CHAT)
    reply = agent.handle_employee_message(
        conv, "אני רוצה לעבור ל-EV3 אבל סוכמה לי השתתפות מיוחדת ואני לא יודע כמה")

    # -> waiting for the representative; no calculation was made
    st = reply.state
    assert reply.frozen and st.waiting_for_rep
    assert "ask_representative" in reply.tools_called
    assert "calculate_comparison" not in reply.tools_called
    assert "EV3" in st.rep_question and st.rep_field == "target_participation"
    assert st.target.target_car_code == "CAR-11" and st.last_comparison is None
    assert store.waiting_for_rep() == [conv]
    assert "נציג" in reply.text

    # -> frozen: further employee messages do not reach the LLM or the tools
    llm_calls = spy.calls
    frozen = agent.handle_employee_message(conv, "נו, יש כבר תשובה?")
    assert frozen.frozen and frozen.tools_called == [] and spy.calls == llm_calls
    assert store.load(conv).waiting_for_rep

    # -> rep answers: state unfreezes, participation applied, calculation runs
    resumed = agent.handle_rep_answer(conv, "ההשתתפות שאושרה היא 350 ₪")
    st = resumed.state
    assert not st.waiting_for_rep and st.rep_question is None
    assert st.target.target_participation == 350
    assert resumed.tools_called == ["calculate_comparison"]
    ref = CONFIG.reference_calculation("E-004")
    assert st.last_comparison["net_today"] == ref.net_current
    # participation is a pure net deduction: catalogue 300 -> approved 350 = -50 net
    assert st.last_comparison["net_alt"] == ref.net_alternative - 50
    assert store.waiting_for_rep() == []
    roles = [m.role for m in store.load(conv).messages]
    assert "representative" in roles


def test_rep_answer_rejected_when_not_waiting(agent):
    conv = agent.start_conversation().conversation_id
    with pytest.raises(ValueError):
        agent.handle_rep_answer(conv, "300")


def test_freeze_skips_tool_calls_after_handoff_in_same_step(store):
    """A model that asks the rep AND calculates in one step: the calculation is skipped."""
    calls = [ToolCall("t1", "ask_representative",
                      {"question": "מה ההשתתפות ב-EV3?", "field": "target_participation"}),
             ToolCall("t2", "calculate_comparison", {})]
    llm = ScriptedLLM([LLMStep("", calls, assistant_message("", calls))])
    agent = AdvisorAgent(CONFIG, store, llm)
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "מה ההשתתפות ב-EV3?")
    assert reply.frozen and reply.tools_called == ["ask_representative"]
    tool_msgs = [m for m in reply.state.messages if m.role == "tool"]
    assert tool_msgs[-1].tool_name == "calculate_comparison" and "מוקפאת" in tool_msgs[-1].content


# --------------------------------------------------------------------------- #
# Hebrew aliases
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("alias, car_id", [
    ("אטו 3", "CAR-10"), ("קורולה", "CAR-07"), ("קורולה קרוס", "CAR-09"),
    ("טסלה 3", "CAR-14"), ("טסלה מודל 3", "CAR-14"), ("קרניבל", "CAR-19"),
    ("סנטה פה", "CAR-20"), ("Atto 3", "CAR-10"), ("CAR-5", "CAR-05"),
])
def test_alias_resolves_to_catalogue_car(alias, car_id):
    assert resolve_car(CONFIG, alias).car_id == car_id


def test_aliases_in_sentences_with_hebrew_prefixes():
    def ids(t):
        return [c.car_id for _, c in find_cars_in_text(CONFIG, t)]
    assert ids("אני רוצה לעבור מהקורולה קרוס לאטו 3") == ["CAR-09", "CAR-10"]
    assert ids("יש לי קורולה ואני שוקל לטסלה") == ["CAR-07", "CAR-14"]
    assert ids("מה עם הקרניבל או הסנטה פה?") == ["CAR-19", "CAR-20"]
    assert ids("אני קונה רכב חדש") == []  # 'קונה' (buys) is not KONA


# The original questions of the 6 sample employees (sheet 'עובדים לדוגמה')
@pytest.mark.parametrize("emp_id, current, target", [
    ("E-001", "CAR-09", "CAR-10"),   # מהקורולה קרוס ל-BYD Atto 3
    ("E-002", None, "CAR-19"),       # אם אקח קרניבל
    ("E-003", None, "CAR-06"),       # אם אקח MG4
    ("E-004", "CAR-12", "CAR-11"),   # אם אחליף את הטוסון ב-EV3
    ("E-006", "CAR-15", "CAR-14"),   # מהאוקטביה לטסלה מודל 3
])
def test_sample_employee_questions_resolve_cars(emp_id, current, target):
    emp = CONFIG.employee(emp_id)
    state = ConversationState()
    state.current.status = "car"  # current car already known -> single car = target
    f = extract_fields(emp.question, state, CONFIG)
    assert f.get("current_car") == current and f.get("target_car") == target


def test_sample_waiver_question():
    f = extract_fields(CONFIG.employee("E-005").question, ConversationState(), CONFIG)
    assert f.get("target_action") == "waiver"


def test_hebrew_conversation_end_to_end(agent):
    conv = agent.start_conversation().conversation_id
    reply = say(agent, conv, *E001_CHAT[:2],
                "היום יש לי קורולה קרוס עם השתתפות 300 ₪, ואני שוקלת לעבור לאטו 3")
    assert reply.state.last_comparison["net_diff"] == CONFIG.reference_calculation("E-001").net_diff


def test_aliases_are_data_and_validated():
    assert CONFIG.car_aliases["CAR-19"][0] == "קרניבל"
    with pytest.raises(ValueError, match="used for both"):
        CONFIG.model_validate({**CONFIG.model_dump(),
                               "car_aliases": {"CAR-01": ["X"], "CAR-02": ["x"]}})


# --------------------------------------------------------------------------- #
# Generic LLM layer (no network: fake transport)
# --------------------------------------------------------------------------- #
def test_default_provider_is_rule_based():
    assert isinstance(make_llm(CONFIG, env={}), RuleBasedLLM)
    assert isinstance(make_llm(CONFIG, env={"LLM_PROVIDER": "rules"}), RuleBasedLLM)


@pytest.mark.parametrize("provider, base_url", [
    ("openai", "https://api.openai.com/v1"),
    ("anthropic", "https://api.anthropic.com/v1"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("ollama", "http://localhost:11434/v1"),
])
def test_provider_selection_from_env(provider, base_url):
    llm = make_llm(CONFIG, env={"LLM_PROVIDER": provider, "LLM_MODEL": "m", "LLM_API_KEY": "k"})
    assert isinstance(llm, OpenAICompatibleLLM)
    assert (llm.base_url, llm.model, llm.api_key) == (base_url, "m", "k")


def test_base_url_override_and_config_errors():
    llm = make_llm(CONFIG, env={"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "qwen",
                                "LLM_BASE_URL": "http://my-vllm:8000/v1/"})
    assert llm.base_url == "http://my-vllm:8000/v1" and llm.api_key is None
    with pytest.raises(LLMError, match="LLM_MODEL"):
        make_llm(CONFIG, env={"LLM_PROVIDER": "openai"})
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        make_llm(CONFIG, env={"LLM_PROVIDER": "openai", "LLM_MODEL": "m"})
    with pytest.raises(LLMError, match="LLM_BASE_URL"):
        make_llm(CONFIG, env={"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "m"})
    with pytest.raises(LLMError, match="unknown"):
        make_llm(CONFIG, env={"LLM_PROVIDER": "nope", "LLM_MODEL": "m"})


class FakeChatCompletions:
    """Stands in for an OpenAI-compatible server: records requests, replays responses."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, json.loads(json.dumps(body))))
        return self.responses.pop(0)


def _tool_call_response(name, args, call_id="call_1"):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}]}


def _text_response(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def test_openai_compatible_adapter_runs_full_tool_loop(store):
    server = FakeChatCompletions([
        _tool_call_response("update_profile", {"base_salary": 14200, "eligibility_group": "ג"}),
        _text_response("תודה! מה אחוז ההפרשה שלך לפנסיה?"),
    ])
    llm = OpenAICompatibleLLM("gpt-x", "https://llm.example/v1", "secret", transport=server)
    agent = AdvisorAgent(CONFIG, store, llm)
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "שכר בסיס 14,200 קבוצה ג'")

    assert (reply.state.profile.base_salary, reply.state.profile.eligibility_group) == (14200, "ג")
    assert reply.text == "תודה! מה אחוז ההפרשה שלך לפנסיה?"
    url, headers, first = server.requests[0]
    assert url == "https://llm.example/v1/chat/completions"
    assert headers == {"Authorization": "Bearer secret"}
    assert first["model"] == "gpt-x" and first["messages"][0]["role"] == "system"
    assert "אתה יועץ רכב חברה לעובדים" in first["messages"][0]["content"]
    assert {t["function"]["name"] for t in first["tools"]} == {t["name"] for t in TOOL_SPECS}
    # second request carries the assistant tool call and the tool result back
    second = server.requests[1][2]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "update_profile"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    assert json.loads(second[-1]["content"])["updated"]["base_salary"] == 14200


def test_adapter_tolerates_missing_ids_and_bad_arguments():
    from backend.agent.llm import parse_completion
    step = parse_completion({"choices": [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "update_profile", "arguments": "{not json"}}]}}]})
    assert step.tool_calls[0].id.startswith("call_")
    assert step.tool_calls[0].input == {"_invalid_arguments": "{not json"}
    with pytest.raises(LLMError):
        parse_completion({"error": "boom"})


# --------------------------------------------------------------------------- #
# Optional live test against a real provider configured in the environment / .env
# --------------------------------------------------------------------------- #
load_env_file()


@pytest.mark.skipif(os.environ.get("LLM_PROVIDER", "rules") == "rules",
                    reason="LLM_PROVIDER not set to a real provider")
def test_live_llm_extracts_salary(store):
    agent = AdvisorAgent(CONFIG, store, make_llm(CONFIG))
    conv = agent.start_conversation().conversation_id
    st = agent.handle_employee_message(conv, "שכר בסיס 14,200 קבוצה ג'").state
    assert (st.profile.base_salary, st.profile.eligibility_group) == (14200, "ג")
