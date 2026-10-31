"""Test Suite for VoiceBrew 16-Stage Streaming Pipeline DAG Architecture.

Validates all 16 pipeline processors, filters, barge-in echo guards,
silence watchdog, and 2-channel stereo audio recorder.
"""

import io
import os
import sys
import time
import wave
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.pipeline_processors import (
    AudioBufferProcessor,
    CallSilenceWatchdog,
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
    UserTurnCoalescer,
)


def test_stage_2_audio_upsampler():
    """Stage 2: Upsamples 8kHz mu-law audio to 16kHz linear PCM WAV."""
    upsampler = SttAudioUpsampler16k(in_rate=8000, out_rate=16000)
    # 160 bytes of mu-law silence (20ms at 8kHz)
    dummy_mulaw = b"\xff" * 160
    wav_bytes = upsampler.upsample_mulaw_to_wav(dummy_mulaw)
    assert len(wav_bytes) > 44  # WAV header is 44 bytes

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000


def test_stage_4_greeting_lock():
    """Stage 4: Protects initial greeting for first 3.0 seconds."""
    lock = GreetingLockProcessor(lock_duration_sec=0.2)
    lock.start()
    assert lock.check_is_locked() is True
    time.sleep(0.25)
    assert lock.check_is_locked() is False

    # Immediate unlock
    lock.start()
    lock.unlock_now()
    assert lock.check_is_locked() is False


def test_stage_5_junk_and_noise_filter():
    """Stage 5: Strips single-character hallucinations, static, and noise tokens."""
    assert JunkTranscriptFilter.clean(".") is None
    assert JunkTranscriptFilter.clean("a") is None
    assert JunkTranscriptFilter.clean("   ") is None
    assert JunkTranscriptFilter.clean("[cough]") is None
    assert JunkTranscriptFilter.clean("[blank_audio]") is None
    assert JunkTranscriptFilter.clean("(laughter)") is None
    assert JunkTranscriptFilter.clean("Привет") is None  # Cyrillic hallucination
    assert JunkTranscriptFilter.clean("हाँ, मैं राहुल हूँ।") == "हाँ, मैं राहुल हूँ।"
    assert JunkTranscriptFilter.clean("Yes, speaking.") == "Yes, speaking."


def test_stage_5b_stt_drop_clarifier():
    """Stage 5b: Returns courteous repeat prompt in borrower language."""
    hindi = STTDropClarifier.get_retry_prompt("Hindi")
    assert "स्पष्ट नहीं आई" in hindi or "दोहरा" in hindi

    marathi = STTDropClarifier.get_retry_prompt("Marathi")
    assert "पुन्हा" in marathi or "आवाज" in marathi

    english = STTDropClarifier.get_retry_prompt("English")
    assert "repeat" in english.lower()


def test_stage_6_user_turn_coalescer():
    """Stage 6: Coalesces broken telecalling speech fragments."""
    coalescer = UserTurnCoalescer(coalesce_window_sec=0.1)
    coalescer.add_fragment("हाँ")
    coalescer.add_fragment("मैं राहुल शर्मा बोल रहा हूँ")
    full = coalescer.flush()
    assert full == "हाँ मैं राहुल शर्मा बोल रहा हूँ"


def test_stage_7_context_aggregator():
    """Stage 7: Maintains conversational turn history."""
    ctx = ContextAggregator("C001", "Rahul Sharma", "Hindi")
    ctx.add_turn("Bot", "नमस्ते", "Hindi", "AWAITING_NAME_CONFIRMATION", 300)
    ctx.add_turn("Borrower", "हाँ मैं राहुल बोल रहा हूँ", "Hindi", "AWAITING_NAME_CONFIRMATION", 450)
    assert len(ctx.turns) == 2
    summary = ctx.get_history_summary()
    assert "Bot: नमस्ते" in summary
    assert "Borrower: हाँ मैं राहुल" in summary


def test_stage_8_rag_single_pass_injector():
    """Stage 8: Injects loan context and compliance rules directly into prompt context."""
    cust = {"customer_id": "C001", "name": "Rahul Sharma", "birth_year": 1985, "language": "Hindi"}
    rag = RagSinglePassInjector.inject(cust, "AWAITING_BIRTH_YEAR")
    assert rag["customer_id"] == "C001"
    assert rag["expected_birth_year"] == 1985
    assert len(rag["compliance_rules"]) == 6
    assert any("Rule 1" in r for r in rag["compliance_rules"])


def test_stage_9_llm_trigger_debounce():
    """Stage 9: Stops double replies if two speech frames fire within 150ms."""
    debounce = LLMTriggerDebounce(debounce_ms=100)
    assert debounce.allow() is True
    assert debounce.allow() is False  # Immediately after -> blocked
    time.sleep(0.12)
    assert debounce.allow() is True  # After window -> allowed


def test_stage_11_meta_narration_filter():
    """Stage 11: Drops LLM thinking tags, meta-commentary, and markdown bolding."""
    raw = "<think>The borrower confirmed name.</think>The customer has confirmed their identity. धन्यवाद राहुल जी।"
    filtered = MetaNarrationFilter.filter_text(raw)
    assert "<think>" not in filtered
    assert "The customer has confirmed" not in filtered
    assert "धन्यवाद राहुल जी।" in filtered

    markdown_text = "**Shaha Finlease** से कॉल है।"
    assert MetaNarrationFilter.filter_text(markdown_text) == "Shaha Finlease से कॉल है।"


def test_stage_12_tts_text_sanitizer():
    """Stage 12: Normalizes currency, percentages, and replaces harsh exclamation marks."""
    hindi_text = "आपका बकाया ₹5,000 है और ब्याज दर 8.85% है!"
    sanitized = TTSTextSanitizer.sanitize(hindi_text, "Hindi")
    assert "5,000 रुपये" in sanitized
    assert "8.85 प्रतिशत" in sanitized
    assert "!" not in sanitized
    assert sanitized.endswith(".")

    eng_text = "Your EMI is ₹4,200 with 10.5% interest!"
    sanitized_eng = TTSTextSanitizer.sanitize(eng_text, "English")
    assert "4,200 rupees" in sanitized_eng
    assert "10.5 percent" in sanitized_eng
    assert "!" not in sanitized_eng


def test_stage_13_language_auto_switcher():
    """Stage 13: Detects Unicode script (Marathi ळ, Hindi Devanagari, English)."""
    assert LanguageAutoSwitcher.detect_language("हो, मी बाळकृष्ण बोलत आहे.") == "Marathi"  # ळ detected
    assert LanguageAutoSwitcher.detect_language("नमस्कार, कसा आहात?") == "Marathi"  # Lexical match
    assert LanguageAutoSwitcher.detect_language("हाँ, मैं राहुल शर्मा बोल रहा हूँ।") == "Hindi"
    assert LanguageAutoSwitcher.detect_language("Yes, this is Rahul speaking.") == "English"

    # Neural voice mappings
    assert LanguageAutoSwitcher.get_neural_tts_voice("Marathi") == "mr-IN-AarohiNeural"
    assert LanguageAutoSwitcher.get_neural_tts_voice("Hindi") == "hi-IN-SwaraNeural"
    assert LanguageAutoSwitcher.get_neural_tts_voice("English") == "en-IN-NeerjaNeural"


def test_barge_in_echo_guard():
    """Barge-in: MinWordsInterruptionStrategy prevents speakerphone echo interruptions."""
    guard = MinWordsInterruptionStrategy(min_words=2)
    assert guard.should_interrupt("uh") is False
    assert guard.should_interrupt("ha") is False
    assert guard.should_interrupt("") is False
    assert guard.should_interrupt("हाँ ठीक") is True
    assert guard.should_interrupt("I am listening") is True


def test_stage_15_server_side_stereo_recorder():
    """Stage 15: Records dual-channel stereo WAV (Left=User, Right=Bot)."""
    recorder = AudioBufferProcessor(sample_rate=8000)
    # 80 bytes of user speech and bot speech
    recorder.record_user_mulaw(b"\x00" * 80)
    recorder.record_bot_mulaw(b"\xff" * 80)

    stereo_bytes = recorder.generate_stereo_wav()
    assert len(stereo_bytes) > 44

    with wave.open(io.BytesIO(stereo_bytes), "rb") as wf:
        assert wf.getnchannels() == 2  # Stereo!
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 8000


def test_call_silence_watchdog():
    """Silence watchdog: Triggers nudge at 10s and disconnect at 15s."""
    nudged = []
    disconnected = []
    dog = CallSilenceWatchdog(
        nudge_seconds=0.05,
        disconnect_seconds=0.1,
        on_nudge=lambda: nudged.append(True),
        on_disconnect=lambda: disconnected.append(True),
    )
    dog.start()
    time.sleep(0.06)
    nudge_trig, disc_trig = dog.check_silence()
    assert nudge_trig is True
    assert disc_trig is False
    assert len(nudged) == 1

    time.sleep(0.06)
    nudge_trig, disc_trig = dog.check_silence()
    assert disc_trig is True
    assert len(disconnected) == 1


def test_metrics_tap():
    """Metrics tap: Measures turn-by-turn SLA metrics and E2E latency."""
    tap = MetricsTap("CALL-TEST-001")
    metric = tap.record_turn_metrics(
        turn_no=1,
        stt_latency_ms=450,
        nlu_latency_ms=320,
        tts_latency_ms=210,
        guardrail_safe=True,
        language="Hindi",
    )
    assert metric["total_e2e_ms"] == 980
    assert metric["sla_met_under_3s"] is True
    assert metric["guardrail_safe"] is True
