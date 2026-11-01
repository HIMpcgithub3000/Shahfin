"""FastAPI Server for Shaha Finlease Right-Party Verification Voicebot.

Provides REST and Voice endpoints serving the Two-Screen Console & Report UI.
"""
import io
import json
import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.compliance_gate import check_pre_call_gate
from backend.twilio_stream import handle_twilio_media_stream
from backend.db import (
    clear_calls,
    create_initial_call_record,
    execute_sql,
    finalize_call_record,
    get_all_customers,
    get_connection,
    get_customer,
    get_next_call_id,
    get_real_leads,
    get_report_stats,
    init_db_and_seed_customers,
    reset_customers,
    save_call_record,
    seed_test_calls,
    update_call_turn,
    update_customer_dnc,
    upsert_customer,
)
from backend.voice_pipeline import (
    RECORDINGS_DIR,
    generate_and_save_full_call_audio,
    generate_opening_disclosure,
    process_turn,
    synthesize_tts_mp3,
    transcribe_audio_stt,
    transcribe_groq_whisper,
)

from contextlib import asynccontextmanager
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

CSV_PATH = os.path.join(BASE_DIR, "data", "customers.csv")
TEST_CALLS_CSV_PATH = os.path.join(BASE_DIR, "data", "test_calls.csv")
FRONTEND_PATH = os.path.join(BASE_DIR, "frontend", "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_and_seed_customers(CSV_PATH)
    # Ensure 10 benchmark calls from test_calls.csv are seeded if table is below 10
    rep = get_report_stats()
    if rep.get("calls_attempted", 0) < 10:
        seed_test_calls(TEST_CALLS_CSV_PATH, CSV_PATH)
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

AUDIO_DEMOS_DIR = os.path.join(BASE_DIR, "audio_demos")
if os.path.exists(AUDIO_DEMOS_DIR):
    app.mount("/audio_demos", StaticFiles(directory=AUDIO_DEMOS_DIR), name="audio_demos")

RECORDINGS_MOUNT_DIR = os.path.join(BASE_DIR, "audio_recordings")
if os.path.exists(RECORDINGS_MOUNT_DIR):
    app.mount("/audio_recordings", StaticFiles(directory=RECORDINGS_MOUNT_DIR), name="audio_recordings")


class PreflightRequest(BaseModel):
    customer_id: str
    simulated_hour: Optional[int] = None


class InitiateCallRequest(BaseModel):
    customer_id: str
    simulated_hour: Optional[int] = None
    start_time: Optional[str] = None


class TurnRequest(BaseModel):
    customer_id: str
    input_text: str
    stage: str = "AWAITING_NAME_CONFIRMATION"
    language: str = "Hindi"
    call_id: Optional[str] = None


class FinalizeCallRequest(BaseModel):
    call_id: str
    duration_seconds: int = 0
    outcome: Optional[str] = None
    summary_3line: Optional[List[str]] = None
    callback_time: Optional[str] = None
    callback_time_iso: Optional[str] = None
    audio_path: Optional[str] = None
    language: Optional[str] = None


class SaveCallRequest(BaseModel):
    call_id: str
    customer_id: str
    start_time: Optional[str] = None
    duration_seconds: int
    connected: bool
    outcome: str
    language: str
    callback_time: Optional[str] = None
    callback_time_iso: Optional[str] = None
    audio_path: Optional[str] = None
    transcript: List[Dict[str, Any]]
    summary_3line: List[str]
    mark_do_not_call: bool = False


class TelephonyOutboundRequest(BaseModel):
    phone_number: str
    customer_id: str = "C001"
    language: str = "Hindi"


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
    # Protect verification data: strip birth_year from public list API
    return [
        {
            "customer_id": c["customer_id"],
            "name": c["name"],
            "phone": c["phone"],
            "language": c["language"],
            "do_not_call": c["do_not_call"],
        }
        for c in get_all_customers()
    ]


@app.post("/api/customers/reset")
def reset_all_customers() -> Dict[str, Any]:
    reset_customers(CSV_PATH)
    return {
        "status": "reset",
        "customers": [
            {
                "customer_id": c["customer_id"],
                "name": c["name"],
                "phone": c["phone"],
                "language": c["language"],
                "do_not_call": c["do_not_call"],
            }
            for c in get_all_customers()
        ],
    }


class LeadItem(BaseModel):
    name: str
    phone: str
    birth_year: Optional[int] = 1990
    language: Optional[str] = "Hindi"
    do_not_call: Optional[int] = 0


class ImportLeadsRequest(BaseModel):
    leads: List[LeadItem]


@app.get("/api/leads/real")
def get_real_telephony_leads() -> List[Dict[str, Any]]:
    """Returns all real telephony leads (excluding dummy test C001-C010). Ensures Himanshu Sharma is present."""
    return get_real_leads()


@app.post("/api/leads/add")
def add_real_lead(lead: LeadItem) -> Dict[str, Any]:
    """Adds a single real lead for outbound calling."""
    phone = lead.phone.strip()
    if not phone.startswith("+"):
        if len(phone) == 10:
            phone = f"+91{phone}"
        else:
            phone = f"+{phone}"
    existing = get_real_leads()
    next_id = f"R{len(existing) + 1:03d}"
    created = upsert_customer(
        customer_id=next_id,
        name=lead.name.strip(),
        phone=phone,
        birth_year=lead.birth_year or 1990,
        language=lead.language or "Hindi",
        do_not_call=lead.do_not_call or 0,
    )
    return {"status": "success", "lead": created}


@app.post("/api/leads/import")
def import_real_leads(req: ImportLeadsRequest) -> Dict[str, Any]:
    """Imports a list of real customer leads from CSV or JSON to the database."""
    existing = get_real_leads()
    count_before = len(existing)
    saved = []
    for idx, item in enumerate(req.leads):
        phone = item.phone.strip()
        if not phone.startswith("+"):
            if len(phone) == 10:
                phone = f"+91{phone}"
            else:
                phone = f"+{phone}"
        lead_id = f"R{count_before + idx + 1:03d}"
        res = upsert_customer(
            customer_id=lead_id,
            name=item.name.strip(),
            phone=phone,
            birth_year=item.birth_year or 1990,
            language=item.language or "Hindi",
            do_not_call=item.do_not_call or 0,
        )
        saved.append(res)
    return {"status": "success", "imported_count": len(saved), "leads": get_real_leads()}


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
    # Protect verification data: do not expose birth_year
    safe_customer = {
        "customer_id": customer["customer_id"],
        "name": customer["name"],
        "phone": customer["phone"],
        "language": customer["language"],
        "do_not_call": customer["do_not_call"],
    }
    return {"allowed": True, "reason": reason, "opening_disclosure": opening, "customer": safe_customer}


@app.post("/api/calls/initiate")
def initiate_call(req: InitiateCallRequest) -> Dict[str, Any]:
    """Feature #4 Backend-Owned Call Lifecycle:

    Checks gatekeeper and creates Call record in dbo.Call as 'Not connected' before conversation begins.
    Guarantees call is tracked in DB even if connection drops before response or browser crashes.
    """
    customer = get_customer(req.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {req.customer_id} not found")

    allowed, reason = check_pre_call_gate(bool(customer["do_not_call"]), req.simulated_hour)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    call_id = get_next_call_id()
    lang = customer.get("language", "Hindi")
    opening = generate_opening_disclosure(customer["name"], lang)

    # Persist in DB immediately
    create_initial_call_record(
        call_id=call_id,
        customer_id=req.customer_id,
        language=lang,
        opening_text=opening,
        start_time=req.start_time,
    )

    safe_customer = {
        "customer_id": customer["customer_id"],
        "name": customer["name"],
        "phone": customer["phone"],
        "language": customer["language"],
        "do_not_call": customer["do_not_call"],
    }
    return {
        "allowed": True,
        "call_id": call_id,
        "opening_disclosure": opening,
        "customer": safe_customer,
    }


@app.post("/api/calls/turn")
def execute_borrower_turn(req: TurnRequest) -> Dict[str, Any]:
    customer = get_customer(req.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    result = process_turn(customer, req.input_text, req.stage, req.language)
    if result.get("mark_do_not_call"):
        update_customer_dnc(req.customer_id, 1)

    # If call_id provided, update dbo.Call turn-by-turn
    if req.call_id:
        update_call_turn(
            call_id=req.call_id,
            borrower_text=req.input_text,
            bot_reply=result.get("bot_reply", ""),
            language=result.get("detected_language", req.language),
            outcome=result.get("outcome"),
            callback_time=result.get("callback_time"),
            mark_do_not_call=bool(result.get("mark_do_not_call", False)),
            customer_id=req.customer_id,
        )

    return result


@app.post("/api/calls/finalize")
async def finalize_call(req: FinalizeCallRequest) -> Dict[str, Any]:
    """Finalizes call outcome, duration, audio recording, and 3-line summary in dbo.Call."""
    audio_path = req.audio_path
    if not audio_path and req.call_id:
        conn = get_connection()
        cur = conn.cursor()
        execute_sql(cur, conn, "SELECT transcript, language FROM Call WHERE call_id = ?", (req.call_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            try:
                trans = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                lang = req.language or row[1] or "Hindi"
                audio_path = await generate_and_save_full_call_audio(req.call_id, trans, lang)
            except Exception as e:
                print(f"[Recording notice] {e}")

    finalize_call_record(
        call_id=req.call_id,
        duration_seconds=req.duration_seconds,
        outcome=req.outcome or "Not connected",
        summary_3line=req.summary_3line,
        callback_time=req.callback_time,
        callback_time_iso=req.callback_time_iso,
        audio_path=audio_path,
        language=req.language,
    )
    return {"status": "finalized", "call_id": req.call_id, "audio_path": audio_path}


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
        callback_time_iso=req.callback_time_iso,
        audio_path=req.audio_path,
    )
    return {"status": "saved", "call_id": req.call_id, "outcome": req.outcome}


@app.get("/api/calls/{call_id}/audio")
def get_call_audio_endpoint(call_id: str):
    """Bonus #3: Serves the persisted MP3 or WAV recording for the call."""
    file_mp3 = os.path.join(RECORDINGS_DIR, f"{call_id}.mp3")
    file_wav = os.path.join(RECORDINGS_DIR, f"{call_id}.wav")
    if os.path.exists(file_mp3):
        return FileResponse(file_mp3, media_type="audio/mpeg", filename=f"{call_id}.mp3")
    if os.path.exists(file_wav):
        return FileResponse(file_wav, media_type="audio/wav", filename=f"{call_id}.wav")
    raise HTTPException(status_code=404, detail="Call audio recording not found")


@app.post("/api/telephony/outbound")
def initiate_real_phone_call(req: TelephonyOutboundRequest) -> Dict[str, Any]:
    """Bonus #5: Places a real outbound phone call via Twilio trial API."""
    import urllib.parse
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
    load_dotenv(os.path.join(BASE_DIR, "..", ".env"), override=True)
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_phone = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    base_url = os.getenv("BASE_WEBHOOK_URL", "").strip()

    customer = get_customer(req.customer_id)
    cust_name = customer["name"] if customer else "Customer"
    if not account_sid or not auth_token or not from_phone:
        return {
            "status": "simulated",
            "message": "Twilio trial credentials (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER) not set in .env. Mock call simulated.",
            "phone_number": req.phone_number,
            "customer_id": req.customer_id,
            "customer": customer,
        }

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        call_id = get_next_call_id()
        opening_text = generate_opening_disclosure(cust_name, req.language)
        create_initial_call_record(call_id, req.customer_id, req.language, opening_text)

        # Use public base_url or active public tunnel for bidirectional voicebot conversation:
        tunnel_url = "https://petite-eggs-burn.loca.lt"
        if base_url.startswith("https://") or (base_url.startswith("http://") and "localhost" not in base_url and "127.0.0.1" not in base_url and " " not in base_url):
            effective_base = base_url
        else:
            effective_base = tunnel_url

        voice_url = f"{effective_base}/api/telephony/twilio/voice?customer_id={req.customer_id}&lang={req.language}&call_id={call_id}"

        tw_call = client.calls.create(
            to=req.phone_number,
            from_=from_phone,
            url=voice_url,
        )
        return {
            "status": "success",
            "sid": tw_call.sid,
            "call_id": call_id,
            "to": req.phone_number,
            "customer": customer,
        }
    except Exception as e:
        print(f"[Twilio Error] {e}")
        return {"status": "error", "detail": str(e), "customer": customer}


@app.websocket("/api/telephony/twilio/stream/{call_id}")
async def twilio_media_stream_ws(websocket: WebSocket, call_id: str):
    """Twilio Media Streams: Bidirectional 8kHz mu-law audio over WebSocket (PRD Module 4)."""
    await handle_twilio_media_stream(websocket, call_id)


@app.api_route("/api/telephony/twilio/voice", methods=["GET", "POST"])
def twilio_voice_webhook(
    request: Request,
    customer_id: str = Query("C001"),
    lang: str = Query("Hindi"),
    call_id: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
) -> Response:
    """Twilio Webhook: Initial TwiML connecting Twilio call to conversational agent."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    is_secure = "https" in (request.headers.get("x-forwarded-proto") or "") or "loca.lt" in host or "ngrok" in host
    proto = "https" if is_secure else "http"
    base = f"{proto}://{host}"
    cid = call_id or get_next_call_id()

    # Explicit bidirectional Media Stream requested (e.g. over ngrok / production WebSocket domain)
    if mode == "stream":
        ws_proto = "wss" if is_secure else "ws"
        stream_ws_url = f"{ws_proto}://{host}/api/telephony/twilio/stream/{cid}"
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_ws_url}">
            <Parameter name="call_id" value="{cid}" />
            <Parameter name="customer_id" value="{customer_id}" />
            <Parameter name="language" value="{lang}" />
        </Stream>
    </Connect>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Interactive Speech Recognition TwiML (ultra-reliable over localtunnel / HTTP webhooks)
    customer = get_customer(customer_id) or {
        "customer_id": customer_id,
        "name": "Customer",
        "language": lang,
        "birth_year": 1990,
        "do_not_call": 0,
    }
    cust_name = customer["name"] if customer else "Customer"
    opening = generate_opening_disclosure(cust_name, lang)
    twiml_lang = "hi-IN" if lang == "Hindi" else ("mr-IN" if lang == "Marathi" else "en-IN")
    action_url = f"{base}/api/telephony/twilio/turn?customer_id={customer_id}&amp;lang={lang}&amp;call_id={cid}&amp;stage=AWAITING_NAME_CONFIRMATION"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{action_url}" method="POST" language="{twiml_lang}" timeout="8" speechTimeout="auto">
        <Say language="{twiml_lang}">{opening}</Say>
    </Gather>
    <Say language="{twiml_lang}">कृपया अपना उत्तर बताएं। क्या मेरी बात {cust_name} जी से हो रही है?</Say>
    <Gather input="speech" action="{action_url}" method="POST" language="{twiml_lang}" timeout="8" speechTimeout="auto"/>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.api_route("/api/telephony/twilio/turn", methods=["GET", "POST"])
def twilio_turn_webhook(
    request: Request,
    SpeechResult: Optional[str] = Form(None),
    customer_id: str = Query("C001"),
    lang: str = Query("Hindi"),
    call_id: Optional[str] = Query(None),
    stage: str = Query("AWAITING_NAME_CONFIRMATION"),
) -> Response:
    """Twilio Webhook: Turn processing from SpeechResult."""
    customer = get_customer(customer_id) or {
        "customer_id": customer_id,
        "name": "Customer",
        "language": lang,
        "birth_year": 1990,
        "do_not_call": 0,
    }
    speech_text = (SpeechResult or "").strip()
    print(f"\n[Twilio Speech Received] Borrower said: '{speech_text}' | Customer: {customer_id} | Stage: {stage} | Lang: {lang}")

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    proto = request.headers.get("x-forwarded-proto") or ("https" if "loca.lt" in host or "ngrok" in host else "http")
    base = f"{proto}://{host}"
    cid = call_id or ""

    if not speech_text:
        twiml_lang = "hi-IN" if lang == "Hindi" else ("mr-IN" if lang == "Marathi" else "en-IN")
        action_url = f"{base}/api/telephony/twilio/turn?customer_id={customer_id}&amp;lang={lang}&amp;call_id={cid}&amp;stage={stage}"
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="{twiml_lang}">क्षमा करें, मुझे आपकी आवाज़ नहीं सुनाई दी।</Say>
    <Gather input="speech" action="{action_url}" method="POST" language="{twiml_lang}" timeout="7" speechTimeout="auto"/>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    res = process_turn(customer, speech_text, stage, lang)
    bot_reply = res.get("bot_reply", "")
    new_stage = res.get("stage", "COMPLETED")
    detected_lang = res.get("detected_language") or lang
    twiml_lang = "hi-IN" if detected_lang == "Hindi" else ("mr-IN" if detected_lang == "Marathi" else "en-IN")

    # Persist the turn to database
    if cid:
        try:
            update_call_turn(
                call_id=cid,
                borrower_text=speech_text,
                bot_reply=bot_reply,
                language=detected_lang,
                outcome=res.get("outcome"),
                callback_time=res.get("callback_time"),
                mark_do_not_call=res.get("mark_do_not_call", False),
                customer_id=customer_id,
            )
        except Exception as e:
            print(f"[DB Turn Error] {e}")

    next_action = f"{base}/api/telephony/twilio/turn?customer_id={customer_id}&amp;lang={detected_lang}&amp;call_id={cid}&amp;stage={new_stage}"

    if new_stage == "COMPLETED" or res.get("outcome"):
        if cid:
            try:
                finalize_call_record(
                    call_id=cid,
                    duration_seconds=20,
                    outcome=res.get("outcome") or "Right customer",
                    summary_3line=res.get("summary_3line"),
                    callback_time=res.get("callback_time"),
                    callback_time_iso=res.get("callback_time_iso"),
                    language=detected_lang,
                )
            except Exception as e:
                print(f"[DB Finalize Error] {e}")

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="{twiml_lang}">{bot_reply}</Say>
    <Pause length="1"/>
    <Hangup/>
</Response>"""
    else:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{next_action}" method="POST" language="{twiml_lang}" timeout="7" speechTimeout="auto">
        <Say language="{twiml_lang}">{bot_reply}</Say>
    </Gather>
    <Say language="{twiml_lang}">कृपया अपना उत्तर बताएं।</Say>
    <Gather input="speech" action="{next_action}" method="POST" language="{twiml_lang}" timeout="7" speechTimeout="auto"/>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


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
    media_type = "audio/wav" if audio_bytes.startswith(b"RIFF") else "audio/mpeg"
    return Response(content=audio_bytes, media_type=media_type)


@app.post("/api/stt")
async def stt_endpoint(file: UploadFile = File(...), lang: str = Form("Hindi")) -> Dict[str, Any]:
    audio_bytes = await file.read()
    transcription = transcribe_audio_stt(audio_bytes, file.filename or "audio.wav", lang)
    if transcription is None:
        return {"status": "fallback", "text": "", "message": "STT fallback: using browser SpeechRecognition"}
    return {"status": "success", "text": transcription}
