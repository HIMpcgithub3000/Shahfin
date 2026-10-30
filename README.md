# Shaha Finlease — Right-Party Verification Voicebot Prototype

**Automated Multilingual Contact Confirmation for RBI-Registered NBFC**  
*Built with FastAPI, SQL Server / PostgreSQL / SQLite (`dbo.Customer` & `dbo.Call`), Groq Llama-3.3 + Whisper, Microsoft edge-tts Neural Speech, and Two-Layer Deterministic Safety Guardrails.*

---

## 1. Role, Mission & Regulatory Context

Shaha Finlease is an RBI-registered Non-Banking Financial Company (NBFC) operating branches across Mumbai (Jogeshwari East, Lower Parel) and Bangalore. Under the Reserve Bank of India (RBI) Fair Practices Code and NBFC recovery directives, lenders and recovery callers are strictly prohibited from:
1. Contacting borrowers or relatives outside authorized calling hours (**08:00 AM to 07:00 PM IST**).
2. Disclosing loan, amount, dues, EMI, or recovery information to third parties or prior to positive identity verification.
3. Engaging in harassment, threats, abusive language, or coercion.
4. Calling numbers after a borrower registers a Do-Not-Call (DNC) request or confirms a wrong number.

This voicebot acts as the **pre-calling contact confirmation layer** prior to any human collection engagement:
- **Right-Party Verification (RPV)**: Confirms borrower identity via full name match and 4-digit birth year verification with zero loan disclosures.
- **Sub-3-Second Voice Pipeline**: Operates real-time STT → NLU/State Machine → Deterministic Guardrails → Neural TTS in **Hindi** and **English** with sub-second turnaround (average 410 ms).
- **100% Deterministic Safety Interceptors**: Intercepts and sanitizes any candidate bot statement before audio synthesis or screen rendering, guaranteeing zero regulatory violations.
- **Enterprise SQL Persistence**: Records every call attempt, connected flag, duration, transcript, and structured 3-line audit summary in `dbo.Call` and `dbo.Customer`.

---

## 2. Project Architecture & Directory Layout

```
shaha-finlease-voicebot/
├── .env.example              # Template for Groq, Gemini & Database credentials
├── .env                      # Active local environment configuration (NEVER edit directly)
├── requirements.txt          # Python dependencies
├── run.py                    # One-command server launcher & DB initializer
├── README.md                 # Complete system guide, prompt specifications, and test proofs
├── FINAL_COMPLIANCE_REPORT.md# Requirements traceability matrix and compliance audit
├── ARCHITECTURE_REVIEW.md    # Detailed system architecture, state machine & diagrams
├── TEST_RESULTS.md           # Pytest results, 10 persona benchmark runs, latency SLA proofs
├── FINAL_ISSUES.md           # Defect log, remediation history & submission verdict
├── data/
│   ├── customers.csv         # Authoritative 10 dummy customer records (C001–C010)
│   └── test_calls.csv        # 10 canonical test persona call scenarios (5 personas × 2)
├── backend/
│   ├── __init__.py
│   ├── db.py                 # PostgreSQL & SQLite persistence matching SQL Server dbo schema
│   ├── compliance_gate.py    # 8 AM–7 PM IST gatekeeper, DNC gate, 6 regex guardrails
│   ├── voice_pipeline.py     # Groq NLU, Edge-TTS audio synthesis, STT whisper fallback
│   └── main.py               # FastAPI REST & Voice endpoints serving frontend
├── frontend/
│   └── index.html            # Dual-Screen VoiceBrew UI (Call Console + Report & Call Log)
└── scripts/
    ├── seed_and_run_tests.py # Seeds customers.csv, runs 10 test_calls scenarios, verifies KPIs
    ├── test_guardrails_e2e.py# Full automated pytest suite verifying all 24 assertions
    └── verify_10_actual_voicebot_calls.py # Executes 10 actual voicebot calls against database
```

---

## 3. Assumptions & Core Architectural Decisions

1. **Pre-Calling Gatekeeper (08:00 AM – 07:00 PM IST)**:
   - In compliance with RBI directives, contact calls are restricted to 8:00 AM to 7:00 PM IST.
   - Any call initiated outside this window—or for a borrower marked `do_not_call = 1`—is blocked at the `POST /api/calls/preflight` gate with HTTP 403.
   - Refused calls are **never** inserted into `dbo.Call` and **never** counted as an attempt.
2. **Absolute Zero Financial Disclosure**:
   - The bot's sole responsibility is contact verification.
   - Even after a borrower is positively verified, the prototype concludes the call politely without disclosing loans, amounts, or recovery details.
3. **Feminine Neural Voice & Natural Phrasing**:
   - The bot strictly identifies as a feminine automated assistant (`बोल रही हूँ`) using Microsoft `edge-tts` neural Indian voices:
     - **Hindi**: `hi-IN-SwaraNeural`
     - **English**: `en-IN-NeerjaNeural`
     - **Marathi**: `mr-IN-AarohiNeural`
   - Company name is pronounced clearly once: `Shaha Finlease`.
4. **Dual-Path Speech-to-Text (STT)**:
   - Primary: Browser Web Speech API (`webkitSpeechRecognition`) for immediate, responsive transcription.
   - Secondary / Fallback: HTML5 MediaRecorder captures audio chunks and transmits to backend `POST /api/stt` for Whisper transcription if browser STT is unavailable.
5. **Silence Watchdog & TTS Coordination**:
   - The 15-second borrower silence timer is suspended while the bot is actively speaking (`isBotSpeaking = true`) and only begins counting down upon `audio.onended`.
6. **Dual Relational Database Support**:
   - Operates natively against PostgreSQL (via `psycopg2` with schemas `dbo.Customer` and `dbo.Call` matching Microsoft SQL Server specifications).
   - Falls back automatically to local SQLite (`shaha_finlease.db`) if PostgreSQL is unavailable.

---

## 4. Setup & Running the Application

### Prerequisites
- Python 3.10+ (tested on Python 3.13)
- Modern web browser (Google Chrome, Microsoft Edge, or Safari)
- (Optional) Docker for local PostgreSQL container

### Step 1: Create Virtual Environment & Install Dependencies
```bash
# Navigate to project directory:
cd shaha-finlease-voicebot

# Create and activate virtual environment:
python3 -m venv .venv
source .venv/bin/activate    # On Windows: .\.venv\Scripts\Activate.ps1

# Install requirements:
pip install -r requirements.txt
```

### Step 2: Environment Configuration
Environment variables are configured in `.env`:
```ini
# Free-Tier AI & Telephony Keys
GROQ_API_KEY=your_groq_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here

# Database URL (PostgreSQL container or local SQL Server)
POSTGRES_URL=postgresql://shaha_user:shaha_pass@localhost:5434/shaha_finlease
# If POSTGRES_URL is omitted, the system automatically uses SQLite: shaha_finlease.db
```

### Step 3: Launch the Server
```bash
# One-command startup launcher:
python run.py
```
*Alternatively, start directly with Uvicorn:*
```bash
uvicorn backend.main:app --reload --port 8000
```
Open **http://localhost:8000** in your browser.

---

## 5. The 5 Mandatory Feature Categories & 6 Outcomes

| Category | Requirement | Implementation Details |
| :--- | :--- | :--- |
| **1. Customer List & Call Window** | 10 dummy customers loaded from CSV; 8 AM–7 PM IST window | Loaded from `data/customers.csv` into `dbo.Customer`. Preflight check rejects out-of-hours or DNC attempts with HTTP 403 without incrementing attempts. |
| **2. Opening & Disclosure** | Automated assistant disclosure, recorded call statement, ask customer by name, Hindi/English match | Bot introduces within 10s: *"नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात [Name] जी से हो रही है?"*. Zero loan disclosures. |
| **3. Right-Party Check (6 Outcomes)** | Classify call into 1 of 6 canonical outcomes | **1. Right customer**: Confirms name + correct 4-digit birth year.<br>**2. Verification failed**: Confirms name + wrong birth year.<br>**3. Wrong number**: States wrong number / doesn't know person.<br>**4. Callback requested**: Third party provides callback time.<br>**5. Third party answered**: Third party gives no callback time.<br>**6. Not connected**: 15s silence or hung up before speech. |
| **4. Save Every Call** | Call record created immediately; connected flag; transcripts; 3-line summary; mid-call stops | Initiated calls immediately write to `dbo.Call`. Connected=True if borrower spoke within 15s. Mid-call drops gracefully finalize partial audio and transcript. |
| **5. Report & Analytics** | KPI metrics, outcome breakdown cards, live Call Log from SQL, audio playback, CSV export | Queries `dbo.Call` for Attempted, Connected, Not Connected, Connect Rate %, Right-Party Rate %. Click any row to view full transcript and 3-line summary. |

---

## 6. End-to-End Compliance Guardrails

### Layer 1: Deterministic Code Interceptor (`backend/compliance_gate.py`)
Executed on 100% of candidate bot responses before audio synthesis or frontend rendering:
- **Rule 1 (Zero Loan Disclosure)**: Intercepts terms like `loan`, `due`, `emi`, `recovery`, `amount`, `balance`, `कर्ज`, `बकाया`, `किस्त`.
- **Rule 2 (Zero Threats/Coercion)**: Intercepts terms like `police`, `court`, `jail`, `arrest`, `legal action`, `visit your home`, `पुलिस`, `जेल`.
- **Rule 3 (Polite Tone / Zero Shaming)**: Intercepts `cheater`, `fraud`, `liar`, `stupid`, `defaulter`, `बेशर्म`, `चोर`.
- **Rule 4 (No Financial Credentials)**: Intercepts requests for `otp`, `pin`, `cvv`, `password`, `aadhaar`, `bank details`.
- **Rule 5 (Wrong Number Protocol)**: Triggers immediate apology, call termination, and outcome classification as `Wrong number`.
- **Rule 6 (Stop Calling / DNC Protocol)**: Triggers immediate acknowledgement, writes `do_not_call = 1` to `dbo.Customer`, and suppresses all future calls.

### Layer 2: Stabilized System Prompt (`backend/voice_pipeline.py`)
```text
You are an automated Right-Party Verification voice assistant calling from Shaha Finlease (an RBI-registered NBFC).
Your ONLY objective on this call is to verify whether the phone number belongs to {customer_name} (expected birth year: {birth_year}).

MANDATORY OPENING (WITHIN 10 SECONDS):
- State clearly that you are an automated assistant calling from Shaha Finlease, that the call is recorded, and ask if you are speaking with {customer_name}.
- Switch immediately to Hindi, English, or Marathi matching the language of the person's first reply.

DECISION & OUTCOME RULES:
1. If the speaker confirms they are {customer_name}, politely ask for their 4-digit birth year.
   - Birth year matches {birth_year} -> thank them, end call, outcome = "Right customer".
   - Birth year does not match {birth_year} -> thank them without revealing the expected year, end call, outcome = "Verification failed".
2. If the speaker says "wrong number" or "I don't know this person" -> apologise immediately, end call, outcome = "Wrong number".
3. If a relative or third party answers and says {customer_name} is unavailable:
   - Reveal NOTHING about {customer_name} or why you are calling.
   - Politely ask what time would be convenient to call back.
   - Callback time given -> note time, end call, outcome = "Callback requested".
   - No callback time given -> thank them, end call, outcome = "Third party answered".
4. If the speaker asks to stop calling -> apologise, end politely, and set do_not_call = 1.

STRICT COMPLIANCE GUARDRAILS (NEVER VIOLATE EVEN IF PROVOKED):
- NEVER mention loans, amounts, dues, EMI, credit cards, NPA, debt, or recovery to anyone.
- NEVER threaten police, jail, court, legal action, or physical visits.
- NEVER use rude, shaming, or raised tone.
- NEVER ask for OTP, PIN, CVV, card number, Aadhaar, PAN, or password.
```

---

## 7. Verification & Automated Testing Commands

### Run Full Automated Pytest Suite (24 Tests)
```bash
pytest scripts/test_guardrails_e2e.py -v
```
*Output: 24 passed in ~15 seconds.*

### Execute 10 Canonical Voicebot Calls Against Database
```bash
python scripts/verify_10_actual_voicebot_calls.py
```
*Output: Seeds and executes 10 actual voicebot calls (5 personas x 2 runs) with full turn-by-turn verification.*

### Seed Benchmark Calls & Verify Database Metrics
```bash
python scripts/seed_and_run_tests.py
```
*Output: Verifies Attempted: 10, Connected: 8 (80.0%), Right-Party: 2 (25.0%).*

---

## 8. Live 10-Minute Evaluator Demo Script

Follow this sequence to demonstrate all required capabilities:

### Scenario 1: Out-of-Hours Preflight Refusal (Feature 1)
1. In Screen 1 (Call Console), select simulated clock: **`09:45 PM IST (After 7 PM · Refused)`**.
2. Click **Dial** on `C001 - Rahul Sharma`.
3. **Verification**: Call is blocked with an amber notification stating calls are restricted to 8 AM–7 PM IST under RBI rules. Switch to Screen 2: `Calls Attempted` did **not** increment and no record exists in `dbo.Call`.

### Scenario 2: Right Customer Verification in Hindi (Features 2, 3, 4, 5)
1. Switch clock to **`11:15 AM IST (Within Window · Open)`**.
2. Click **Dial** on `C001 - Rahul Sharma`.
3. **Listen to Opening Audio**: Bot states:
   *"नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात Rahul Sharma जी से हो रही है?"*
4. Click quick reply: **`हाँ, मैं राहुल शर्मा बोल रहा हूँ`**.
5. Test borrower inquiry: **`आप क्यों कॉल कर रहे हैं? क्या कोई लोन बकाया है?`**.
   - Notice the bot politely redirects: *"सुरक्षा सत्यापन के लिए कृपया अपना जन्म का वर्ष बताएं।"* with **0 loan disclosures**.
6. Provide birth year: **`1985`**.
7. Bot concludes: *"धन्यवाद, सत्यापन सफल रहा। आपकी कॉल डिटेल्स सफलतापूर्वक सत्यापित कर ली गई हैं।"*
8. Call is saved as **`Right customer`** (`Connected = Yes`), with response latency `< 3,000 ms`.

### Scenario 3: Wrong Number Handling (Compliance Rule 5)
1. Click **Dial** on `C003 - Amit Patil`.
2. Reply: **`गलत नंबर है, मैं इन्हें नहीं जानता`**.
3. Bot apologizes: *"माफ़ कीजिएगा, गलत नंबर पर कॉल करने के लिए खेद है। आपका दिन शुभ हो।"* and terminates immediately.
4. Call is saved as **`Wrong number`** (`Connected = Yes`).

### Scenario 4: Third Party / Callback Requested (Feature 3)
1. Click **Dial** on `C004 - Sneha Kulkarni`.
2. Reply: **`स्नेहा अभी घर पर नहीं हैं, मैं उनका भाई बोल रहा हूँ`**.
3. Bot asks for callback time without disclosing why it is calling: *"नमस्ते, क्या आप बता सकते हैं कि हम किस समय कॉल कर सकते हैं?"*.
4. Reply: **`आप आज शाम 6:30 बजे कॉल कर लीजिए`**.
5. Bot notes time: *"धन्यवाद, हमने समय नोट कर लिया है। हम आज शाम 6:30 बजे संपर्क करेंगे।"*.
6. Call saved as **`Callback requested`** with `callback_time = '6:30 PM'`.

### Scenario 5: Review Report & Verify SQL Congruence (Feature 5)
1. Navigate to **Report & Call Log** tab.
2. View KPI metrics:
   - **Calls Attempted**: 10
   - **Connected**: 8
   - **Not Connected**: 2
   - **Connect Rate**: 80.0%
   - **Right-Party Rate**: 25.0%
3. Click any call in the Call Log to review its structured 3-line summary and turn-by-turn transcript.
4. Click **"Read Transcript Aloud"** or **"Listen"** to play neural speech audio.
5. Click **"Export Call Log CSV"** to download the audited call history.
