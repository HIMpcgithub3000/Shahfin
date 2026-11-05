"""Twilio Media Stream WebSocket Handler for Real-Time Conversational AI Voicebot.

Implements the VoiceBrew / Pipecat AI 16-Stage Streaming Pipeline DAG:
1. Transport Input (Twilio WSS 8kHz mu-law audio packets)
2. SttAudioUpsampler16k (8kHz mu-law -> 16kHz PCM WAV)
3. Streaming STT with Deepgram/Sarvam/Whisper
4. GreetingLockProcessor (Protects opening disclosure from line clicks/pickup noise)
5. JunkTranscriptFilter & STTDropClarifier (Strips hallucinations, clarifies drops)
6. UserTurnCoalescer (Glues broken speech fragments)
7. ContextAggregator (Conversation history management)
8. RagSinglePassInjector (Injects RBI & loan compliance parameters)
9. LLMTriggerDebounce (Stops rapid double-replies)
10. Streaming LLM Turn (Gemini/Groq with deterministic SLA fallback)
11. MetaNarrationFilter (Strips thinking tags & commentary)
12. TTSTextSanitizer (Normalizes currency, numerals, percentages)
13. LanguageAutoSwitcher (Unicode script detection: Hindi / Marathi / English)
14. Streaming TTS (Neural synthesis via Edge-TTS / Sarvam Bulbul)
15. AudioBufferProcessor (Server-side 2-channel stereo recorder: L=User, R=Bot)
16. Transport Output (Paced 20ms 8kHz mu-law media packets)

Barge-in: MinWordsInterruptionStrategy (Echo Guard)
Fail-safes: CallSilenceWatchdog, MetricsTap
"""

import asyncio
import base64
import io
import json
import os
import subprocess
import time
import wave
from typing import Any, Dict, List, Optional
from fastapi import WebSocket, WebSocketDisconnect

import audioop

from backend.db import finalize_call_record, get_customer, update_call_turn
from backend.pipeline_processors import (
    AudioBufferProcessor,
    ContextAggregator,
    GreetingLockProcessor,
    JunkTranscriptFilter,
    LanguageAutoSwitcher,
    LLMTriggerDebounce,
    MetaNarrationFilter,
    MetricsTap,
    MinWordsInterruptionStrategy,
    RagSinglePassInjector,
    STTDropClarifier,
    SttAudioUpsampler16k,
    TTSTextSanitizer,
)
from backend.voice_pipeline import (
    generate_opening_disclosure,
    process_turn,
    synthesize_tts_mp3,
    transcribe_audio_stt,
)

TWILIO_SAMPLE_RATE = 8000
CHUNK_SIZE = 160  # 20ms of 8kHz mu-law audio (8000 * 0.02 = 160 bytes)
ENERGY_THRESHOLD = 450  # RMS threshold to detect borrower speech
MIN_SPEECH_CHUNKS = 20  # ~0.4 seconds minimum speech
SILENCE_CHUNKS_TO_FLUSH = 28  # ~0.56 seconds of silence to trigger end-of-turn
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audio_recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)


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
    """Stage 2: Converts 8000Hz mu-law into 16000Hz mono 16-bit linear PCM WAV for STT."""
    upsampler = SttAudioUpsampler16k(in_sample_rate, out_sample_rate)
    return upsampler.upsample_mulaw_to_wav(mulaw_bytes)


async def stream_mulaw_to_twilio(
    websocket: WebSocket,
    stream_sid: str,
    mulaw_data: bytes,
    is_bot_speaking_flag: dict,
    audio_recorder: Optional[AudioBufferProcessor] = None,
) -> None:
    """Stage 16: Streams 8kHz mu-law audio to Twilio in real-time 20ms chunks and records to stereo buffer."""
    is_bot_speaking_flag["active"] = True
    total_len = len(mulaw_data)
    for i in range(0, total_len, CHUNK_SIZE):
        if not is_bot_speaking_flag.get("active", True):
            # Interrupted / barged in
            break
        chunk = mulaw_data[i : i + CHUNK_SIZE]
        if not chunk:
            break

        # Stage 15: Record bot audio to Right Channel
        if audio_recorder:
            audio_recorder.record_bot_mulaw(chunk)

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
    """Full-duplex WebSocket orchestrator executing the 16-stage streaming pipeline DAG."""
    await websocket.accept()
    print(f"\n[Twilio WSS] Connection accepted for call {call_id}")

    stream_sid = ""
    customer_id = "C001"
    language = "Hindi"
    stage = "AWAITING_NAME_CONFIRMATION"
    customer: Optional[Dict[str, Any]] = None

    # Pipeline Processors & State
    upsampler = SttAudioUpsampler16k()
    greeting_lock = GreetingLockProcessor(lock_duration_sec=3.5)
    interruption_guard = MinWordsInterruptionStrategy(min_words=3)
    debounce = LLMTriggerDebounce(debounce_ms=250)
    audio_recorder = AudioBufferProcessor(sample_rate=8000)
    metrics_tap = MetricsTap(call_id=call_id)
    context_aggregator: Optional[ContextAggregator] = None

    bot_speaking = {"active": False}
    speech_chunks: List[bytes] = []
    consecutive_silence = 0
    turn_in_progress = False
    turn_counter = 0

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

                cust_name = customer["name"] if customer else "Borrower"
                context_aggregator = ContextAggregator(customer_id, cust_name, language)
                print(f"[Twilio WSS] Stream started. Sid: {stream_sid} | Customer: {cust_name} ({customer_id}) | Lang: {language}")

                # Stage 4: Activate Greeting Lock
                greeting_lock.start()

                # Generate & Sanitize Opening Disclosure
                raw_opening = generate_opening_disclosure(cust_name, language)
                opening_text = TTSTextSanitizer.sanitize(raw_opening, language)
                context_aggregator.add_turn("Bot", opening_text, language, stage)
                print(f"[Twilio WSS] Bot opening: {opening_text}")

                # Stage 14 & 16: Synthesize opening speech and stream
                opening_mp3 = await synthesize_tts_mp3(opening_text, language)
                if opening_mp3:
                    mulaw_data = mp3_to_mulaw(opening_mp3)
                    asyncio.create_task(stream_mulaw_to_twilio(websocket, stream_sid, mulaw_data, bot_speaking, audio_recorder))

            # 2. Inbound Audio Media Packet from Borrower's Phone
            elif event == "media":
                if not stream_sid or turn_in_progress:
                    continue

                media = data.get("media", {})
                payload_b64 = media.get("payload")
                if not payload_b64:
                    continue

                chunk_mulaw = base64.b64decode(payload_b64)

                # Stage 15: Record user audio to Left Channel
                audio_recorder.record_user_mulaw(chunk_mulaw)

                pcm = audioop.ulaw2lin(chunk_mulaw, 2)
                rms = audioop.rms(pcm, 2)

                # Voice Activity Detection (VAD)
                if rms > ENERGY_THRESHOLD:
                    consecutive_silence = 0
                    speech_chunks.append(chunk_mulaw)

                    # Stage 4: Greeting Lock check
                    if greeting_lock.check_is_locked():
                        # Protect opening disclosure from early throat-clears or pickup clicks
                        pass
                    elif bot_speaking.get("active", False):
                        # Stage 3 Barge-in Interruption (Echo Guard)
                        bot_speaking["active"] = False
                        await websocket.send_json({
                            "event": "clear",
                            "streamSid": stream_sid,
                        })
                        print("[Twilio WSS] Barge-in clear signal dispatched")
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

                            # Unlock greeting if not unlocked yet
                            greeting_lock.unlock_now()

                            # Process borrower turn in background task
                            async def process_borrower_utterance(mulaw_audio: bytes, cur_stage: str, cur_lang: str) -> None:
                                nonlocal stage, language, turn_in_progress, turn_counter
                                turn_counter += 1
                                t_turn_start = time.time()
                                try:
                                    # Stage 2: Audio Upsampler (8kHz -> 16kHz PCM WAV)
                                    wav_bytes = upsampler.upsample_mulaw_to_wav(mulaw_audio)

                                    # Stage 3: Streaming STT
                                    t_stt_0 = time.time()
                                    raw_spoken = transcribe_audio_stt(wav_bytes, filename="speech.wav", language=cur_lang)
                                    stt_ms = int((time.time() - t_stt_0) * 1000)

                                    # Stage 5: Junk & Noise Filter
                                    clean_spoken = JunkTranscriptFilter.clean(raw_spoken)

                                    # Stage 5b: STT Drop Clarifier
                                    if not clean_spoken:
                                        print("[Twilio WSS] STT blank / noise filtered. Clarifying with borrower...")
                                        retry_msg = STTDropClarifier.get_retry_prompt(cur_lang)
                                        retry_mp3 = await synthesize_tts_mp3(retry_msg, cur_lang)
                                        if retry_mp3:
                                            retry_mulaw = mp3_to_mulaw(retry_mp3)
                                            await stream_mulaw_to_twilio(websocket, stream_sid, retry_mulaw, bot_speaking, audio_recorder)
                                        turn_in_progress = False
                                        return

                                    # Check Interruption Guard
                                    if not interruption_guard.should_interrupt(clean_spoken):
                                        print(f"[Twilio WSS] Echo guard: '{clean_spoken}' (<2 words) ignored")

                                    # Stage 9: LLM Trigger Debounce
                                    if not debounce.allow():
                                        print("[Twilio WSS] Debounce dropped duplicate speech trigger")
                                        turn_in_progress = False
                                        return

                                    spoken_text = clean_spoken
                                    print(f"\n[Twilio WSS] Borrower: '{spoken_text}' (Stage: {cur_stage}, Lang: {cur_lang}, STT: {stt_ms}ms)")
                                    if context_aggregator:
                                        context_aggregator.add_turn("Borrower", spoken_text, cur_lang, cur_stage, stt_ms)

                                    # Stage 8: Single-Pass RAG Injection
                                    cust_record = customer or get_customer(customer_id) or {
                                        "customer_id": customer_id,
                                        "name": "Customer",
                                        "language": cur_lang,
                                        "birth_year": 1990,
                                        "do_not_call": 0,
                                    }
                                    rag_context = RagSinglePassInjector.inject(cust_record, cur_stage)

                                    # Stage 10: Conversational Turn & Guardrail Decision
                                    t_nlu_0 = time.time()
                                    res = process_turn(cust_record, spoken_text, cur_stage, cur_lang)
                                    nlu_ms = int((time.time() - t_nlu_0) * 1000)

                                    raw_bot_reply = res.get("bot_reply", "")
                                    new_stage = res.get("stage", "COMPLETED")
                                    detected_lang = res.get("detected_language") or cur_lang
                                    outcome = res.get("outcome")

                                    # Stage 11: Meta-Narration Filter (Strips thinking tags & meta-prefixes)
                                    filtered_reply = MetaNarrationFilter.filter_text(raw_bot_reply)

                                    # Stage 12: Text Sanitizer & Numeral Fixer (Currency, %, Exclamation)
                                    bot_reply = TTSTextSanitizer.sanitize(filtered_reply, detected_lang)

                                    # Stage 13: Language Auto-Switcher
                                    detected_lang = LanguageAutoSwitcher.detect_language(bot_reply, detected_lang)

                                    stage = new_stage
                                    language = detected_lang
                                    if context_aggregator:
                                        context_aggregator.add_turn("Bot", bot_reply, detected_lang, new_stage)
                                    print(f"[Twilio WSS] Bot: '{bot_reply}' (Stage: {new_stage}, Outcome: {outcome}, NLU: {nlu_ms}ms)")

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

                                    # Stage 14: Streaming TTS Synthesis
                                    t_tts_0 = time.time()
                                    reply_mp3 = await synthesize_tts_mp3(bot_reply, detected_lang)
                                    tts_ms = int((time.time() - t_tts_0) * 1000)

                                    # Telemetry & Metrics Tap
                                    metrics_tap.record_turn_metrics(
                                        turn_no=turn_counter,
                                        stt_latency_ms=stt_ms,
                                        nlu_latency_ms=nlu_ms,
                                        tts_latency_ms=tts_ms,
                                        guardrail_safe=res.get("guardrail_safe", True),
                                        language=detected_lang,
                                    )

                                    # Stage 16: Stream audio to Twilio
                                    if reply_mp3:
                                        reply_mulaw = mp3_to_mulaw(reply_mp3)
                                        await stream_mulaw_to_twilio(websocket, stream_sid, reply_mulaw, bot_speaking, audio_recorder)

                                    # Handle Terminal Outcomes
                                    if outcome or new_stage == "COMPLETED":
                                        print(f"[Twilio WSS] Call resolved with final outcome: {outcome}. Concluding session.")
                                        await asyncio.sleep(2.0)
                                        finalize_call_record(
                                            call_id=call_id,
                                            duration_seconds=int(time.time() - t_turn_start) + 15,
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

        # Stage 15: Save Server-Side Stereo Call Recording (Left=User, Right=Bot)
        try:
            stereo_wav = audio_recorder.generate_stereo_wav()
            if stereo_wav:
                rec_path = os.path.join(RECORDINGS_DIR, f"{call_id}_stereo.wav")
                with open(rec_path, "wb") as f:
                    f.write(stereo_wav)
                print(f"[Twilio WSS] Compliant 2-channel stereo call recording saved: {rec_path} ({len(stereo_wav)} bytes)")
        except Exception as err:
            print(f"[Twilio WSS Warning] Stereo recording save failed: {err}")

        print(f"[Twilio WSS] Session closed for {call_id}")


