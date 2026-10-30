"""Automated Test Suite for Shaha Finlease Assignment Bonus Features:
1. Native Marathi (मराठी) support with Sarvam AI & resilient Edge-TTS fallback.
2. Dynamic mid-call language switching (Hindi <-> English <-> Marathi).
3. Saving complete call audio to disk and streaming via GET /api/calls/{call_id}/audio.
4. Strict callback_time_iso (HH:MM:SS) extraction and database persistence.
5. Real telephony integration via Twilio/Exotel webhooks and TwiML voice responses.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from backend.main import app
from backend.db import get_customer, get_call, get_report_stats, clear_calls, update_customer_dnc
from backend.voice_pipeline import (
    process_turn,
    run_deterministic_nlu,
    extract_callback_time_and_iso,
    detect_mid_call_language_switch,
    trip_sarvam_circuit,
    is_sarvam_circuit_open,
    generate_and_save_full_call_audio,
)

client = TestClient(app)


def test_bonus_1_sarvam_fallback_and_marathi_flow():
    """Bonus 1: Verify Marathi RPV conversation flow and Sarvam circuit-breaker fallback."""
    # Force trip the Sarvam circuit breaker to test resilient fallback
    trip_sarvam_circuit(60.0, "Forced test trip")
    assert is_sarvam_circuit_open() is True

    customer = get_customer("C003")  # Amit Patil
    # Turn 1: Borrower answers in Marathi affirmatively ("होय, मी अमित पाटील बोलतोय")
    res1 = process_turn(customer, "होय, मी अमित पाटील बोलतोय", "AWAITING_NAME_CONFIRMATION", "Marathi")
    assert res1["detected_language"] == "Marathi"
    assert res1["stage"] == "AWAITING_BIRTH_YEAR"
    assert ("जन्म" in res1["bot_reply"] and "वर्ष" in res1["bot_reply"]) or "birth year" in res1["bot_reply"].lower()
    assert res1["guardrail_safe"] is True

    # Turn 2: Borrower provides correct birth year in Marathi Devanagari numerals
    res2 = process_turn(customer, "माझे जन्म वर्ष 1988 आहे", "AWAITING_BIRTH_YEAR", "Marathi")
    assert res2["outcome"] == "Right customer"
    assert res2["stage"] == "COMPLETED"
    assert "यशस्वी" in res2["bot_reply"] or "verified" in res2["bot_reply"].lower() or "सत्यापन" in res2["bot_reply"] or "धन्यवाद" in res2["bot_reply"]
    assert res2["guardrail_safe"] is True


def test_bonus_2_mid_call_language_switching():
    """Bonus 2: Seamlessly switch language mid-dialogue upon borrower request."""
    customer = get_customer("C001")  # Rahul Sharma (Hindi by default)

    # 1. Detect explicit language switch intent
    assert detect_mid_call_language_switch("मला मराठीत बोला") == "Marathi"
    assert detect_mid_call_language_switch("Please switch to English") == "English"
    assert detect_mid_call_language_switch("हिंदी में बात करो") == "Hindi"

    # 2. Mid-call switch from Hindi opening to Marathi
    res_switch = process_turn(
        customer,
        "मला मराठीत बोला, मला हिंदी समजत नाही",
        "AWAITING_NAME_CONFIRMATION",
        "Hindi"
    )
    assert res_switch["detected_language"] == "Marathi"
    assert res_switch["language_switched"] is True
    assert "मराठीत बोलेन" in res_switch["bot_reply"] or "Shaha Finlease" in res_switch["bot_reply"]
    # Stage should remain ready for name confirmation in Marathi
    assert res_switch["stage"] == "AWAITING_NAME_CONFIRMATION"

    # 3. Mid-call switch from Marathi to English at birth year stage
    res_switch_en = process_turn(
        customer,
        "Can we please continue in English?",
        "AWAITING_BIRTH_YEAR",
        "Marathi"
    )
    assert res_switch_en["detected_language"] == "English"
    assert res_switch_en["language_switched"] is True
    assert "English" in res_switch_en["bot_reply"]
    assert "birth" in res_switch_en["bot_reply"].lower()
    assert res_switch_en["stage"] == "AWAITING_BIRTH_YEAR"


@pytest.mark.asyncio
async def test_bonus_3_audio_recording_saved_and_streamed():
    """Bonus 3: Verify complete call audio is generated, persisted to disk, and streamable via API."""
    call_id = "CALL-TEST-B3"
    transcript = [
        {"role": "assistant", "content": "नमस्ते, मैं Shaha Finlease से बोल रही हूँ। क्या मेरी बात राहुल शर्मा जी से हो रही है?"},
        {"role": "user", "content": "हाँ, मैं राहुल शर्मा बोल रहा हूँ।"},
        {"role": "assistant", "content": "धन्यवाद राहुल शर्मा जी। सत्यापन के लिए जन्म का वर्ष बताएं।"},
        {"role": "user", "content": "1992"},
        {"role": "assistant", "content": "सत्यापन सफल रहा। आपका दिन शुभ हो, नमस्ते।"},
    ]

    audio_path = await generate_and_save_full_call_audio(call_id, transcript, "Hindi")
    assert audio_path is not None
    assert os.path.exists(audio_path), f"Audio file not found at {audio_path}"
    assert os.path.getsize(audio_path) > 1000, "Audio file should contain valid MP3 bytes"

    # Finalize a call with this audio_path
    fin_res = client.post("/api/calls/finalize", json={
        "call_id": call_id,
        "customer_id": "C001",
        "duration_seconds": 22,
        "connected": True,
        "outcome": "Right customer",
        "language": "Hindi",
        "audio_path": audio_path,
        "transcript": transcript,
        "summary_3line": [
            "1. Verified identity with Rahul Sharma.",
            "2. Positive Right-Party Verification.",
            "3. Closed respectfully."
        ]
    })
    assert fin_res.status_code == 200

    # Stream the recording via GET /api/calls/{call_id}/audio
    audio_res = client.get(f"/api/calls/{call_id}/audio")
    assert audio_res.status_code == 200
    assert audio_res.headers["content-type"] in ("audio/mpeg", "audio/wav")
    assert len(audio_res.content) > 1000


def test_bonus_4_callback_time_strict_iso_field():
    """Bonus 4: Capture callback time as a strict ISO time (HH:MM:SS) in DB and reports."""
    # 1. Verify strict parser returns both display string and ISO format
    disp1, iso1 = extract_callback_time_and_iso("शाम को 6:30 बजे फोन करना")
    assert disp1 == "6:30 PM"
    assert iso1 == "18:30:00"

    disp2, iso2 = extract_callback_time_and_iso("दुपारी २ वाजता संपर्क करा")
    assert "2:00 PM" in disp2
    assert iso2 == "14:00:00"

    disp3, iso3 = extract_callback_time_and_iso("Please call back tomorrow at 11:00 AM")
    assert "11:00 AM" in disp3
    assert iso3 == "11:00:00"

    # 2. Test turn processing extracts callback_time_iso
    customer = get_customer("C001")
    turn_res = process_turn(
        customer,
        "राहुल घर पर नहीं हैं, आप शाम 6:30 बजे कॉल करना",
        "AWAITING_NAME_CONFIRMATION",
        "Hindi"
    )
    assert turn_res["outcome"] == "Callback requested"
    assert turn_res["callback_time"] == "6:30 PM"
    assert turn_res["callback_time_iso"] == "18:30:00"

    # 3. Save call record and verify in database and reports
    call_id = "CALL-TEST-B4"
    fin_res = client.post("/api/calls/save", json={
        "call_id": call_id,
        "customer_id": "C001",
        "start_time": "2026-10-31T03:00:00",
        "duration_seconds": 18,
        "connected": True,
        "outcome": "Callback requested",
        "language": "Hindi",
        "callback_time": "6:30 PM",
        "callback_time_iso": "18:30:00",
        "transcript": [{"role": "user", "content": "शाम 6:30"}],
        "summary_3line": ["1. Third party answered.", "2. Callback requested at 6:30 PM (ISO 18:30:00).", "3. Call ended."]
    })
    assert fin_res.status_code == 200

    record = get_call(call_id)
    assert record is not None
    assert record["callback_time_iso"] == "18:30:00"

    # Verify report stats includes the ISO callback time
    report = get_report_stats()
    matched_call = next((c for c in report["calls"] if c["call_id"] == call_id), None)
    assert matched_call is not None
    assert matched_call["callback_time_iso"] == "18:30:00"


def test_bonus_5_twilio_telephony_endpoints():
    """Bonus 5: Verify Twilio outbound and TwiML webhook endpoints."""
    # 1. Outbound call endpoint with unconfigured Twilio credentials gracefully returns simulation mode
    outbound_res = client.post("/api/telephony/outbound", json={
        "customer_id": "C001",
        "phone_number": "+919876543210"
    })
    assert outbound_res.status_code == 200
    out_data = outbound_res.json()
    assert "status" in out_data
    assert out_data["customer"]["name"] == "Rahul Sharma"

    # 2. Twilio incoming voice webhook (returns compliant TwiML XML with <Stream> or <Gather> and mandatory disclosure)
    voice_res = client.post("/api/telephony/twilio/voice?customer_id=C001&language=Hindi")
    assert voice_res.status_code == 200
    assert "application/xml" in voice_res.headers["content-type"]
    xml_text = voice_res.text
    assert "<Response>" in xml_text
    assert "<Stream" in xml_text or "<Gather" in xml_text

    # 3. Twilio turn webhook processing speech result
    turn_res = client.post(
        "/api/telephony/twilio/turn?customer_id=C001&stage=AWAITING_NAME_CONFIRMATION&language=Hindi",
        data={"SpeechResult": "हाँ मैं राहुल शर्मा बोल रहा हूँ"}
    )
    assert turn_res.status_code == 200
    assert "application/xml" in turn_res.headers["content-type"]
    turn_xml = turn_res.text
    assert "<Response>" in turn_xml
    assert "<Gather" in turn_xml
    # Should ask for birth year
    assert "जन्म" in turn_xml or "birth" in turn_xml.lower()
