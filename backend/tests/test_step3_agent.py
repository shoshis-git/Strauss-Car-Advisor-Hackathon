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
    assert "דגמים קרובים בקטלוג: COROLLA (CAR-07)" in result["errors"][0]
    assert "יש לאשר מול העובד" not in result["errors"][0]  # caused double confirmations


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
    assert reply.state.precheck["eligible"] is False
    assert "מעל הקבוצה שלך" in reply.state.precheck["reason"]
    assert reply.state.missing_info == ["allowed_alternative"]
    assert "לא ניתן לבצע את הבקשה" in reply.text and "חלופות מותרות" in reply.text


# --------------------------------------------------------------------------- #
# Review fixes (from real conversations): fail-fast, group, car names, rounds
# --------------------------------------------------------------------------- #
def test_fail_fast_disallowed_car_in_first_message(agent):
    """Q1: 'developer wants a Tesla' - blocked immediately, no salary questions."""
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(
        conv, "אני מפתח תוכנה ורוצה לדעת כמה יעלה לי בנטו לקחת טסלה מודל 3")
    st = reply.state
    assert st.profile.occupation == "מפתח/ת תוכנה" and st.profile.eligibility_group == "ב"
    assert st.precheck["eligible"] is False and "CAR-14" in st.precheck["requested"]
    assert st.missing_info == ["allowed_alternative"]
    assert "שכר בסיס" not in reply.text                 # no salary questionnaire
    alts = st.precheck["alternatives"]
    assert alts and all(a["group"] in ("א", "ב") for a in alts)
    assert alts[0]["drive"] == "חשמלי"                   # closest to the request first
    assert {"MG4", "KONA EV"} & {a["model_name"] for a in alts}


def test_fail_fast_field_role_waiver_in_first_message(agent):
    """Q2: 'service technician wants to waive the car' - blocked immediately."""
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(
        conv, "אני טכנאי שירות ורוצה לוותר על הרכב תמורת אחזקת רכב")
    st = reply.state
    assert st.precheck["eligible"] is False and "חובה" in st.precheck["reason"]
    assert st.missing_info == ["allowed_alternative"]
    assert "שכר בסיס" not in reply.text


def test_choosing_an_allowed_alternative_unblocks(agent):
    conv = agent.start_conversation().conversation_id
    say(agent, conv, "אני מפתח תוכנה ורוצה לקחת טסלה מודל 3")
    reply = agent.handle_employee_message(conv, "אז אני רוצה לעבור ל-MG4")
    assert reply.state.precheck["eligible"] is True
    assert "allowed_alternative" not in reply.state.missing_info
    assert "base_salary" in reply.state.missing_info     # now the questionnaire starts


def test_group_comes_from_occupation_not_the_car():
    """Q5: the model sent the car's group (א) for an analyst (ב) - must be rejected."""
    state = ConversationState()
    r = update_profile(state, CONFIG, occupation="אנליסט נתונים", eligibility_group="א",
                       target_car="BYD דולפין")
    assert state.profile.eligibility_group == "ב"
    assert any("נקבעת לפי העיסוק" in e for e in r["errors"])
    assert state.target.target_car_code == "CAR-03"      # 'BYD דולפין' accepted directly
    # and a later attempt to overwrite it is rejected too
    update_profile(state, CONFIG, eligibility_group="א")
    assert state.profile.eligibility_group == "ב"


def test_occupation_sets_group_even_if_group_was_given_first():
    state = ConversationState()
    update_profile(state, CONFIG, eligibility_group="ג")
    update_profile(state, CONFIG, occupation="מפתח תוכנה")
    assert state.profile.eligibility_group == "ב"


@pytest.mark.parametrize("name, car_id", [
    ("BYD Seal", "CAR-17"), ("byd seal", "CAR-17"), ("BYD דולפין", "CAR-03"),
    ("טויוטה קורולה קרוס", "CAR-09"), ("SEAL 2025", "CAR-17"), ("Tesla Model 3", "CAR-14"),
])
def test_unambiguous_car_names_are_accepted_without_confirmation(name, car_id):
    """Q6: 'BYD Seal' was rejected twice and the model asked to confirm twice."""
    assert resolve_car(CONFIG, name).car_id == car_id


@pytest.mark.parametrize("name", ["קורולה 2030", "טסלה מודל Y", "Tesla Model S", "SEAL 2019"])
def test_names_with_extra_meaning_are_not_guessed(name):
    assert resolve_car(CONFIG, name) is None


def test_occupation_typo_is_matched_when_unambiguous():
    """Q2 conversation: 'נציג מבירות שטח' (typo) was rejected."""
    state = ConversationState()
    update_profile(state, CONFIG, occupation="נציג מבירות שטח")
    assert state.profile.occupation == "נציג/ת מכירות שטח"
    r = update_profile(ConversationState(), CONFIG, occupation="אסטרונאוט")
    assert not r["ok"] and "לא נמצא" in r["errors"][0]


def test_salary_questions_come_in_one_round(agent):
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "אני רוצה לבדוק מעבר לרכב חשמלי")
    for label in ("שכר בסיס", "פנסיה", "השתלמות", "נקודות זיכוי"):
        assert label in reply.text


def test_family_track_figures_come_from_the_engine():
    """Q3 replay: the model computed 'gap 1,100 -> 550' itself. Now the tool supplies it."""
    state = ConversationState()
    r = update_profile(state, CONFIG, occupation="מנהל מוצר", children_count=4,
                       target_car="סנטה פה")
    ft = r["eligibility"]["family_track"]
    assert (ft["company_monthly_cost"], ft["group_budget"]) == (5600, 4500)
    assert (ft["gap_above_budget"], ft["employee_share_monthly_net_deduction"]) == (1100, 550)
    assert ft["plus_regular_participation"] == 500


def test_prompt_forbids_self_computed_numbers():
    from backend.agent.prompts import build_system_prompt
    prompt = build_system_prompt(ConversationState(), CONFIG)
    assert "לעולם אל תחשב סכומים בעצמך" in prompt and "eligibility.family_track" in prompt


def test_system_prompt_carries_policy_facts_from_rules():
    from backend.agent.prompts import build_system_prompt
    prompt = build_system_prompt(ConversationState(), CONFIG)
    assert "250 ₪" in prompt and "מגולם" in prompt          # electric refund, from rules.json
    assert "4 ילדים" in prompt and "50%" in prompt          # family track
    assert "MG4" in prompt and "טכנאי/ת שירות" in prompt     # catalogue + occupations
    assert "Fail-fast" in prompt and "אל תבקש אישור" in prompt


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
# Scenario 3.1 - case isolation + an unknown participation goes to the rep
# (the real conversation: a Picanto waiver case, then a new employee in the same chat)
# --------------------------------------------------------------------------- #
PICANTO_CASE = [
    "אם אוותר לגמרי על הפיקנטו ואקח אחזקת רכב, כמה כסף זה יוסיף לי בפועל לנטו?",
    "תפקיד: רכז/ת רכש. שכר בסיס: 11,000 ₪. פנסיה 6%, השתלמות 2.5%. נקודות זיכוי: 2.75. "
    "נוהג ב-PICANTO, השתתפות: 150 ₪.",
    "אין עמלות",
]
TUCSON_CASE = ("אני מנהל אזור מכירות (קבוצה ג') עם שכר 16,000 ₪ ועמלות 3,000 ₪. אני נוהג "
               "בטוסון ורוצה לעבור לקורולה קרוס. אבל אין לי מושג כמה תהיה ההשתתפות שלי "
               "ברכב החדש.")
# exactly what Gemini did: saved the new data and calculated in the same step
TUCSON_ARGS = {"current_car": "טוסון", "occupation": "מנהל אזור מכירות", "base_salary": 16000,
               "target_action": "replace", "commissions_bonuses": 3000,
               "target_car": "קורולה קרוס"}


def _disobedient_llm(*extra_calls):
    calls = [ToolCall("g1", "update_profile", TUCSON_ARGS),
             ToolCall("g2", "calculate_comparison", {}), *extra_calls]
    return ScriptedLLM([LLMStep("", calls, assistant_message("", calls)),
                        LLMStep("ההשתתפות בקורולה קרוס היא 300 ₪ (לעומת 150 ₪ בטוסון)", [],
                                assistant_message("x", []))])


@pytest.fixture
def after_picanto(agent):
    conv = agent.start_conversation().conversation_id
    say(agent, conv, *PICANTO_CASE)
    st = agent.store.load(conv)
    assert st.current.current_participation == 150 and st.last_comparison  # case 1 done
    return conv


def test_new_employee_in_same_chat_inherits_nothing(agent, after_picanto):
    agent.llm = _disobedient_llm()
    r = agent.handle_employee_message(after_picanto, TUCSON_CASE)
    p, cur = r.state.profile, r.state.current
    # stated in this message - kept
    assert (p.occupation, p.base_salary, p.commissions_bonuses) == (
        "מנהל/ת אזור מכירות", 16000, 3000)
    assert cur.current_car_code == "CAR-12" and r.state.target.target_car_code == "CAR-09"
    # from the previous case - all gone (no 6% / 2.5% / 2.75 / Picanto's 150 ₪)
    assert (p.pension_pct, p.hishtalmut_pct, p.credit_points) == (None, None, None)
    assert cur.current_participation is None
    # the calculation the model asked for was refused - nothing was computed
    calc = json.loads(next(m.content for m in reversed(r.state.messages)
                           if m.tool_name == "calculate_comparison"))
    assert calc["ok"] is False and "pension_pct" in calc["missing_info"]
    assert r.state.last_comparison is None


def test_unknown_participation_goes_to_rep_never_to_default(agent, after_picanto):
    agent.llm = _disobedient_llm()
    r = agent.handle_employee_message(after_picanto, TUCSON_CASE)
    # deterministic hand-off, although the model never called ask_representative
    assert r.frozen and r.state.waiting_for_rep and "ask_representative" in r.tools_called
    assert r.state.rep_field == "target_participation"
    assert "COROLLA CROSS" in r.state.rep_question
    assert "פתחתי פנייה לנציג" in r.text and "300" not in r.text and "150" not in r.text
    assert r.state.participation_unknown == "target_participation"
    assert "target_participation" in r.state.missing_info


def test_model_cannot_fill_an_unknown_participation_with_the_default(store):
    """Even if the model 'helpfully' sends the catalogue default, it is rejected."""
    calls = [ToolCall("a", "update_profile", {**TUCSON_ARGS, "target_participation": 300})]
    llm = ScriptedLLM([LLMStep("", calls, assistant_message("", calls)),
                       LLMStep("ok", [], assistant_message("ok", []))])
    agent = AdvisorAgent(CONFIG, store, llm)
    conv = agent.start_conversation().conversation_id
    r = agent.handle_employee_message(conv, TUCSON_CASE)
    upd = json.loads(next(m.content for m in r.state.messages if m.tool_name == "update_profile"))
    assert any("target_participation" in e and "נציג" in e for e in upd["errors"])
    assert r.state.target.target_participation is None and r.state.waiting_for_rep


def test_after_rep_answer_salary_is_still_required_before_any_result(agent, after_picanto):
    agent.llm = _disobedient_llm()
    agent.handle_employee_message(after_picanto, TUCSON_CASE)
    agent.llm = RuleBasedLLM(CONFIG)
    r = agent.handle_rep_answer(after_picanto, "ההשתתפות שנקבעה היא 350 ₪")
    st = r.state
    assert st.target.target_participation == 350 and st.participation_unknown is None
    assert st.last_comparison is None  # gatekeeper: pension / credit points still missing
    assert {"pension_pct", "hishtalmut_pct", "credit_points",
            "current_participation"} <= set(st.missing_info)
    r = say(agent, after_picanto, "פנסיה 6%, השתלמות 2.5%, 2.25 נקודות זיכוי",
            "היום יש לי טוסון עם השתתפות 400")
    c = r.state.last_comparison
    assert c and c["alternative"]["participation"] == 350
    assert c["today"]["participation"] == 400  # the Tucson's own value, not the Picanto's 150
    assert c["alternative_participation_source"] == "approved_by_representative"


def test_previous_case_is_hidden_from_the_model(agent, after_picanto):
    seen = []

    class Recorder:
        def step(self, **kw):
            seen.append([m.get("content") for m in kw["messages"]])
            return LLMStep("מה אחוז הפנסיה?", [], assistant_message("x", []))

    agent.llm = _disobedient_llm()
    agent.handle_employee_message(after_picanto, TUCSON_CASE)
    agent.llm = Recorder()
    agent.handle_rep_answer(after_picanto, "350")
    agent.handle_employee_message(after_picanto, "פנסיה 6%")
    text = " ".join(str(c) for c in seen[-1])
    assert "פיקנטו" not in text and "PICANTO" not in text and "11,000" not in text
    assert "טוסון" in text


def test_changing_the_current_car_drops_its_participation():
    st = ConversationState()
    update_profile(st, CONFIG, current_car="פיקנטו", current_participation=150)
    update_profile(st, CONFIG, current_car="טוסון")
    assert st.current.current_participation is None
    update_profile(st, CONFIG, current_car="קורולה", current_participation=250)  # same call
    assert st.current.current_participation == 250
    # a participation known before any car (e.g. from the payslip) belongs to the first car
    st = ConversationState()
    update_profile(st, CONFIG, current_participation=400)
    update_profile(st, CONFIG, current_car="טוסון")
    assert st.current.current_participation == 400


def test_start_new_case_tool_resets_everything(agent, after_picanto):
    calls = [ToolCall("n", "start_new_case", {})]
    agent.llm = ScriptedLLM([LLMStep("", calls, assistant_message("", calls)),
                             LLMStep("תרחיש חדש - מה השכר?", [], assistant_message("x", []))])
    r = agent.handle_employee_message(after_picanto, "שאלה חדשה על חבר שלי")
    assert r.state.profile.base_salary is None and r.state.current.current_car_code is None
    assert r.state.messages[r.state.case_start].content == "שאלה חדשה על חבר שלי"


@pytest.mark.parametrize("text, field", [
    ("אין לי מושג כמה תהיה ההשתתפות שלי ברכב החדש", "target_participation"),
    ("סוכמה לי השתתפות מיוחדת ואני לא יודע כמה", "target_participation"),
    ("אני לא זוכר כמה השתתפות אני משלם היום", "current_participation"),
    ("ההשתתפות שלי 300", None),
    ("אני לא יודע מה אחוז הפנסיה", None),
])
def test_unknown_participation_detection(text, field):
    from backend.agent.state import participation_unknown_field
    assert participation_unknown_field(text, ConversationState()) == field


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
def test_language_model_is_mandatory():
    """No silent model-less mode: LLM_PROVIDER must be configured."""
    with pytest.raises(LLMError, match="LLM_PROVIDER is not set"):
        make_llm(CONFIG, env={})
    # the deterministic brain only when asked for explicitly (tests / offline)
    assert isinstance(make_llm(CONFIG, env={"LLM_PROVIDER": "rules"}), RuleBasedLLM)


@pytest.mark.parametrize("status, words", [
    (402, "קרדיט"), (429, "מכסת"), (401, "מפתח ה-API נדחה"), (403, "מפתח ה-API נדחה"),
    (404, "LLM_MODEL"), (None, "לא ניתן להתחבר"), (503, "תקלה אצל ספק"),
])
def test_llm_errors_explain_the_real_cause(status, words):
    assert words in LLMError("x", status=status).user_message


def test_gemini_real_error_texts_are_classified_by_status():
    prepay = '{"error":{"code":402,"message":"Your prepayment credits are depleted."}}'
    assert "קרדיט" in LLMError(f"LLM HTTP 402: {prepay}", status=402).user_message
    quota = ('{"error":{"code":429,"message":"You exceeded your current quota, please check '
             'your plan and billing details."}}')
    msg = LLMError(f"LLM HTTP 429: {quota}", status=429).user_message
    assert "מכסת" in msg and "קרדיט" not in msg


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


def test_provider_tool_call_extras_are_echoed_back(store):
    """Gemini 3 rejects (HTTP 400) a follow-up request whose function call lost its
    thought_signature. The adapter must return tool calls exactly as received."""
    signed = _tool_call_response("update_profile", {"base_salary": 14200})
    signed["choices"][0]["message"]["tool_calls"][0]["extra_content"] = {
        "google": {"thought_signature": "SIG-123"}}

    class GeminiLike(FakeChatCompletions):
        def __call__(self, url, headers, body, timeout):
            for m in body["messages"]:
                for tc in m.get("tool_calls") or []:
                    if tc.get("extra_content", {}).get("google", {}).get(
                            "thought_signature") != "SIG-123":
                        raise LLMError("LLM HTTP 400: Function call is missing a thought_signature")
            return super().__call__(url, headers, body, timeout)

    server = GeminiLike([signed, _text_response("תודה!")])
    llm = OpenAICompatibleLLM("gemini-3.8-flash", "https://g.example/v1", "k", transport=server)
    agent = AdvisorAgent(CONFIG, store, llm)
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "שכר בסיס 14,200")
    assert reply.text == "תודה!" and reply.state.profile.base_salary == 14200
    echoed = server.requests[1][2]["messages"][-2]["tool_calls"][0]
    assert echoed["extra_content"]["google"]["thought_signature"] == "SIG-123"


def test_api_key_never_appears_in_repr_or_tracebacks():
    import uuid
    key = uuid.uuid4().hex  # generated, so it is not in this file's source lines

    def failing_transport(url, headers, body, timeout):
        raise LLMError(f"boom {headers!r}")

    llm = OpenAICompatibleLLM("m", "https://x.example/v1", key, transport=failing_transport)
    with pytest.raises(LLMError) as exc:
        llm.step(system="s", messages=[], tools=[], state=ConversationState())
    assert key not in str(exc.value) and "'***'" in str(exc.value)
    assert key not in repr(llm.api_key)
    # what pytest prints by default for a failure: source + arguments of every frame
    # (this is where the key leaked before: `headers = {'Authorization': 'Bearer ...'}`)
    printed = str(exc.getrepr(style="long", funcargs=True))
    assert "headers = " in printed and key not in printed


def test_adapter_tolerates_missing_ids_and_bad_arguments():
    from backend.agent.llm import parse_completion
    step = parse_completion({"choices": [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "update_profile", "arguments": "{not json"}}]}}]})
    assert step.tool_calls[0].id.startswith("call_")
    assert step.tool_calls[0].input == {"_invalid_arguments": "{not json"}
    with pytest.raises(LLMError):
        parse_completion({"error": "boom"})


# --------------------------------------------------------------------------- #
# Resilience: primary LLM fails (quota / outage) -> deterministic fallback
# --------------------------------------------------------------------------- #
class FailingLLM:
    """Fails like Gemini's free tier did: HTTP 429 RESOURCE_EXHAUSTED.
    `succeed_first` scripted steps are returned before it starts failing."""

    def __init__(self, status=429, succeed_first=()):
        self.status, self.calls, self.script = status, 0, list(succeed_first)

    def step(self, **kw):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        raise LLMError(f"LLM HTTP {self.status}: quota exceeded", status=self.status)


def test_default_has_no_fallback_llm_failure_is_reported(store, monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    agent = AdvisorAgent(CONFIG, store, FailingLLM(402))
    assert agent.fallback_llm is None
    conv = agent.start_conversation().conversation_id
    with pytest.raises(LLMError) as e:
        agent.handle_employee_message(conv, "שכר בסיס 14,200")
    assert e.value.status == 402


def test_fallback_is_opt_in_via_env(store, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK", "rules")
    assert isinstance(AdvisorAgent(CONFIG, store, FailingLLM()).fallback_llm, RuleBasedLLM)


def test_quota_error_falls_back_to_deterministic_brain(store):
    primary = FailingLLM(429)
    agent = AdvisorAgent(CONFIG, store, primary, fallback_llm=RuleBasedLLM(CONFIG))
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "שכר בסיס 14,200 קבוצה ג'")
    assert reply.llm_fallback and "אינו זמין" in reply.notice
    assert reply.state.profile.base_salary == 14200       # the turn still worked
    assert "פנסיה" in reply.text
    # cooldown: the next turn does not hit the exhausted provider again
    calls = primary.calls
    reply2 = agent.handle_employee_message(conv, "פנסיה 6%, השתלמות 2.5%, 2.25 נקודות זיכוי")
    assert primary.calls == calls and reply2.llm_fallback
    assert reply2.state.profile.credit_points == 2.25


def test_primary_retried_after_cooldown(store):
    primary = FailingLLM(429)
    agent = AdvisorAgent(CONFIG, store, primary, fallback_llm=RuleBasedLLM(CONFIG),
                         fallback_cooldown_s=0)
    conv = agent.start_conversation().conversation_id
    agent.handle_employee_message(conv, "שכר בסיס 14,200")
    agent.handle_employee_message(conv, "קבוצה ג'")
    assert primary.calls == 2  # cooldown 0 -> primary tried on every turn


def test_failure_mid_turn_keeps_tools_already_run(store):
    """Primary saves data, then fails on the follow-up request: fallback finishes the turn."""
    calls = [ToolCall("c1", "update_profile", {"base_salary": 14200, "eligibility_group": "ג"})]
    primary = FailingLLM(503, succeed_first=[LLMStep("", calls, assistant_message("", calls))])
    agent = AdvisorAgent(CONFIG, store, primary, fallback_llm=RuleBasedLLM(CONFIG))
    conv = agent.start_conversation().conversation_id
    reply = agent.handle_employee_message(conv, "שכר בסיס 14,200 קבוצה ג'")
    assert reply.llm_fallback
    assert reply.tools_called == ["update_profile"]     # not run twice
    assert reply.state.profile.eligibility_group == "ג"
    assert reply.text.startswith("תודה, עדכנתי")


def test_non_transient_error_falls_back_without_cooldown(store):
    primary = FailingLLM(400)
    agent = AdvisorAgent(CONFIG, store, primary, fallback_llm=RuleBasedLLM(CONFIG))
    conv = agent.start_conversation().conversation_id
    agent.handle_employee_message(conv, "שכר בסיס 14,200")
    agent.handle_employee_message(conv, "קבוצה ג'")
    assert primary.calls == 2  # a bad request is not a reason to stop trying


def test_fallback_disabled_propagates_error(store):
    agent = AdvisorAgent(CONFIG, store, FailingLLM(429), fallback_llm=None)
    conv = agent.start_conversation().conversation_id
    with pytest.raises(LLMError):
        agent.handle_employee_message(conv, "שכר בסיס 14,200")
    assert store.load(conv).messages == []  # nothing half-saved


def test_rule_based_primary_has_no_fallback(store):
    agent = AdvisorAgent(CONFIG, store, RuleBasedLLM(CONFIG))
    assert agent.fallback_llm is None


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
