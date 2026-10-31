"""Pipeline Processors & Streaming DAG Architecture.

Based on the 16-stage VoiceBrew / Pipecat AI Pipeline Blueprint:
[Carrier: Twilio / Exotel / Plivo]
           │ (WSS 8kHz μ-law)
           ▼
1. Transport Input (`TransportInput`)
2. Audio Upsampler (`SttAudioUpsampler16k`) [8kHz → 16kHz for STT]
3. Streaming STT (`StreamingSTT`)
4. Greeting Lock (`GreetingLockProcessor`) [Protects initial introduction]
5. Junk & Noise Filter (`JunkTranscriptFilter`)
   5b. STT Drop Clarifier (`STTDropClarifier`)
6. Turn Coalescer (`UserTurnCoalescer`) [Glues sentence fragments together]
7. User Context Aggregator (`ContextAggregator`)
8. Single-Pass RAG Injector (`RagSinglePassInjector`) [Injects loan rules]
9. LLM Trigger Debounce (`LLMTriggerDebounce`) [Stops double replies]
10. Streaming LLM (`StreamingLLMTurn`)
11. Meta-Narration Filter (`MetaNarrationFilter`) [Removes thinking notes]
12. Text Sanitizer & Numeral Fixer (`TTSTextSanitizer`)
13. Language Auto-Switcher (`LanguageAutoSwitcher`) [Unicode script detection]
14. Streaming TTS (`StreamingTTS`)
15. Server-Side Stereo Audio Recorder (`AudioBufferProcessor`) [L=User, R=Bot]
16. Transport Output (`TransportOutput`)
           │ (WSS 8kHz μ-law)
           ▼
[Customer Hears Audio on Phone]

Barge-in: MinWordsInterruptionStrategy
Fail-safes: CallSilenceWatchdog, MetricsTap
"""

import asyncio
import base64
import io
import json
import re
import struct
import time
import wave
from typing import Any, Callable, Dict, List, Optional, Tuple

import audioop


# =============================================================================
# STAGE 2: Audio Upsampler (8kHz mu-law / PCM -> 16kHz Linear PCM)
# =============================================================================
class SttAudioUpsampler16k:
    """Upsamples 8000Hz audio to 16000Hz Linear PCM for STT engines.

    Prevents acoustic degradation on code-mixed Indian phonetics.
    """

    def __init__(self, in_rate: int = 8000, out_rate: int = 16000):
        self.in_rate = in_rate
        self.out_rate = out_rate

    def upsample_mulaw_to_wav(self, mulaw_bytes: bytes) -> bytes:
        """Converts raw 8kHz mu-law bytes into 16kHz mono 16-bit PCM WAV."""
        if not mulaw_bytes:
            return b""
        pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
        resampled_16k, _ = audioop.ratecv(pcm_8k, 2, 1, self.in_rate, self.out_rate, None)
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.out_rate)
            wf.writeframes(resampled_16k)
        return wav_io.getvalue()

    def upsample_mulaw_to_pcm(self, mulaw_bytes: bytes) -> bytes:
        """Converts raw 8kHz mu-law bytes into 16kHz mono 16-bit PCM raw bytes."""
        if not mulaw_bytes:
            return b""
        pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
        resampled_16k, _ = audioop.ratecv(pcm_8k, 2, 1, self.in_rate, self.out_rate, None)
        return resampled_16k


# =============================================================================
# STAGE 4: Greeting Lock Processor
# =============================================================================
class GreetingLockProcessor:
    """Protects initial introduction from customer pickup noise.

    During the first 3.0 seconds (or while the mandatory opening disclosure is
    being played), coughing, phone handling noise, or early 'hello' bursts
    are locked from interrupting or cutting off the bot's pitch.
    """

    def __init__(self, lock_duration_sec: float = 3.0):
        self.lock_duration_sec = lock_duration_sec
        self.start_time: float = 0.0
        self.is_locked: bool = True

    def start(self) -> None:
        self.start_time = time.time()
        self.is_locked = True

    def check_is_locked(self) -> bool:
        if not self.is_locked:
            return False
        elapsed = time.time() - self.start_time
        if elapsed >= self.lock_duration_sec:
            self.is_locked = False
            return False
        return True

    def unlock_now(self) -> None:
        self.is_locked = False


# =============================================================================
# STAGE 5: Junk & Noise Filter + STT Drop Clarifier
# =============================================================================
class JunkTranscriptFilter:
    """Strips single-character hallucinations, static, and noise tokens."""

    NOISE_PATTERNS = [
        re.compile(r"\[.*?\]"),  # [cough], [applause], [blank_audio]
        re.compile(r"\(.*?\)"),  # (laughter), (music)
        re.compile(r"^[\s.,!?;:\-_'\"`~]+$"),  # Only punctuation
    ]

    CYRILLIC_OR_FOREIGN = re.compile(r"[\u0400-\u04FF\u0600-\u06FF]")

    @classmethod
    def clean(cls, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        cleaned = text.strip()
        for pat in cls.NOISE_PATTERNS:
            cleaned = pat.sub("", cleaned).strip()

        # Reject single character hallucinations (e.g. '.', 'a', 'अ')
        if len(cleaned) <= 1:
            return None

        # Reject telephone static mistakenly decoded as Cyrillic/Arabic
        if cls.CYRILLIC_OR_FOREIGN.search(cleaned):
            return None

        return cleaned if cleaned else None


class STTDropClarifier:
    """Provides courteous retry prompts if audio energy was detected but STT was blank."""

    PROMPTS = {
        "Hindi": "माफ़ कीजिये, आपकी आवाज़ स्पष्ट नहीं आई। क्या आप कृपया फिर से बोल सकते हैं?",
        "Marathi": "क्षमस्व, तुमचा आवाज स्पष्ट आला नाही. तुम्ही पुन्हा सांगू शकता का?",
        "English": "Pardon me, your audio was slightly unclear. Could you please repeat that?",
    }

    @classmethod
    def get_retry_prompt(cls, language: str = "Hindi") -> str:
        return cls.PROMPTS.get(language, cls.PROMPTS["English"])


# =============================================================================
# STAGE 6: User Turn Coalescer
# =============================================================================
class UserTurnCoalescer:
    """Glues sentence fragments into a single coherent thought.

    Indian telecalling borrowers speak in broken phrases:
    ("हाँ..." pause "एक्चुअली..." pause "1985"). Coalesces them so downstream
    NLU and stage transition don't fire prematurely.
    """

    def __init__(self, coalesce_window_sec: float = 0.5):
        self.coalesce_window_sec = coalesce_window_sec
        self.fragments: List[str] = []
        self.last_fragment_time: float = 0.0

    def add_fragment(self, text: str) -> None:
        if not text:
            return
        now = time.time()
        self.fragments.append(text.strip())
        self.last_fragment_time = now

    def should_wait(self) -> bool:
        if not self.fragments:
            return False
        return (time.time() - self.last_fragment_time) < self.coalesce_window_sec

    def flush(self) -> str:
        full_text = " ".join(self.fragments).strip()
        self.fragments = []
        return full_text


# =============================================================================
# STAGE 7: User Context Aggregator
# =============================================================================
class ContextAggregator:
    """Maintains conversational turn history."""

    def __init__(self, customer_id: str, lead_name: str, preferred_lang: str):
        self.customer_id = customer_id
        self.lead_name = lead_name
        self.preferred_lang = preferred_lang
        self.turns: List[Dict[str, Any]] = []

    def add_turn(self, speaker: str, text: str, lang: str, stage: str, latency_ms: int = 0) -> None:
        self.turns.append({
            "speaker": speaker,
            "text": text,
            "lang": lang,
            "stage": stage,
            "latency_ms": latency_ms,
            "timestamp": time.time(),
        })

    def get_history_summary(self) -> str:
        lines = []
        for t in self.turns[-6:]:
            lines.append(f"{t['speaker']}: {t['text']}")
        return "\n".join(lines)


# =============================================================================
# STAGE 8: Single-Pass RAG Injector
# =============================================================================
class RagSinglePassInjector:
    """Injects NBFC loan rules and borrower parameters directly into prompt context.

    Eliminates slow mid-call tool roundtrips.
    """

    @classmethod
    def inject(cls, customer: Dict[str, Any], stage: str) -> Dict[str, Any]:
        cust_name = customer.get("name", "Borrower")
        cid = customer.get("customer_id", "C001")
        birth_year = customer.get("birth_year", 1985)
        lang = customer.get("language", "Hindi")

        rag_context = {
            "entity": "Shaha Finlease Private Limited (RBI-Registered NBFC)",
            "purpose": "Right-Party Contact Confirmation prior to calling",
            "lead_name": cust_name,
            "customer_id": cid,
            "expected_birth_year": birth_year,
            "stage": stage,
            "preferred_language": lang,
            "compliance_rules": [
                "Rule 1: Never disclose loan details, debt, or EMI to third parties or unverified persons.",
                "Rule 2: Zero abusive language, threats, or harassment under any circumstances.",
                "Rule 3: Respect calling hours (8:00 AM to 7:00 PM IST strictly).",
                "Rule 4: Do not disclose expected birth year under any condition. Borrower must supply it.",
                "Rule 5: If customer requests callback, record exact date/time politely.",
                "Rule 6: If customer requests Do Not Call, immediately acknowledge and mark flag.",
            ],
        }
        return rag_context


# =============================================================================
# STAGE 9: LLM Trigger Debounce
# =============================================================================
class LLMTriggerDebounce:
    """Stops double replies if two speech frames fire within 150ms."""

    def __init__(self, debounce_ms: int = 150):
        self.debounce_ms = debounce_ms
        self.last_trigger_ms: float = 0.0

    def allow(self) -> bool:
        now_ms = time.time() * 1000.0
        if (now_ms - self.last_trigger_ms) < self.debounce_ms:
            return False
        self.last_trigger_ms = now_ms
        return True


# =============================================================================
# STAGE 11: Meta-Narration Filter
# =============================================================================
class MetaNarrationFilter:
    """Drops accidental LLM thinking tags, meta-commentary, or chain-of-thought."""

    THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
    META_PREFIXES = [
        re.compile(r"^(The customer has confirmed.*?:?\s*)", re.IGNORECASE),
        re.compile(r"^(The borrower said.*?:?\s*)", re.IGNORECASE),
        re.compile(r"^(Assistant note.*?:?\s*)", re.IGNORECASE),
        re.compile(r"^(Internal thought.*?:?\s*)", re.IGNORECASE),
        re.compile(r"^(Thinking:?\s*)", re.IGNORECASE),
        re.compile(r"^(Thought:?\s*)", re.IGNORECASE),
    ]

    @classmethod
    def filter_text(cls, text: str) -> str:
        if not text:
            return ""
        cleaned = cls.THINK_BLOCK.sub("", text)
        for pat in cls.META_PREFIXES:
            cleaned = pat.sub("", cleaned)
        # Strip markdown bolding / headers which sound unnatural in TTS
        cleaned = cleaned.replace("**", "").replace("###", "").replace("##", "")
        return cleaned.strip()


# =============================================================================
# STAGE 12: Text Sanitizer & Numeral Fixer
# =============================================================================
class TTSTextSanitizer:
    """Normalizes currency, percentages, numerals and replaces harsh punctuation."""

    @classmethod
    def sanitize(cls, text: str, language: str = "Hindi") -> str:
        if not text:
            return ""
        s = text
        # Replace aggressive exclamations with polite periods
        s = s.replace("!", ".")

        if language == "Hindi":
            # Currency formatting
            s = re.sub(r"₹\s*([0-9,]+)", r"\1 रुपये", s)
            s = re.sub(r"Rs\.?\s*([0-9,]+)", r"\1 रुपये", s, flags=re.IGNORECASE)
            # Percentages
            s = re.sub(r"([0-9.]+)\s*%", r"\1 प्रतिशत", s)
        elif language == "Marathi":
            s = re.sub(r"₹\s*([0-9,]+)", r"\1 रुपये", s)
            s = re.sub(r"Rs\.?\s*([0-9,]+)", r"\1 रुपये", s, flags=re.IGNORECASE)
            s = re.sub(r"([0-9.]+)\s*%", r"\1 टक्के", s)
        else:
            s = re.sub(r"₹\s*([0-9,]+)", r"\1 rupees", s)
            s = re.sub(r"Rs\.?\s*([0-9,]+)", r"\1 rupees", s, flags=re.IGNORECASE)
            s = re.sub(r"([0-9.]+)\s*%", r"\1 percent", s)

        # Remove extra whitespace
        s = re.sub(r"\s+", " ", s).strip()
        return s


# =============================================================================
# STAGE 13: Language Auto-Switcher
# =============================================================================
class LanguageAutoSwitcher:
    """Inspects Unicode code points to detect Marathi, Hindi, or English on the fly."""

    MARATHI_LETTER_LLA = "\u0933"  # ळ
    MARATHI_KEYWORDS = {"नमस्कार", "कसा", "आहात", "सांगा", "माहिती", "हो", "नाही", "नको", "धन्यवाद", "पाहिजे", "बोलत"}
    DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]")

    @classmethod
    def detect_language(cls, text: str, default_lang: str = "Hindi") -> str:
        if not text:
            return default_lang

        # 1. Check for Marathi exclusive character ळ
        if cls.MARATHI_LETTER_LLA in text:
            return "Marathi"

        # 2. Check for Marathi lexical markers
        if any(kw in text for kw in cls.MARATHI_KEYWORDS):
            return "Marathi"

        # 3. Check for Devanagari script (Hindi/Marathi)
        dev_matches = cls.DEVANAGARI_RANGE.findall(text)
        if len(dev_matches) >= 3:
            return "Hindi"

        # 4. Check for Latin / English
        latin_matches = re.findall(r"[a-zA-Z]", text)
        if len(latin_matches) > len(dev_matches):
            return "English"

        return default_lang

    @classmethod
    def get_neural_tts_voice(cls, language: str) -> str:
        """Returns the optimal Microsoft Edge neural TTS voice for the language."""
        if language == "Marathi":
            return "mr-IN-AarohiNeural"
        elif language == "English":
            return "en-IN-NeerjaNeural"
        return "hi-IN-SwaraNeural"


# =============================================================================
# STAGE 3 & BARGE-IN: MinWordsInterruptionStrategy (Echo Guard)
# =============================================================================
class MinWordsInterruptionStrategy:
    """Echo Guard: Prevents smartphone speakerphone echo from falsely interrupting bot.

    The AI's voice leaking into the phone microphone transcribes to 0 or 1 words.
    Only interrupts if STT transcribes at least min_words genuine words.
    """

    def __init__(self, min_words: int = 2):
        self.min_words = min_words

    def should_interrupt(self, transcript: str) -> bool:
        if not transcript:
            return False
        # Strip punctuation
        cleaned = re.sub(r"[^\w\s]", "", transcript).strip()
        words = cleaned.split()
        return len(words) >= self.min_words


# =============================================================================
# STAGE 15: Server-Side Stereo Audio Recorder (Left=User, Right=Bot)
# =============================================================================
class AudioBufferProcessor:
    """Dual-channel stereo recorder for 100% compliant call archiving.

    Left Channel (Track 1): Borrower's microphone.
    Right Channel (Track 2): Voicebot AI assistant audio.
    """

    def __init__(self, sample_rate: int = 8000):
        self.sample_rate = sample_rate
        self.left_chunks: List[bytes] = []   # 16-bit linear PCM (User)
        self.right_chunks: List[bytes] = []  # 16-bit linear PCM (Bot)

    def record_user_mulaw(self, mulaw_bytes: bytes) -> None:
        """Appends incoming user mu-law audio to left channel."""
        if not mulaw_bytes:
            return
        pcm = audioop.ulaw2lin(mulaw_bytes, 2)
        self.left_chunks.append(pcm)

    def record_bot_mulaw(self, mulaw_bytes: bytes) -> None:
        """Appends outgoing bot mu-law audio to right channel."""
        if not mulaw_bytes:
            return
        pcm = audioop.ulaw2lin(mulaw_bytes, 2)
        self.right_chunks.append(pcm)

    def generate_stereo_wav(self) -> bytes:
        """Interleaves left and right channels into a compliant stereo WAV file."""
        left_pcm = b"".join(self.left_chunks)
        right_pcm = b"".join(self.right_chunks)

        num_l = len(left_pcm) // 2
        num_r = len(right_pcm) // 2
        total_samples = max(num_l, num_r)

        if total_samples == 0:
            return b""

        l_unpacked = struct.unpack(f"<{num_l}h", left_pcm) if num_l > 0 else ()
        r_unpacked = struct.unpack(f"<{num_r}h", right_pcm) if num_r > 0 else ()

        interleaved = bytearray()
        for i in range(total_samples):
            l_val = l_unpacked[i] if i < num_l else 0
            r_val = r_unpacked[i] if i < num_r else 0
            interleaved.extend(struct.pack("<hh", l_val, r_val))

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes(interleaved))

        return wav_io.getvalue()


# =============================================================================
# FAIL-SAFES: Call Silence Watchdog
# =============================================================================
class CallSilenceWatchdog:
    """Monitors continuous caller silence and triggers gentle nudge or disconnection."""

    def __init__(
        self,
        nudge_seconds: float = 10.0,
        disconnect_seconds: float = 15.0,
        on_nudge: Optional[Callable[[], Any]] = None,
        on_disconnect: Optional[Callable[[], Any]] = None,
    ):
        self.nudge_seconds = nudge_seconds
        self.disconnect_seconds = disconnect_seconds
        self.on_nudge = on_nudge
        self.on_disconnect = on_disconnect

        self.last_speech_time = time.time()
        self.nudged = False
        self.disconnected = False
        self.active = False

    def reset_speech_timer(self) -> None:
        self.last_speech_time = time.time()
        self.nudged = False

    def start(self) -> None:
        self.active = True
        self.last_speech_time = time.time()
        self.nudged = False
        self.disconnected = False

    def stop(self) -> None:
        self.active = False

    def check_silence(self) -> Tuple[bool, bool]:
        """Checks silence status. Returns (trigger_nudge, trigger_disconnect)."""
        if not self.active or self.disconnected:
            return False, False
        elapsed = time.time() - self.last_speech_time

        if elapsed >= self.disconnect_seconds and not self.disconnected:
            self.disconnected = True
            if self.on_disconnect:
                self.on_disconnect()
            return False, True

        if elapsed >= self.nudge_seconds and not self.nudged:
            self.nudged = True
            if self.on_nudge:
                self.on_nudge()
            return True, False

        return False, False


# =============================================================================
# METRICS TAP: Latency & SLA Telemetry
# =============================================================================
class MetricsTap:
    """Measures and logs turn-by-turn SLA metrics."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self.turn_records: List[Dict[str, Any]] = []

    def record_turn_metrics(
        self,
        turn_no: int,
        stt_latency_ms: int,
        nlu_latency_ms: int,
        tts_latency_ms: int,
        guardrail_safe: bool,
        language: str,
    ) -> Dict[str, Any]:
        total_ms = stt_latency_ms + nlu_latency_ms + tts_latency_ms
        sla_met = total_ms < 3000

        record = {
            "call_id": self.call_id,
            "turn_no": turn_no,
            "stt_ms": stt_latency_ms,
            "nlu_ms": nlu_latency_ms,
            "tts_ms": tts_latency_ms,
            "total_e2e_ms": total_ms,
            "sla_met_under_3s": sla_met,
            "guardrail_safe": guardrail_safe,
            "language": language,
        }
        self.turn_records.append(record)
        return record
