"""Twilio Media Stream WebSocket Handler for Real-Time Conversational AI Voicebot.

Implements bidirectional full-duplex audio streaming over WebSocket:
- Inbound: Twilio 8kHz mu-law audio -> VAD Speech Detection -> 16kHz WAV -> Sarvam / Whisper STT
- NLU: 6-Rule Compliance Gate & Stage Transition via `process_turn()`
- Outbound: Sarvam Bulbul / Microsoft Edge-TTS -> 8kHz mu-law -> Twilio Media Stream
- Barge-in: Clears playback buffer on user interruption via {"event": "clear"}
"""

import asyncio
import base64
import io
import json
import os
import subprocess
import wave
from typing import Any, Dict, List, Optional
from fastapi import WebSocket, WebSocketDisconnect

import audioop

from backend.db import finalize_call_record, get_customer, update_call_turn
from backend.voice_pipeline import (
    generate_opening_disclosure,
    process_turn,
    synthesize_tts_mp3,
    transcribe_audio_stt,
)

TWILIO_SAMPLE_RATE = 8000
CHUNK_SIZE = 160  # 20ms of 8kHz mu-law audio (8000 * 0.02 = 160 bytes)
ENERGY_THRESHOLD = 450  # RMS threshold to detect borrower speech
MIN_SPEECH_CHUNKS = 25  # ~0.5 seconds minimum speech
SILENCE_CHUNKS_TO_FLUSH = 32  # ~0.64 seconds of silence to trigger end-of-turn


def mp3_to_mulaw(mp3_bytes: bytes) -> bytes:
    """Converts MP3 audio bytes into 8000Hz mono 8-bit mu-law raw bytes."""
    if not mp3_bytes:
        return b""
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-i", "pipe:0", "-f", "mulaw", "-ar", "8000", "-ac", "1", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _ = proc.communicate(input=mp3_bytes)
        return out
    except Exception as e:
        print(f"[Audio Conversion Error] mp3_to_mulaw failed: {e}")
        return b""


def mulaw_to_wav(mulaw_bytes: bytes, in_sample_rate: int = 8000, out_sample_rate: int = 16000) -> bytes:
    """Converts 8000Hz mu-law raw bytes into 16000Hz mono 16-bit linear PCM WAV bytes for STT."""
    linear_pcm = audioop.ulaw2lin(mulaw_bytes, 2)
    resampled, _ = audioop.ratecv(linear_pcm, 2, 1, in_sample_rate, out_sample_rate, None)
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(out_sample_rate)
        wf.writeframes(resampled)
    return wav_io.getvalue()


async def stream_mulaw_to_twilio(websocket: WebSocket, stream_sid: str, mulaw_data: bytes, is_bot_speaking_flag: dict) -> None:
    """Streams 8kHz mu-law audio to Twilio WebSocket in real-time 20ms chunks."""
    is_bot_speaking_flag["active"] = True
    total_len = len(mulaw_data)
    for i in range(0, total_len, CHUNK_SIZE):
        if not is_bot_speaking_flag.get("active", True):
            # Interrupted / barged in
            break
        chunk = mulaw_data[i : i + CHUNK_SIZE]
        if not chunk:
            break
        payload = base64.b64encode(chunk).decode("ascii")
        try:
            await websocket.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": payload}
            })
        except Exception:
            break
        await asyncio.sleep(0.018)  # 20ms real-time pacing
    is_bot_speaking_flag["active"] = False


async def handle_twilio_media_stream(websocket: WebSocket, call_id: str) -> None:
    """Full-duplex WebSocket handler managing Twilio Media Stream for a call."""
    await websocket.accept()
    print(f"\n[Twilio WSS] Connection accepted for call {call_id}")

    stream_sid = ""
    customer_id = "C001"
    language = "Hindi"
    stage = "AWAITING_NAME_CONFIRMATION"
    customer: Optional[Dict[str, Any]] = None

    bot_speaking = {"active": False}
    speech_chunks: List[bytes] = []
    consecutive_silence = 0
    transcript_history: List[Dict[str, Any]] = []
    turn_in_progress = False

    try:
        while True:
            raw_msg = await websocket.receive_text()
            data = json.loads(raw_msg)
            event = data.get("event")

            # 1. Twilio Handshake Events
            if event == "connected":
                print(f"[Twilio WSS] Handshake connected for {call_id}")

            elif event == "start":
                start_info = data.get("start", {})
                stream_sid = data.get("streamSid") or start_info.get("streamSid", "")
                custom_params = start_info.get("customParameters", {})
                customer_id = custom_params.get("customer_id") or "C001"
                language = custom_params.get("language") or "Hindi"
                customer = get_customer(customer_id)

                cust_name = customer["name"] if customer else "Customer"
                print(f"[Twilio WSS] Stream started. Sid: {stream_sid} | Customer: {cust_name} ({customer_id}) | Lang: {language}")

                # Send opening disclosure greeting immediately
                opening_text = generate_opening_disclosure(cust_name, language)
                transcript_history.append({"speaker": "Bot", "text": opening_text, "lang": language})
                print(f"[Twilio WSS] Bot opening: {opening_text}")

                # Synthesize opening speech
                opening_mp3 = await synthesize_tts_mp3(opening_text, language)
                if opening_mp3:
                    mulaw_data = mp3_to_mulaw(opening_mp3)
                    asyncio.create_task(stream_mulaw_to_twilio(websocket, stream_sid, mulaw_data, bot_speaking))

            # 2. Inbound Audio Media Packet from Borrower's Phone
            elif event == "media":
                if not stream_sid or turn_in_progress:
                    continue

                media = data.get("media", {})
                payload_b64 = media.get("payload")
                if not payload_b64:
                    continue

                chunk_mulaw = base64.b64decode(payload_b64)
                pcm = audioop.ulaw2lin(chunk_mulaw, 2)
                rms = audioop.rms(pcm, 2)

                # Voice Activity Detection (VAD)
                if rms > ENERGY_THRESHOLD:
                    # User is speaking!
                    consecutive_silence = 0
                    speech_chunks.append(chunk_mulaw)

                    # Barge-in interruption: if bot is playing speech, clear playback buffer!
                    if bot_speaking.get("active", False):
                        bot_speaking["active"] = False
                        await websocket.send_json({
                            "event": "clear",
                            "streamSid": stream_sid,
                        })
                        print("[Twilio WSS] Barge-in detected: cleared bot playback buffer")
                else:
                    # Silence chunk
                    if speech_chunks:
                        consecutive_silence += 1
                        speech_chunks.append(chunk_mulaw)

                        # End of borrower speech detected!
                        if consecutive_silence >= SILENCE_CHUNKS_TO_FLUSH and len(speech_chunks) >= MIN_SPEECH_CHUNKS:
                            turn_in_progress = True
                            full_mulaw = b"".join(speech_chunks)
                            speech_chunks = []
                            consecutive_silence = 0

                            # Process borrower turn in background task
                            async def process_borrower_utterance(mulaw_audio: bytes, cur_stage: str, cur_lang: str):
                                nonlocal stage, language, turn_in_progress
                                try:
                                    # 1. Speech-to-Text
                                    wav_bytes = mulaw_to_wav(mulaw_audio)
                                    spoken_text = transcribe_audio_stt(wav_bytes, filename="speech.wav", language=cur_lang)
                                    if not spoken_text or not spoken_text.strip():
                                        print("[Twilio WSS] STT silence / unrecognised speech")
                                        turn_in_progress = False
                                        return

                                    print(f"\n[Twilio WSS] Borrower: '{spoken_text}' (Stage: {cur_stage}, Lang: {cur_lang})")
                                    transcript_history.append({"speaker": "Borrower", "text": spoken_text, "lang": cur_lang})

                                    # 2. Conversational Turn & Guardrail Decision
                                    cust_record = customer or get_customer(customer_id) or {
                                        "customer_id": customer_id,
                                        "name": "Customer",
                                        "language": cur_lang,
                                        "birth_year": 1990,
                                        "do_not_call": 0,
                                    }
                                    res = process_turn(cust_record, spoken_text, cur_stage, cur_lang)
                                    bot_reply = res.get("bot_reply", "")
                                    new_stage = res.get("stage", "COMPLETED")
                                    detected_lang = res.get("detected_language") or cur_lang
                                    outcome = res.get("outcome")

                                    stage = new_stage
                                    language = detected_lang
                                    transcript_history.append({"speaker": "Bot", "text": bot_reply, "lang": detected_lang})
                                    print(f"[Twilio WSS] Bot: '{bot_reply}' (New Stage: {new_stage}, Outcome: {outcome})")

                                    # Update turn record in DB
                                    update_call_turn(
                                        call_id=call_id,
                                        borrower_text=spoken_text,
                                        bot_reply=bot_reply,
                                        language=detected_lang,
                                        outcome=outcome,
                                        callback_time=res.get("callback_time"),
                                        mark_do_not_call=res.get("mark_do_not_call", False),
                                        customer_id=customer_id,
                                    )

                                    # 3. Synthesize & Stream Bot Response
                                    reply_mp3 = await synthesize_tts_mp3(bot_reply, detected_lang)
                                    if reply_mp3:
                                        reply_mulaw = mp3_to_mulaw(reply_mp3)
                                        await stream_mulaw_to_twilio(websocket, stream_sid, reply_mulaw, bot_speaking)

                                    # 4. Handle Terminal Outcomes
                                    if outcome or new_stage == "COMPLETED":
                                        print(f"[Twilio WSS] Call resolved with final outcome: {outcome}. Concluding session.")
                                        await asyncio.sleep(2.0)
                                        finalize_call_record(
                                            call_id=call_id,
                                            duration_seconds=30,
                                            outcome=outcome or "Right customer",
                                            callback_time=res.get("callback_time"),
                                            callback_time_iso=res.get("callback_time_iso"),
                                            language=detected_lang,
                                        )
                                        try:
                                            await websocket.close()
                                        except Exception:
                                            pass
                                finally:
                                    turn_in_progress = False

                            asyncio.create_task(process_borrower_utterance(full_mulaw, stage, language))

            # 3. Twilio Hangup / Stop Event
            elif event == "stop":
                print(f"[Twilio WSS] Stream stopped by Twilio for {call_id}")
                break

    except WebSocketDisconnect:
        print(f"[Twilio WSS] WebSocket disconnected for {call_id}")
    except Exception as e:
        print(f"[Twilio WSS Error] {e}")
    finally:
        bot_speaking["active"] = False
        print(f"[Twilio WSS] Session closed for {call_id}")

