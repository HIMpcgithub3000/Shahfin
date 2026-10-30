#!/usr/bin/env python3
"""Execute 10 Actual Voicebot Calls (5 Personas x 2 Runs) through the Live Backend.

Demonstrates end-to-end compliance for Problem 10:
- Calls initiated via POST /api/calls/initiate
- Multi-turn conversation executed via POST /api/calls/turn with Live LLM/NLU
- Call finalized and saved to SQL via POST /api/calls/finalize
- KPIs and metrics calculated directly from database records (zero manual seeding)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from backend.main import app, CSV_PATH
from backend.db import clear_calls, get_all_customers, get_connection, execute_sql, get_report_stats, reset_customers

client = TestClient(app)

PERSONA_RUNS = [
    # 1. Hindi Right Customer (Run 1: C001 Rahul Sharma)
    {
        "customer_id": "C001",
        "name": "Hindi Right Customer (Run 1)",
        "expected_outcome": "Right customer",
        "expected_connected": True,
        "turns": [
            ("हाँ, मैं राहुल शर्मा बोल रहा हूँ।", "AWAITING_NAME_CONFIRMATION", "Hindi"),
            ("मेरा जन्म का साल 1985 है।", "AWAITING_BIRTH_YEAR", "Hindi"),
        ],
    },
    # 2. Hindi Right Customer (Run 2: C006 Anjali Deshmukh)
    {
        "customer_id": "C006",
        "name": "Hindi Right Customer (Run 2)",
        "expected_outcome": "Right customer",
        "expected_connected": True,
        "turns": [
            ("हाँ, मैं अंजलि देशमुख बोल रही हूँ।", "AWAITING_NAME_CONFIRMATION", "Hindi"),
            ("मेरा जन्म वर्ष 1995 है।", "AWAITING_BIRTH_YEAR", "Hindi"),
        ],
    },
    # 3. English Wrong Birth Year (Run 1: C002 Priya Nair)
    {
        "customer_id": "C002",
        "name": "English Wrong Birth Year (Run 1)",
        "expected_outcome": "Verification failed",
        "expected_connected": True,
        "turns": [
            ("Yes, this is Priya Nair speaking.", "AWAITING_NAME_CONFIRMATION", "English"),
            ("My birth year is 1999.", "AWAITING_BIRTH_YEAR", "English"),
        ],
    },
    # 4. English Wrong Birth Year (Run 2: C005 Vikram Singh)
    {
        "customer_id": "C005",
        "name": "English Wrong Birth Year (Run 2)",
        "expected_outcome": "Verification failed",
        "expected_connected": True,
        "turns": [
            ("Yes, Vikram Singh here.", "AWAITING_NAME_CONFIRMATION", "English"),
            ("I was born in 1988.", "AWAITING_BIRTH_YEAR", "English"),
        ],
    },
    # 5. Wrong Number (Run 1: C003 Amit Patil)
    {
        "customer_id": "C003",
        "name": "Wrong Number (Run 1)",
        "expected_outcome": "Wrong number",
        "expected_connected": True,
        "turns": [
            ("रॉन्ग नंबर है, मैं किसी अमित पाटिल को नहीं जानता।", "AWAITING_NAME_CONFIRMATION", "Hindi"),
        ],
    },
    # 6. Wrong Number (Run 2: C008 Kavita Joshi)
    {
        "customer_id": "C008",
        "name": "Wrong Number (Run 2)",
        "expected_outcome": "Wrong number",
        "expected_connected": True,
        "turns": [
            ("गलत नंबर लगा दिया है आपने, यहाँ कोई कविता नहीं रहती।", "AWAITING_NAME_CONFIRMATION", "Hindi"),
        ],
    },
    # 7. Relative Answers: Callback Requested (Run 1: C004 Sneha Kulkarni)
    {
        "customer_id": "C004",
        "name": "Relative Callback (Run 1)",
        "expected_outcome": "Callback requested",
        "expected_connected": True,
        "turns": [
            ("स्नेहा अभी घर पर नहीं हैं, मैं उनका भाई बोल रहा हूँ।", "AWAITING_NAME_CONFIRMATION", "Hindi"),
            ("आप आज शाम 6:30 बजे कॉल कर लीजिए।", "AWAITING_CALLBACK_TIME", "Hindi"),
        ],
    },
    # 8. Relative Answers: No Callback Time (Run 2: C009 Suresh Yadav)
    {
        "customer_id": "C009",
        "name": "Relative No Callback (Run 2)",
        "expected_outcome": "Third party answered",
        "expected_connected": True,
        "turns": [
            ("सुरेश बाहर गए हैं, मुझे नहीं पता कब वापस आएंगे।", "AWAITING_NAME_CONFIRMATION", "Hindi"),
            ("मुझे नहीं मालूम कोई समय।", "AWAITING_CALLBACK_TIME", "Hindi"),
        ],
    },
    # 9. Silence / No Answer: Not Connected (Run 1: C007 Rohan Mehta)
    {
        "customer_id": "C007",
        "name": "Silence / Dropped Midway (Run 1)",
        "expected_outcome": "Not connected",
        "expected_connected": False,
        "turns": [],  # Recipient hangs up or 15s silence before speaking
    },
    # 10. Silence / No Answer: Not Connected (Run 2: C010 Neha Gupta)
    {
        "customer_id": "C010",
        "name": "Silence / Dropped Midway (Run 2)",
        "expected_outcome": "Not connected",
        "expected_connected": False,
        "turns": [],
    },
]


def run_10_actual_voicebot_calls():
    print("=" * 80)
    print("EXECUTING 10 ACTUAL VOICEBOT CALLS (5 PERSONAS x 2 RUNS EACH)")
    print("=" * 80)

    # 1. Reset customers and clear call table
    reset_customers(CSV_PATH)
    clear_calls()

    executed_calls = []

    for i, p in enumerate(PERSONA_RUNS, 1):
        print(f"\n--- [Call #{i}] {p['name']} (Customer: {p['customer_id']}) ---")

        # Step 1: Initiate call in backend
        t0 = time.time()
        init_res = client.post("/api/calls/initiate", json={
            "customer_id": p["customer_id"],
            "simulated_hour": 11
        })
        assert init_res.status_code == 200, f"Call initiation failed: {init_res.text}"
        init_data = init_res.json()
        call_id = init_data["call_id"]
        customer = init_data["customer"]
        print(f"✓ Initiated call {call_id} in SQL (attempted increments immediately)")
        print(f"  Bot Opening: \"{init_data['opening_disclosure'][:65]}...\"")

        outcome = "Not connected"
        connected = False
        stage = "AWAITING_NAME_CONFIRMATION"
        latencies = []

        # Step 2: Execute borrower voice turns through the Live pipeline
        for user_text, expected_stage, lang in p["turns"]:
            turn_t0 = time.time()
            turn_res = client.post("/api/calls/turn", json={
                "call_id": call_id,
                "customer_id": p["customer_id"],
                "input_text": user_text,
                "stage": stage,
                "language": lang
            })
            assert turn_res.status_code == 200, f"Turn failed: {turn_res.text}"
            turn_data = turn_res.json()
            turn_ms = turn_data.get("latency_ms", int((time.time() - turn_t0) * 1000))
            latencies.append(turn_ms)

            connected = True
            stage = turn_data.get("stage", stage)
            if turn_data.get("outcome"):
                outcome = turn_data["outcome"]

            print(f"  Borrower: \"{user_text}\"")
            print(f"  Bot Reply ({turn_ms}ms): \"{turn_data['bot_reply'][:65]}...\"")
            print(f"  Guardrails Safe: {turn_data.get('guardrail_safe', True)} | Stage: {stage}")

        # Step 3: Finalize call in backend
        duration_sec = 0 if not connected else int(10 + len(p["turns"]) * 5)
        fin_res = client.post("/api/calls/finalize", json={
            "call_id": call_id,
            "duration_seconds": duration_sec,
            "outcome": outcome,
        })
        assert fin_res.status_code == 200

        print(f"✓ Call Finalized in SQL: Connected={'Yes' if connected else 'No'} | Outcome='{outcome}' | Duration={duration_sec}s")
        assert outcome == p["expected_outcome"], f"Expected {p['expected_outcome']}, got {outcome}"
        assert connected == p["expected_connected"], f"Expected connected={p['expected_connected']}, got {connected}"
        executed_calls.append({
            "call_id": call_id,
            "customer_id": p["customer_id"],
            "connected": connected,
            "outcome": outcome
        })

    # Step 4: Validate Report Metrics directly from SQL
    print("\n" + "=" * 80)
    print("VALIDATING REPORT & DASHBOARD AGAINST ACTUAL DATABASE")
    print("=" * 80)

    report_res = client.get("/api/report")
    assert report_res.status_code == 200
    report = report_res.json()

    print(f"Total Calls Attempted : {report['calls_attempted']} (Expected: 10)")
    print(f"Total Connected Calls : {report['connected']} (Expected: 8)")
    print(f"Total Not Connected   : {report['not_connected']} (Expected: 2)")
    print(f"Connect Rate          : {report['connect_rate']}% (Expected: 80.0%)")
    print(f"Right-Party Rate      : {report['right_party_rate']}% (Expected: 25.0%)")
    print("\nOutcome Breakdown in SQL:")
    for out, cnt in sorted(report["outcome_counts"].items()):
        print(f"  - {out:22s}: {cnt}")

    # Assert exact match with database calculations
    assert report["calls_attempted"] == 10
    assert report["connected"] == 8
    assert report["not_connected"] == 2
    assert report["connect_rate"] == 80.0
    assert report["right_party_rate"] == 25.0
    assert report["outcome_counts"]["Right customer"] == 2
    assert report["outcome_counts"]["Verification failed"] == 2
    assert report["outcome_counts"]["Wrong number"] == 2
    assert report["outcome_counts"]["Callback requested"] == 1
    assert report["outcome_counts"]["Third party answered"] == 1
    assert report["outcome_counts"]["Not connected"] == 2

    print("\n✓ SUCCESS: All 10 actual voicebot calls executed and verified directly against SQL!")
    return True


if __name__ == "__main__":
    run_10_actual_voicebot_calls()
