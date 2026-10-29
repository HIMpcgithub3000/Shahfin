"""FastAPI Server for Shaha Finlease Right-Party Verification Voicebot.

Provides REST and Voice endpoints serving the Two-Screen Console & Report UI.
"""
import io
import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from backend.compliance_gate import check_pre_call_gate
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
from backend.voice_pipeline import (
    generate_opening_disclosure,
    process_turn,
    synthesize_tts_mp3,
    transcribe_groq_whisper,
)

from contextlib import asynccontextmanager

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CSV_PATH = os.path.join(BASE_DIR, "data", "customers.csv")
TEST_CALLS_CSV_PATH = os.path.join(BASE_DIR, "data", "test_calls.csv")
FRONTEND_PATH = os.path.join(BASE_DIR, "frontend", "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_and_seed_customers(CSV_PATH)
    yield


app = FastAPI(
    title="Shaha Finlease — Right-Party Verification Bot API",
    description="Automated pre-calling contact confirmation backend for RBI-registered NBFC.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PreflightRequest(BaseModel):
    customer_id: str
    simulated_hour: Optional[int] = None


class TurnRequest(BaseModel):
    customer_id: str
    input_text: str
    stage: str = "AWAITING_NAME_CONFIRMATION"
    language: str = "Hindi"


class SaveCallRequest(BaseModel):
    call_id: str
    customer_id: str
    start_time: Optional[str] = None
    duration_seconds: int
    connected: bool
    outcome: str
    language: str
    callback_time: Optional[str] = None
    transcript: List[Dict[str, Any]]
    summary_3line: List[str]
    mark_do_not_call: bool = False


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def serve_console_and_report() -> FileResponse:
    if os.path.exists(FRONTEND_PATH):
        return FileResponse(FRONTEND_PATH)
    # Check parent directory index.html if frontend/index.html is missing
    parent_index = os.path.join(BASE_DIR, "..", "index.html")
    if os.path.exists(parent_index):
        return FileResponse(parent_index)
    raise HTTPException(status_code=404, detail="index.html not found")


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    return {"status": "ok", "service": "Shaha Finlease Voicebot API", "version": "1.0.0"}


@app.get("/api/customers")
def list_customers() -> List[Dict[str, Any]]:
    return get_all_customers()


@app.post("/api/customers/reset")
def reset_all_customers() -> Dict[str, Any]:
    reset_customers(CSV_PATH)
    return {"status": "reset", "customers": get_all_customers()}


@app.post("/api/calls/preflight")
def preflight_call_window_check(req: PreflightRequest) -> Dict[str, Any]:
    customer = get_customer(req.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {req.customer_id} not found")

    allowed, reason = check_pre_call_gate(bool(customer["do_not_call"]), req.simulated_hour)
    if not allowed:
        # Refused calls MUST return 403 and NEVER create a row in Call table
        raise HTTPException(status_code=403, detail=reason)

    opening = generate_opening_disclosure(customer["name"], customer.get("language", "Hindi"))
    return {"allowed": True, "reason": reason, "opening_disclosure": opening, "customer": customer}


@app.post("/api/calls/turn")
def execute_borrower_turn(req: TurnRequest) -> Dict[str, Any]:
    customer = get_customer(req.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    result = process_turn(customer, req.input_text, req.stage, req.language)
    if result.get("mark_do_not_call"):
        update_customer_dnc(req.customer_id, 1)

    return result


@app.post("/api/calls/save")
def save_call(req: SaveCallRequest) -> Dict[str, Any]:
    save_call_record(
        call_id=req.call_id,
        customer_id=req.customer_id,
        start_time=req.start_time,
        duration_seconds=req.duration_seconds,
        connected=req.connected,
        outcome=req.outcome,
        language=req.language,
        callback_time=req.callback_time,
        transcript=req.transcript,
        summary_3line=req.summary_3line,
        mark_do_not_call=req.mark_do_not_call,
    )
    return {"status": "saved", "call_id": req.call_id, "outcome": req.outcome}


@app.get("/api/report")
def get_report() -> Dict[str, Any]:
    return get_report_stats()


@app.post("/api/calls/clear")
def clear_all_calls() -> Dict[str, Any]:
    clear_calls()
    return {"status": "cleared", "report": get_report_stats()}


@app.post("/api/seed_test_calls")
def seed_test_calls_endpoint() -> Dict[str, Any]:
    return seed_test_calls(TEST_CALLS_CSV_PATH, CSV_PATH)


@app.get("/api/tts")
async def tts_endpoint(text: str = Query(..., min_length=1), lang: str = Query("Hindi")) -> Response:
    audio_bytes = await synthesize_tts_mp3(text, lang)
    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis unavailable")
    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.post("/api/stt")
async def stt_endpoint(file: UploadFile = File(...)) -> Dict[str, Any]:
    audio_bytes = await file.read()
    transcription = transcribe_groq_whisper(audio_bytes, file.filename or "audio.wav")
    if transcription is None:
        return {"status": "fallback", "text": "", "message": "STT fallback: using browser SpeechRecognition"}
    return {"status": "success", "text": transcription}
