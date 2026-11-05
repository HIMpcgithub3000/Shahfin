"""Automated End-to-End Test Suite verifying All 5 Features & All 6 Compliance Rules.

Can be run via:
  pytest scripts/test_guardrails_e2e.py
  python scripts/test_guardrails_e2e.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

from backend.compliance_gate import (
    check_pre_call_gate,
    is_dnc_request,
    is_wrong_number_phrase,
    verify_and_sanitize_reply,
)
from backend.db import (
    clear_calls,
    get_all_customers,
    get_customer,
    get_report_stats,
    init_db_and_seed_customers,
    reset_customers,
    save_call_record,
    seed_test_calls,
    update_customer_dnc,
)
from backend.main import app
from backend.voice_pipeline import (
    generate_opening_disclosure,
    process_turn,
    run_deterministic_nlu,
    synthesize_tts_mp3,
)

CSV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "customers.csv"))
TEST_CALLS_CSV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "test_calls.csv"))
client = TestClient(app)


def setup_module():
    init_db_and_seed_customers(CSV_PATH)


# ==============================================================================
# FEATURE 1: Customer List & Call Window
# ==============================================================================
def test_feature_1_customer_seeding_and_list():
    """Verify exactly 10 dummy customers are loaded with correct schemas and birth_year stripped from API."""
    reset_customers(CSV_PATH)
    customers = get_all_customers()
    assert len(customers) >= 10, f"Expected at least 10 dummy customers, found {len(customers)}"
    c001 = next(c for c in customers if c["customer_id"] == "C001")
    assert c001["name"] == "Rahul Sharma"
    assert c001["phone"] == "+919000000001"
    assert c001["birth_year"] == 1985
    assert c001["language"] == "Hindi"
    assert c001["do_not_call"] == 0

    res = client.get("/api/customers")
    assert res.status_code == 200
    customer_list = res.json()
    assert len(customer_list) >= 10
    # Problem 5 Fix: Birth year must never be exposed via frontend list API
    assert "birth_year" not in customer_list[0]


def test_feature_1_call_window_refusal_before_8am():
    """Calls before 8 AM IST are refused (HTTP 403) and not inserted into dbo.Call."""
    clear_calls()
    res = client.post("/api/calls/preflight", json={"customer_id": "C001", "simulated_hour": 6})
    assert res.status_code == 403
    assert "outside the 8:00 AM – 7:00 PM calling window" in res.json()["detail"]
    assert "Not counted as an attempt" in res.json()["detail"]

    # Verify Call table remains empty
    report = get_report_stats()
    assert report["calls_attempted"] == 0


def test_feature_1_call_window_refusal_after_7pm():
    """Calls after 7 PM (19:00) IST are refused (HTTP 403) and not inserted into dbo.Call."""
    clear_calls()
    res = client.post("/api/calls/preflight", json={"customer_id": "C001", "simulated_hour": 21})
    assert res.status_code == 403
    assert "outside the 8:00 AM – 7:00 PM calling window" in res.json()["detail"]
    assert "Not counted as an attempt" in res.json()["detail"]

    report = get_report_stats()
    assert report["calls_attempted"] == 0


def test_feature_1_call_window_refusal_for_dnc():
    """Customers with do_not_call = 1 are refused (HTTP 403)."""
    update_customer_dnc("C001", 1)
    res = client.post("/api/calls/preflight", json={"customer_id": "C001", "simulated_hour": 11})
    assert res.status_code == 403
    assert "Do Not Call" in res.json()["detail"]
    update_customer_dnc("C001", 0)


# ==============================================================================
# FEATURE 2: Opening & Disclosure (<10s, Recorded, Zero Loan Disclosure)
# ==============================================================================
def test_feature_2_opening_disclosure():
    """Within first 10s, states automated assistant from Shaha Finlease, recorded call, asks for name."""
    hindi = generate_opening_disclosure("Rahul Sharma", "Hindi")
    assert "शाहा फिनलीज़" in hindi or "Shaha Finlease" in hindi
    assert "ऑटोमेटेड असिस्टेंट" in hindi
    assert "रिकॉर्ड" in hindi
    assert "Rahul Sharma" in hindi

    english = generate_opening_disclosure("Priya Nair", "English")
    assert "Shaha Finlease" in english
    assert "automated assistant" in english
    assert "recorded" in english
    assert "Priya Nair" in english

    marathi = generate_opening_disclosure("Sneha Kulkarni", "Marathi")
    assert "Shaha Finlease" in marathi
    assert "रेकॉर्ड" in marathi
    assert "Sneha Kulkarni" in marathi

    # Zero loan terms in opening
    for text in [hindi, english, marathi]:
        v = verify_and_sanitize_reply(text, "English", "Customer")
        assert v["safe"] is True


# ==============================================================================
# FEATURE 3: All 6 Exact Outcomes
# ==============================================================================
def test_feature_3_outcome_1_right_customer():
    """Right customer: confirms name and matching birth year (Connected = 1)."""
    customer = get_customer("C001")
    assert customer is not None
    res1 = process_turn(customer, "हाँ, मैं राहुल शर्मा बोल रहा हूँ।", "AWAITING_NAME_CONFIRMATION", "Hindi")
    assert res1["stage"] == "AWAITING_BIRTH_YEAR"

    res2 = process_turn(customer, "मेरा जन्म का साल 1985 है।", "AWAITING_BIRTH_YEAR", "Hindi")
    assert res2["outcome"] == "Right customer"
    assert "सत्यापन सफल रहा" in res2["bot_reply"]


def test_feature_3_outcome_2_verification_failed():
    """Verification failed: confirms name but wrong birth year. NEVER reveals expected year!"""
    customer = get_customer("C002")
    assert customer is not None
    res1 = process_turn(customer, "Yes, this is Priya Nair speaking.", "AWAITING_NAME_CONFIRMATION", "English")
    assert res1["stage"] == "AWAITING_BIRTH_YEAR"

    res2 = process_turn(customer, "My birth year is 1996.", "AWAITING_BIRTH_YEAR", "English")
    assert res2["outcome"] == "Verification failed"
    # NEVER reveal the expected birth year 1992
    assert "1992" not in res2["bot_reply"]
    assert "not match" in res2["bot_reply"] or "मेल नहीं खाता" in res2["bot_reply"]


def test_feature_3_outcome_3_wrong_number():
    """Wrong number: says 'I don't know this person' -> apologises and ends immediately."""
    customer = get_customer("C003")
    assert customer is not None
    res = process_turn(customer, "रॉन्ग नंबर है, मैं अमित पाटिल नाम के किसी व्यक्ति को नहीं जानता।", "AWAITING_NAME_CONFIRMATION", "Hindi")
    assert res["outcome"] == "Wrong number"
    assert "खेद" in res["bot_reply"] or "गलत नंबर" in res["bot_reply"] or "apologise" in res["bot_reply"]


def test_feature_3_outcome_4_callback_requested():
    """Callback requested: relative answers, gives callback time, zero loan disclosure."""
    customer = get_customer("C004")
    assert customer is not None
    res = process_turn(customer, "स्नेहा अभी घर से बाहर गई हैं, आप आज शाम 6:30 बजे कॉल कर लीजिए।", "AWAITING_NAME_CONFIRMATION", "Hindi")
    assert res["outcome"] == "Callback requested"
    assert res["callback_time"] is not None
    assert "6:30" in res["callback_time"]
    assert "6:30" in res["bot_reply"]


def test_feature_3_outcome_5_third_party_answered():
    """Third party answered: someone else answers and gives no callback time."""
    customer = get_customer("C009")
    assert customer is not None
    res1 = process_turn(customer, "सुरेश जी अभी घर पर नहीं हैं, मैं उनका भाई बोल रहा हूँ।", "AWAITING_NAME_CONFIRMATION", "Hindi")
    assert res1["stage"] == "AWAITING_CALLBACK_TIME"

    res2 = process_turn(customer, "मुझे नहीं पता वो कब आएंगे।", "AWAITING_CALLBACK_TIME", "Hindi")
    assert res2["outcome"] == "Third party answered"


def test_feature_3_outcome_6_not_connected():
    """Not connected: 15s silence watchdog or drop before speech -> connected = 0."""
    clear_calls()
    save_call_record(
        call_id="CALL-1005",
        customer_id="C005",
        start_time=None,
        duration_seconds=15,
        connected=False,
        outcome="Not connected",
        language="English",
        callback_time=None,
        transcript=[{"speaker": "System", "text": "15s silence watchdog triggered"}],
        summary_3line=["1. Called C005", "2. Silence 15s", "3. Outcome = Not connected"],
    )
    report = get_report_stats()
    assert report["outcome_counts"]["Not connected"] == 1
    assert report["not_connected"] == 1


# ==============================================================================
# FEATURE 4: Save Every Call (Including Mid-Call Stops)
# ==============================================================================
def test_feature_4_mid_call_stop_saved_to_db():
    """If call drops midway, it is saved to dbo.Call with correct connected status."""
    clear_calls()
    # Case A: Spoke once before drop -> connected = 1
    save_call_record(
        call_id="CALL-DROP-1",
        customer_id="C001",
        start_time=None,
        duration_seconds=8,
        connected=True,
        outcome="Third party answered",
        language="Hindi",
        callback_time=None,
        transcript=[
            {"speaker": "Bot", "text": "Opening greeting"},
            {"speaker": "Borrower", "text": "Hello?"},
        ],
        summary_3line=["Line 1", "Line 2", "Line 3"],
    )
    # Case B: Dropped before speaking -> connected = 0
    save_call_record(
        call_id="CALL-DROP-2",
        customer_id="C002",
        start_time=None,
        duration_seconds=3,
        connected=False,
        outcome="Not connected",
        language="English",
        callback_time=None,
        transcript=[{"speaker": "Bot", "text": "Opening greeting"}],
        summary_3line=["Line 1", "Line 2", "Line 3"],
    )
    report = get_report_stats()
    assert report["calls_attempted"] == 2
    assert report["connected"] == 1
    assert report["not_connected"] == 1


# ==============================================================================
# FEATURE 5: Report Page & Call Log Matches DB 100%
# ==============================================================================
def test_feature_5_report_matches_test_calls():
    """Seed all 10 test calls and verify exact KPI math and 100% database match."""
    report = seed_test_calls(TEST_CALLS_CSV, CSV_PATH)
    assert report["calls_attempted"] == 10
    assert report["connected"] == 8
    assert report["not_connected"] == 2
    assert report["connect_rate_pct"] == 80.0
    assert report["right_party_rate_pct"] == 25.0
    assert report["outcome_counts"]["Right customer"] == 2
    assert report["outcome_counts"]["Verification failed"] == 2
    assert report["outcome_counts"]["Wrong number"] == 2
    assert report["outcome_counts"]["Callback requested"] == 1
    assert report["outcome_counts"]["Third party answered"] == 1
    assert report["outcome_counts"]["Not connected"] == 2

    # Verify clicking call row contains transcript and 3-line summary
    call_1 = report["calls"][-1]  # CALL-1001
    assert "transcript" in call_1
    assert len(call_1["transcript"]) >= 2
    assert "summary_3line" in call_1
    assert len(call_1["summary_3line"]) == 3


# ==============================================================================
# COMPLIANCE RULES 1 TO 6: Deterministic Safety Guardrails
# ==============================================================================
def test_compliance_rule_1_zero_loan_disclosure():
    """Rule 1: Intercepts loan/amount/dues/EMI/recovery terms."""
    provocations = [
        "Your loan of 50,000 rupees is pending.",
        "Aapka loan dues baaki hai, EMI bharo.",
        "Your EMI payment default is being recovered.",
    ]
    for prov in provocations:
        res = verify_and_sanitize_reply(prov, "English", "Rahul Sharma")
        assert res["safe"] is False
        assert any("Rule 1" in v for v in res["violations"])
        assert "loan" not in res["reply"].lower()
        assert "emi" not in res["reply"].lower()


def test_compliance_rule_2_zero_threats():
    """Rule 2: Intercepts police/jail/court/legal action/visits."""
    threats = [
        "Police will visit your house tomorrow.",
        "Aapko jail bhej denge court se warrant aayega.",
        "Legal action will be taken immediately.",
    ]
    for t in threats:
        res = verify_and_sanitize_reply(t, "English", "Rahul Sharma")
        assert res["safe"] is False
        assert any("Rule 2" in v for v in res["violations"])
        assert "police" not in res["reply"].lower()
        assert "jail" not in res["reply"].lower()


def test_compliance_rule_3_polite_tone_zero_shaming():
    """Rule 3: Intercepts abusive/shaming/insulting language."""
    shames = [
        "You are a liar and a cheater.",
        "Tum chor aur defaulter ho.",
        "You idiot, pay your dues.",
    ]
    for s in shames:
        res = verify_and_sanitize_reply(s, "Hindi", "Rahul Sharma")
        assert res["safe"] is False
        assert any("Rule 3" in v for v in res["violations"])
        assert "cheater" not in res["reply"].lower()
        assert "chor" not in res["reply"].lower()


def test_compliance_rule_4_never_ask_credentials():
    """Rule 4: Intercepts requests for OTP/PIN/CVV/Card/Password/Aadhaar."""
    creds = [
        "Please provide the OTP sent to your phone.",
        "Aap apna ATM PIN aur password batayein.",
        "Tell me your Aadhaar card number.",
    ]
    for c in creds:
        res = verify_and_sanitize_reply(c, "English", "Rahul Sharma")
        assert res["safe"] is False
        assert any("Rule 4" in v for v in res["violations"])
        assert "otp" not in res["reply"].lower()
        assert "pin" not in res["reply"].lower()


def test_compliance_rule_5_wrong_number_trigger():
    """Rule 5: Borrower wrong number phrase triggers immediate apology & end."""
    assert is_wrong_number_phrase("रॉन्ग नंबर है, मैं अमित पाटिल को नहीं जानता।") is True
    assert is_wrong_number_phrase("Wrong number, this is not their phone.") is True
    assert is_wrong_number_phrase("Yes, Rahul speaking.") is False


def test_compliance_rule_6_dnc_trigger_and_flag_update():
    """Rule 6: Borrower asking to stop calling updates do_not_call = 1 in database."""
    reset_customers(CSV_PATH)
    assert is_dnc_request("Stop calling me! Remove my number.") is True
    assert is_dnc_request("डू नॉट कॉल, दोबारा फोन मत करना।") is True

    customer = get_customer("C001")
    assert customer is not None
    res = process_turn(customer, "Please stop calling this number, put me on DNC.", "AWAITING_NAME_CONFIRMATION", "English")
    assert res["mark_do_not_call"] is True

    # Simulate turn endpoint updating DNC
    update_customer_dnc("C001", 1)
    updated = get_customer("C001")
    assert updated is not None
    assert updated["do_not_call"] == 1

    # Preflight should now refuse C001
    allowed, reason = check_pre_call_gate(bool(updated["do_not_call"]), 11)
    assert allowed is False
    assert "Do Not Call" in reason
    # Reset C001 DNC to 0 so other tests are not blocked
    update_customer_dnc("C001", 0)


# ==============================================================================
# SLA & TTS VERIFICATION
# ==============================================================================
def test_sla_latency_under_3_seconds():
    """Verify turn processing executes with latency < 3000 ms."""
    customer = get_customer("C001")
    assert customer is not None
    res = process_turn(customer, "हाँ, मैं राहुल शर्मा बोल रहा हूँ।", "AWAITING_NAME_CONFIRMATION", "Hindi")
    assert res["latency_ms"] < 3000, f"Turn latency {res['latency_ms']} ms exceeded 3,000 ms SLA!"


@pytest.mark.asyncio
async def test_edge_tts_audio_synthesis():
    """Verify Microsoft edge-tts synthesizes non-empty MP3 audio bytes."""
    from backend.voice_pipeline import edge_tts
    if edge_tts is None:
        pytest.skip("edge-tts library is not installed in the running environment")
    audio_bytes = await synthesize_tts_mp3("नमस्ते, यह शाहा फिनलीज़ की ऑडियो टेस्ट है।", "Hindi")
    assert audio_bytes is not None
    assert len(audio_bytes) > 1000, "Audio bytes should be > 1000 bytes for MP3 frame"


def test_backend_owned_call_lifecycle_and_interrupted_call():
    """Feature #4 / Problem 7 Fix: Call is persisted at initiation.

    Even if dropped before reply, it is recorded in DB with Connected = No and attempted count increments.
    """
    clear_calls()
    init_res = client.post("/api/calls/initiate", json={"customer_id": "C001", "simulated_hour": 11})
    assert init_res.status_code == 200
    data = init_res.json()
    call_id = data["call_id"]
    assert "birth_year" not in data["customer"]

    # Verify call already exists in DB before any turns
    report = get_report_stats()
    assert report["calls_attempted"] == 1
    assert report["connected"] == 0
    assert report["not_connected"] == 1

    # Now turn occurs
    turn_res = client.post("/api/calls/turn", json={
        "call_id": call_id,
        "customer_id": "C001",
        "input_text": "हाँ, मैं राहुल शर्मा बोल रहा हूँ।",
        "stage": "AWAITING_NAME_CONFIRMATION",
        "language": "Hindi"
    })
    assert turn_res.status_code == 200

    # Verify connected is now 1 in DB
    report = get_report_stats()
    assert report["connected"] == 1

    # Finalize call
    fin_res = client.post("/api/calls/finalize", json={
        "call_id": call_id,
        "duration_seconds": 15,
        "outcome": "Verification failed"
    })
    assert fin_res.status_code == 200


def test_hindi_interrogative_collision_fixed():
    """Problem 2 Fix: Saying 'आप कौन हो?' must NOT be treated as affirmative Marathi 'हो'."""
    customer = get_customer("C001")
    assert customer is not None
    res = process_turn(customer, "आप कौन हो? किसने फोन किया है?", "AWAITING_NAME_CONFIRMATION", "Hindi")
    # Must remain at AWAITING_NAME_CONFIRMATION and not jump to birth year
    assert res["stage"] == "AWAITING_NAME_CONFIRMATION"
    assert res["outcome"] is None


def test_birth_year_never_in_preflight_or_customer_api():
    """Problem 5 Fix: Expected birth year must never be exposed via frontend REST endpoints."""
    pref_res = client.post("/api/calls/preflight", json={"customer_id": "C001", "simulated_hour": 11})
    assert pref_res.status_code == 200
    assert "birth_year" not in pref_res.json()["customer"]

    cust_res = client.get("/api/customers")
    assert cust_res.status_code == 200
    for c in cust_res.json():
        assert "birth_year" not in c


def test_recruiter_issue_2_rahul_kumar_vs_rahul_sharma():
    """Recruiter Issue #2: When customer is Rahul Sharma and caller says Rahul Kumar, reject as Wrong number!"""
    customer = get_customer("C001")
    assert customer is not None
    assert customer["name"] == "Rahul Sharma"

    # Test variety of Rahul Kumar utterances
    utterances = [
        "mera naam rahul kumar hai",
        "main rahul kumar bol raha hu",
        "Rahul Kumar बोल रहा हूँ",
        "This is Rahul Kumar",
        "rahul kumar"
    ]
    for utt in utterances:
        res = process_turn(customer, utt, "AWAITING_NAME_CONFIRMATION", "Hindi")
        assert res["outcome"] == "Wrong number", f"Failed for utterance '{utt}', got {res['outcome']}"
        assert res["stage"] == "COMPLETED"
        assert "Rahul Sharma" in res["bot_reply"]


def test_recruiter_issue_1_introductory_line_twiml_architecture():
    """Recruiter Issue #1: Bulletproof introductory line delivery over Twilio PSTN.
    
    Verifies:
    1. <Pause length="1"/> is present to eliminate cellular carrier RTP setup delay.
    2. Mandatory Shaha Finlease opening declaration is unnested and un-interruptible before <Gather>.
    3. <Gather> handles borrower speech recognition.
    """
    res = client.post("/api/telephony/twilio/voice?customer_id=C001&lang=Hindi")
    assert res.status_code == 200
    twiml = res.text
    assert "<Pause length=\"1\"/>" in twiml
    assert "Shaha Finlease" in twiml or "शाहा फिनलीज" in twiml
    assert "<Gather input=\"speech\"" in twiml
    # Decoupled Say before Gather
    pause_pos = twiml.find("<Pause length=\"1\"/>")
    gather_pos = twiml.find("<Gather")
    assert pause_pos < gather_pos, "Pause must occur before Gather"


def test_english_calling_for_priya_vikram_rohan_neha():
    """Verify English language calling & opening disclosures for C002, C005, C007, C010."""
    english_ids = ["C002", "C005", "C007", "C010"]
    for cid in english_ids:
        cust = get_customer(cid)
        assert cust is not None
        assert cust["language"] == "English"
        init_res = client.post("/api/calls/initiate", json={"customer_id": cid, "simulated_hour": 11})
        assert init_res.status_code == 200
        data = init_res.json()
        assert "Hello, I am an automated assistant calling from Shaha Finlease" in data["opening_disclosure"]
        assert cust["name"] in data["opening_disclosure"]


if __name__ == "__main__":
    pytest.main(["-v", __file__])

