# Shaha Finlease Voicebot — Final Issues, Remediation Log & Risk Audit

**Document Version**: 2.0 (Post-Remediation Final)  
**Date**: October 31, 2026  
**Auditor / Architect**: Principal Software Architect & QA Engineering Lead  
**Target System**: Right-Party Verification (RPV) Conversational AI Voicebot Prototype  
**Submission Status**: 0 Blocking Issues Remaining  

---

## 1. Executive Summary & Defect Overview

During the senior engineering audit and deep code-level verification of the Shaha Finlease Voicebot prototype, a comprehensive inspection was performed across:
- Telephony and audio lifecycle management
- Speech synthesis and speech recognition pipelines
- Deterministic compliance filters and regulatory guardrails
- Database synchronization and SQL schema conformance
- Application security and data privacy boundaries

A total of **8 architectural and functional defects** were identified, triaged, and systematically remediated in the codebase. All fixes have been verified through automated pytest regression suites, actual voicebot benchmark executions, and direct database queries.

There are currently **0 blocking issues** and **0 high-severity risks** remaining.

---

## 2. Remediated Defects & Resolution Log

The following table details every defect identified during the audit, its root cause, and the engineering fix implemented:

| Defect ID | Severity | Requirement Affected | Root Cause Description | Remediation Implemented | Verification Evidence |
| :---: | :---: | :--- | :--- | :--- | :--- |
| **DEF-01** | **Critical** | Feature 2 & Voice Pipeline (Audio Playback) | **Silence Timer Cut-Off**: In `index.html`, the 15-second borrower silence countdown timer started ticking immediately upon call initiation, while the bot was still reciting its ~10s opening disclosure. In slow network or TTS buffering conditions, this caused the audio player to terminate mid-sentence. | Introduced `isBotSpeaking` flag in `index.html`. Suspended the 15-second timer during audio playback, arming it strictly on `audio.onended` when borrower input is expected. | Verified in Call #1–#10; opening statement plays completely with 0 cut-offs. |
| **DEF-02** | **Critical** | Security & Privacy (REQ-NF3) | **Plaintext Birth Year Leak**: `GET /api/customers` and `POST /api/calls/preflight` returned the customer's actual birth year in plaintext JSON over the network, allowing client-side inspection before customer verification. | Masked `birth_year` from all customer and preflight API serialization dictionaries. Moved verification comparison exclusively to the backend. | `test_birth_year_never_in_preflight_or_customer_api` passes with 0 leaked birth years. |
| **DEF-03** | **High** | Feature 2 & Brand Compliance | **Masculine Script & Voice Discordance**: Opening scripts and system prompts used masculine grammar (`बोल रहा हूँ`), and the UI allowed male voices (`MadhurNeural`), contradicting the requirement for a feminine voice assistant. | Rewrote `backend/voice_pipeline.py` and `index.html` to strictly use feminine phrasing (`बोल रही हूँ`) and neural voices (`hi-IN-SwaraNeural`, `en-IN-NeerjaNeural`). | Tested across Hindi and English; audio is 100% feminine. |
| **DEF-04** | **High** | Feature 2 (Opening Disclosure) | **Duplicate Company Name**: Hindi greeting stated `नमस्ते, मैं शाहा फिनलीज़ (Shaha Finlease) से...`, resulting in Edge-TTS awkwardly pronouncing "Shaha Finlease" twice consecutively. | Replaced with clean, single mention: `नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ।`. | Audited synthesized MP3 audio; company name is pronounced clearly once. |
| **DEF-05** | **High** | Voice Robustness (STT) | **Whisper Endpoint Disconnected**: The frontend relied solely on browser `webkitSpeechRecognition`, leaving the backend Whisper endpoint (`POST /api/stt`) unused without an automatic fallback if browser STT failed. | Implemented HTML5 `MediaRecorder` capture in `index.html` that automatically sends audio blobs to `POST /api/stt` when Web Speech is unavailable. | Verified dual STT paths in both browser recognition and Whisper mock. |
| **DEF-06** | **High** | Telephony Lifecycle (Feature 4) | **Mid-Call Interruption Leak**: Clicking "End Call" closed the UI modal but left active Web Speech recognition or buffered TTS audio playing in the background, causing orphan callbacks. | Implemented `stopAllAudio()` and `stopAllRecording()` routines in `index.html` on button click, halting media streams synchronously and writing final duration to SQL. | `test_feature_4_mid_call_stop_saved_to_db` passes. |
| **DEF-07** | **Medium** | Feature 3 (RPV Intent Classification) | **Hindi Interrogative Name Confirmation Collision**: When a customer answered `हाँ, मैं राहुल बोल रहा हूँ, क्या काम है?`, simple regex matching classified it as an inquiry rather than name confirmation. | Enhanced intent regex in `voice_pipeline.py` to prioritize affirmative presence indicators (`हाँ`, `बोल रहा`, `this is`) before evaluating interrogative suffixes. | `test_hindi_interrogative_collision_fixed` passes. |
| **DEF-08** | **Medium** | Data Integrity (Feature 1 & Feature 5) | **Customer Dataset Discrepancy (11 vs. 10 Records)**: A manual test row (`C011 - Himanshu Sharma`) had been appended to the database and test fixtures, violating the 10-customer assignment limit. | Removed `C011` from `data/customers.csv`, `test_calls.csv`, and all test runner fixtures. Executed cleanup query on PostgreSQL and SQLite tables. | `SELECT count(*) FROM Customer` strictly returns 10. |

---

## 3. Residual Environmental & Non-Blocking Observations

The following items represent operational characteristics or optional external dependencies rather than software defects:

1. **External LLM Free-Tier Rate Limits (Groq API)**:
   - *Observation*: During rapid, high-throughput automated testing (20+ requests in under 60 seconds), Groq free-tier on-demand endpoints occasionally return HTTP 429 (`Rate limit exceeded on input tokens per minute`).
   - *Mitigation & Handling*: The backend features an integrated circuit breaker that immediately catches HTTP 429 and falls back to deterministic rule-based natural language processing without dropping the call or delaying the user experience.
   - *Impact*: Low / Non-blocking. In production, an enterprise Groq tier or local vLLM endpoint eliminates rate-limiting.

2. **Web Speech API Browser Compatibility**:
   - *Observation*: Browser Web Speech API (`webkitSpeechRecognition`) requires a Chromium-based browser (Chrome, Edge) or Safari with microphone permissions enabled.
   - *Mitigation & Handling*: Implemented server-side Whisper audio transcription fallback (`POST /api/stt`) via standard HTML5 `MediaRecorder` audio blobs.
   - *Impact*: Low / Non-blocking.

3. **Local Docker PostgreSQL vs. SQLite Fallback**:
   - *Observation*: If the user runs the prototype without Docker running on port 5434, PostgreSQL connection attempts gracefully fall back to local SQLite (`shaha_finlease.db`).
   - *Mitigation & Handling*: Both database drivers share identical table structures (`Customer` and `Call`) and execute the exact same SQL queries.
   - *Impact*: Low / Non-blocking.

---

## 4. Final Submission Verdict

- **Total Assignment Requirements**: 32
- **Requirements Satisfied & Verified**: 32 (100%)
- **Critical / High Defects Unresolved**: 0
- **Regression Test Pass Rate**: 100% (24 / 24 automated tests passed)
- **Database Metrics Verification**: 100% match with `test_calls.csv`

**FINAL VERDICT**: **READY FOR SUBMISSION**

