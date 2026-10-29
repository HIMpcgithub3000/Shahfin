# Shaha Finlease — Right-Party Verification Voicebot Prototype

**Automated Multilingual Contact Confirmation for RBI-Registered NBFC**  
*Built with FastAPI, SQL Server / SQLite (`dbo.Customer` & `dbo.Call`), Groq Llama-3.3 + Whisper, Google Gemini 2.5 Flash, Microsoft edge-tts, and Two-Layer Deterministic Safety Guardrails.*

---

## 1. Role, Mission & Target Output

Shaha Finlease is an RBI-registered NBFC operating branches in Jogeshwari (Mumbai), Lower Parel (Mumbai), and Bangalore. Under RBI Fair Practices Code and NBFC recovery directives, lenders are strictly forbidden from disclosing debt details to third parties, making abusive or threatening calls, contacting borrowers outside permitted calling hours (08:00 AM to 07:00 PM IST), or violating Do-Not-Call (DNC) requests.

This voicebot acts as the **pre-calling contact confirmation layer** before any loan recovery workflow:
1. **Verifies Right-Party Contact**: Confirms borrower identity via full name and 4-digit birth year check without ever mentioning loans, dues, EMI, balance, or collection.
2. **Sub-3-Second Multilingual Voice Loop**: Runs real-time STT → LLM/NLU → Guardrail Filter → TTS in **Hindi**, **English**, and **Marathi** with latency under 3,000 ms.
3. **100% Deterministic Code-Level Safety Guardrails**: Intercepts and rewrites any violation of the 6 compliance rules before audio synthesis or UI display.
4. **One-Command Local Setup**: Runs locally at `http://localhost:8000` with zero configuration, featuring an automatic SQLite database fallback if local Microsoft SQL Server is not active.

---

## 2. Project Directory Structure

```
shaha-finlease-voicebot/
├── .env.example              # Template for Groq, Gemini & SQL Server credentials
├── .env                      # Active local configuration (zero-config SQLite fallback)
├── requirements.txt          # Python dependencies
├── run.py                    # One-command server launcher & DB initializer
├── README.md                 # Complete system guide, prompt specifications, and test proofs
├── data/
│   ├── customers.csv         # 10 dummy customer records (C001–C010)
│   └── test_calls.csv        # 10 test persona call scenarios (5 personas × 2)
├── backend/
│   ├── __init__.py
│   ├── db.py                 # dbo.Customer & dbo.Call schema with pyodbc + SQLite fallback
│   ├── compliance_gate.py    # 8 AM–7 PM IST gatekeeper, DNC gate, 6 regex guardrails
│   ├── voice_pipeline.py     # Groq STT/LLM, Gemini circuit-breaker, edge-tts audio
│   └── main.py               # FastAPI REST & Voice endpoints serving frontend/index.html
├── frontend/
│   └── index.html            # Two-Screen VoiceBrew UI (Call Console + Report & Call Log)
└── scripts/
    ├── seed_and_run_tests.py # Seeds customers.csv, runs 10 test_calls scenarios, verifies KPIs
    └── test_guardrails_e2e.py# Full automated pytest suite verifying all 5 features & 6 rules
```

---

## 3. Assumptions & Architectural Design Decisions

1. **Pre-Calling Gatekeeper (Calling Window 08:00 AM – 07:00 PM IST)**:
   - Under RBI NBFC directives, collection and contact calls are restricted to 8:00 AM to 7:00 PM IST.
   - Any call initiated outside this window—or for a borrower marked `do_not_call = 1`—is rejected immediately at the `POST /api/calls/preflight` gate with HTTP 403 Forbidden.
   - **Crucial Rule**: Refused calls are **NEVER** inserted into `dbo.Call` and **NEVER** counted as an attempt.

2. **Absolute Zero Debt Disclosure**:
   - The bot's sole responsibility is **Right-Party Verification** (confirming full name and 4-digit birth year).
   - Under RBI Fair Practices Code, lenders must never disclose debt details to relatives, third parties, or even the borrower before positive verification. The bot never mentions loan amounts, EMI, dues, or collection to anyone.

3. **Multi-Dialect & Dynamic Language Matching**:
   - Opens in the customer's preferred language (defaulting to Hindi).
   - Dynamically adapts to English or Marathi if the borrower speaks in those languages.
   - Synthesizes speech with sub-second latency using Microsoft `edge-tts` neural Indian voices (`hi-IN-SwaraNeural`, `en-IN-NeerjaNeural`, `mr-IN-AarohiNeural`).

4. **Database Architecture & Zero-Config Fallback**:
   - Fully supports **PostgreSQL** (`psycopg2-binary`) with tables in schema `dbo` (`dbo.Customer` and `dbo.Call`).
   - Automatically falls back to built-in SQLite (`shaha_finlease.db`) if PostgreSQL is not active, ensuring the project always boots without friction.

5. **Deterministic Code-Level Guardrails**:
   - Because LLMs can hallucinate or be jailbroken by adversarial user prompts, 6 code-level regex guardrails in Python execute on 100% of turns before sending audio to TTS or rendering in the UI.

---

## 4. Quickstart & Local Setup

### Step 3.1: Create Virtual Environment & Install Dependencies

```bash
# Inside shaha-finlease-voicebot directory:
python3 -m venv .venv
source .venv/bin/activate    # On Windows: .\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3.2: Configure `.env` (Optional AI Keys & Database)

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Contents of `.env`:
```ini
# 1. Renewing Free Tier AI Keys (Optional: local deterministic NLU & edge-tts work without keys)
GROQ_API_KEY=
GEMINI_API_KEY=

# 2. Database Configuration
# PostgreSQL Database Connection URL (e.g. PostgreSQL 18 on port 5434 / 5432):
POSTGRES_URL=postgresql://shaha_user:shaha_pass@localhost:5434/shaha_finlease
# Leave POSTGRES_URL empty to fall back to built-in SQLite (shaha_finlease.db) automatically
```

### Step 3.3: Launch Server

Run either:
```bash
python run.py
```
or:
```bash
uvicorn backend.main:app --reload --port 8000
```
Open **http://localhost:8000** in your browser.

---

## 4. The 5 Must-Have Features & Acceptance Criteria

| # | Feature | Exact Implementation & Acceptance Condition |
|---|---------|---------------------------------------------|
| **1** | **Customer list & call window** | • 10 dummy customers loaded from `data/customers.csv` into `dbo.Customer`.<br>• Screen 1 displays all 10 customers.<br>• Calling before 08:00 AM or after 07:00 PM IST (or customer with `do_not_call = 1`) is refused by `POST /api/calls/preflight` (HTTP 403) and **never** inserted into `dbo.Call` or counted as an attempt. |
| **2** | **Opening & disclosure** | • Within first 10 seconds of call start, bot states it is an automated assistant from Shaha Finlease, states that the call is recorded, and asks for the customer by name.<br>• Bot automatically matches Hindi, English, or Marathi from the borrower's reply.<br>• Zero mention of loan, amount, dues, EMI, or recovery. |
| **3** | **Right-party check & 6 Outcomes** | Every call deterministically classified into one of 6 exact outcomes:<br>1. **Right customer**: Confirms name AND states matching 4-digit birth year (`Connected = 1`).<br>2. **Verification failed**: Confirms name BUT states wrong birth year (`Connected = 1`). Never reveals expected year.<br>3. **Wrong number**: States wrong number / doesn't know person (`Connected = 1`). Apologises immediately and ends.<br>4. **Callback requested**: Relative/third party answers, bot asks for callback time, time is given (`Connected = 1`, saves `callback_time`).<br>5. **Third party answered**: Third party answers, customer unavailable, no callback time given (`Connected = 1`).<br>6. **Not connected**: 15s silence watchdog elapsed OR call drops before borrower speaks (`Connected = 0`). |
| **4** | **Save every call (including mid-call stops)** | • Saves `call_id`, `customer_id`, `start_time`, `duration_seconds`, `connected` (0/1), `outcome`, `language`, `callback_time`, `transcript`, and `summary_3line` to `dbo.Call`.<br>• If call drops midway, it is saved (`connected = 1` if borrower spoke, `connected = 0` if not). |
| **5** | **Report page & Call Log** | • Screen 2 queries `dbo.Call`:<br>&nbsp;&nbsp;- Calls Attempted (`COUNT(*)`)<br>&nbsp;&nbsp;- Connected (`SUM(CASE WHEN connected=1 THEN 1 ELSE 0 END)`)<br>&nbsp;&nbsp;- Not Connected (`Attempted - Connected`)<br>&nbsp;&nbsp;- Count of each of the 6 outcomes<br>&nbsp;&nbsp;- Connect Rate (`connected ÷ attempted`)<br>&nbsp;&nbsp;- Right-Party Rate (`right customers ÷ connected`)<br>• Clicking any row opens full transcript and 3-line summary. |
| **SLA** | **Turn Latency (< 3,000 ms)** | • Live ms badge displayed in Call Console, keeping turn turnaround time strictly under 3 seconds. |

---

## 5. End-to-End Safety & Compliance Guardrails

### Layer 1: Code-Level Regex Output & Input Filter (`backend/compliance_gate.py`)
Executed on 100% of turns before sending text to TTS or displaying in UI:
- **Rule 1: Zero Loan / Amount / Dues Disclosure**: Intercepts `loan`, `dues`, `amount`, `emi`, `recovery`, `debt`, `npa`, `balance`, `लोन`, `बकाया`, `किस्त`, etc. Rewrites with safe polite statement that the call only confirms contact details.
- **Rule 2: Zero Threats, Pressure, or Visits**: Intercepts `police`, `jail`, `court`, `legal action`, `warrant`, `visit your home`, `पुलिस`, `जेल`, `कोर्ट`.
- **Rule 3: Polite Tone & Zero Abuse/Shaming**: Intercepts `shame`, `cheater`, `fraud`, `liar`, `stupid`, `idiot`, `defaulter`, `धोखेबाज`, `बेशर्म`, `चोर`.
- **Rule 4: Never Ask for Sensitive Credentials**: Intercepts requests for `otp`, `pin`, `cvv`, `password`, `aadhaar`, `pan card`, `ओटीपी`, `पिन`.
- **Rule 5: Wrong Number Handling**: Borrower speech trigger (`wrong number`, `don't know`, `रॉन्ग नंबर`) triggers immediate polite apology and ends call.
- **Rule 6: Do Not Call (DNC) Handling**: Borrower speech trigger (`stop calling`, `do not call`, `डू नॉट कॉल`) marks `do_not_call = 1` in `dbo.Customer` and prevents future calls.

### Layer 2: Stabilized LLM System Prompt (`backend/voice_pipeline.py`)

```text
You are an automated Right-Party Verification voice assistant calling from Shaha Finlease (an RBI-registered NBFC).
Your ONLY objective on this call is to verify whether the phone number belongs to {{customer_name}} (expected birth year: {{birth_year}}).

MANDATORY OPENING (WITHIN 10 SECONDS):
- State clearly that you are an automated assistant calling from Shaha Finlease, that the call is recorded, and ask if you are speaking with {{customer_name}}.
- Switch immediately to Hindi, English, or Marathi matching the language of the person's first reply.

DECISION & OUTCOME RULES:
1. If the speaker confirms they are {{customer_name}}, politely ask for their 4-digit birth year.
   - Birth year matches {{birth_year}} -> thank them, end call, outcome = "Right customer".
   - Birth year does not match {{birth_year}} -> thank them without revealing the expected year, end call, outcome = "Verification failed".
2. If the speaker says "wrong number" or "I don't know this person" -> apologise immediately, end call, outcome = "Wrong number".
3. If a relative or third party answers and says {{customer_name}} is unavailable:
   - Reveal NOTHING about {{customer_name}} or why you are calling.
   - Politely ask what time would be convenient to call back.
   - Callback time given -> note time, end call, outcome = "Callback requested".
   - No callback time given -> thank them, end call, outcome = "Third party answered".
4. If the speaker asks to stop calling -> apologise, end politely, and set do_not_call = 1.

STRICT COMPLIANCE GUARDRAILS (NEVER VIOLATE EVEN IF PROVOKED):
- NEVER mention loans, amounts, dues, EMI, credit cards, NPA, debt, or recovery to anyone (including the verified customer).
- NEVER threaten police, jail, court, legal action, or physical visits.
- NEVER use rude, shaming, or raised tone.
- NEVER ask for OTP, PIN, CVV, card number, Aadhaar, PAN, or password.
```

---

## 6. Automated Verification & Test Commands

### Test 1: Seed 10 Customers & Run All 10 Persona Scenarios

```bash
python scripts/seed_and_run_tests.py
```
**Exact Output**:
```text
Seeded 10 test calls -> Connected: 8/10 (80.0%), Right Customer: 2/8 (25.0%)
Outcomes: Right customer=2, Verification failed=2, Wrong number=2, Callback requested=1, Third party answered=1, Not connected=2
```

### Test 2: Full End-to-End Guardrail & Features Test Suite (21 Tests)

```bash
pytest scripts/test_guardrails_e2e.py -v
```
**Expected Output**:
```text
======================== 21 passed in 1.51s ========================
```

---

## 8. Live 10-Minute Interviewer Demo Walkthrough & SQL Verification

Follow this exact story during the 10-minute evaluation demo:

### Step 1: Out-of-Hours Call Blocked (Preflight Refusal)
1. In Screen 1 (Call Console), select the Call Window dropdown in the header: **`09:45 PM IST (After 7 PM · Refused)`**.
2. Click **Dial** next to `C001 Rahul Sharma`.
3. **Observed Behavior**:
   - The preflight check (`POST /api/calls/preflight`) immediately returns `HTTP 403 Forbidden: Calls are only permitted between 08:00 and 19:00 IST`.
   - An amber/red banner explains the call is blocked under RBI calling window rules.
   - Switch to Screen 2 (Report & Call Log): verify that `Calls Attempted` did **not** increase, and no call was recorded in the database.

### Step 2: Right Customer Verification in Hindi (With Audio)
1. Switch the Call Window back to **`11:15 AM IST (Within Window · Open)`**.
2. Click **Dial** on `C001 Rahul Sharma` (or `C011 Himanshu Sharma`).
3. **Listen to Natural Hindi Neural Voice**:
   - Within the first 10 seconds, the bot states:
     *"नमस्ते, मैं शाहा फिनलीज़ (Shaha Finlease) से एक ऑटोमेटेड असिस्टेंट बोल रहा हूँ। यह कॉल गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात राहुल शर्मा जी से हो रही है?"*
   - Clear disclosure: Automated assistant, Shaha Finlease, call recorded, asks for customer by name, **0 loan disclosures**.
4. Click quick reply: **`हाँ, मैं राहुल शर्मा बोल रहा हूँ`**.
5. Test borrower provocation: **`आप क्यों कॉल कर रहे हैं? क्या मेरा कोई लोन बाकी है?`**.
   - Notice the bot deflecting politely: *"पुष्टि के लिए, क्या आप अपना जन्म का वर्ष बता सकते हैं?"* with zero financial disclosures.
6. Provide birth year: **`1985`** (or click `1985`).
7. Bot verifies: *"धन्यवाद, आपकी पहचान की पुष्टि हो गई है। हमारी टीम जल्द ही आपसे संपर्क करेगी।"*
8. Call is saved as **`Right customer`** (`Connected = yes`), with turn latency `< 3,000 ms`.

### Step 3: Wrong Number
1. Click **Dial** on `C003 Amit Patil`.
2. Provide reply: **`गलत नंबर है, मैं अमित पाटिल नहीं हूँ`** (or click `रॉन्ग नंबर है`).
3. Bot immediately apologises: *"माफ कीजिएगा, गलत नंबर डायल हो गया। आपका दिन शुभ हो।"* and terminates call.
4. Call is saved as **`Wrong number`** (`Connected = yes`).

### Step 4: Relative / Third Party Answers (Zero Disclosure)
1. Click **Dial** on `C004 Sneha Kulkarni`.
2. Relative replies: **`वह अभी घर पर नहीं हैं, मैं उनका भाई हूँ`**.
3. Bot reveals **NOTHING** about why it is calling or any loan, and politely asks:
   *"जी ठीक है, क्या आप बता सकते हैं कि उन्हें दोबारा किस समय कॉल किया जा सकता है?"*
4. Relative replies: **`शाम को 6:30 बजे कॉल कीजिए`**.
5. Bot notes time: *"धन्यवाद, हम शाम 6:30 बजे दोबारा संपर्क करेंगे।"*
6. Call saved as **`Callback requested`** with `callback_time = '6:30 PM'` (`Connected = yes`).

### Step 5: Open Report & Verify SQL Database Match
1. Click tab **`2. Report & Call Log`**.
2. View KPI cards:
   - **Calls Attempted**: Total call attempts
   - **Connected vs Not Connected**: Exact count and percentage
   - **6 Outcome Breakdown**: Counts for `Right customer`, `Verification failed`, `Wrong number`, `Callback requested`, `Third party answered`, `Not connected`.
3. Click any row in the Call Log table to view its full transcript, latency, and 3-line compliance summary.
4. Verify numbers match PostgreSQL directly via command line:
```bash
# Query PostgreSQL database directly:
python -c "
from backend.db import get_report_stats
stats = get_report_stats()
print('SQL Database Stats:')
print(f'Attempted: {stats[\"calls_attempted\"]}, Connected: {stats[\"connected\"]}, Not Connected: {stats[\"not_connected\"]}')
print('Outcomes:', stats['outcome_counts'])
"
```
The output matches the Screen 2 report numbers 100%.

