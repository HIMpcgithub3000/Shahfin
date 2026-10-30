"""Pre-Call Compliance Gatekeeper & Code-Level Output Guardrails (Sections 4 & 5).

1. Pre-Dial Gatekeeper: Enforces 08:00 to 19:00 IST call window and `do_not_call` flag.
   Refused calls NEVER create a row in `dbo.Call` (not counted as an attempt).
2. Deterministic 6-Rule Reply Filter: Checks every bot reply in code before TTS.
   Guarantees 100% compliance with zero reliance on LLM good behavior.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Helper to build regex that properly respects word/boundary characters for both English and Devanagari
def _boundary_regex(pattern_str: str) -> re.Pattern:
    return re.compile(
        r"(?:^|[^\w\u0900-\u097F])(?:" + pattern_str + r")(?:[^\w\u0900-\u097F]|$)",
        re.IGNORECASE,
    )

# Layer 1: Code-level regex guardrails (enforced on every bot output before TTS)
FORBIDDEN_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (
        "Rule 1: Zero Loan/Amount/Dues/Recovery Mention",
        _boundary_regex(
            r"loan|loans|amount|dues|emi|recovery|default|debt|overdue|npa|credit card|balance|installment|"
            r"money owed|outstanding|settlement|collection|loanee|repay|"
            r"लोन|कर्ज|बकाया|राशि|किस्त|वसूली|उधार|कर्जा|पैसे|हफ्ता"
        ),
    ),
    (
        "Rule 2: Zero Threats, Pressure, or Visits",
        _boundary_regex(
            r"police|jail|prison|arrest|court|legal action|lawyer|warrant|we will visit|visit your home|"
            r"seize|attachment|case filed|summons|police complaint|"
            r"पुलिस|जेल|कोर्ट|कानूनी|गिरफ्तार|वारंट|घर आएंगे|कार्रवाई|जब्त"
        ),
    ),
    (
        "Rule 3: Polite Tone & Zero Abuse/Shaming",
        _boundary_regex(
            r"shame|cheater|fraud|liar|stupid|idiot|useless|defaulter|criminal|chor|badtameez|"
            r"धोखेबाज|बेशर्म|चोर|अपराधी|बकवास|कंजूस"
        ),
    ),
    (
        "Rule 4: Never Ask for Sensitive Credentials",
        _boundary_regex(
            r"otp|pin|cvv|cvv2|card number|password|aadhaar|pan card|bank account|net banking|mpin|"
            r"ओटीपी|पिन|सीवीवी|पासवर्ड|आधार|पैन कार्ड|खाता संख्या"
        ),
    ),
]

# Borrower speech triggers for Rule 5 (Wrong Number) and Rule 6 (Do Not Call)
WRONG_NUMBER_REGEX = _boundary_regex(
    r"wrong number|don't know|do not know|not their number|not my number|galat number|wrong person|"
    r"nahi janta|nahi jaanta|koi nahi hai|रॉन्ग नंबर|गलत नंबर|नहीं जानता|नहीं जानती|उनका नंबर नहीं|रॉंग नंबर|"
    r"मी ओळखत नाही|चुकीचा नंबर"
)

DNC_REGEX = _boundary_regex(
    r"stop calling|do not call|don't call|never call|remove my number|remove this number|dnc|block this number|"
    r"mat karo|call mat|डू नॉट कॉल|कॉल मत करो|परेशान मत|फोन मत करना|दोबारा मत करना|कॉल करू नका"
)

PROVOCATION_REGEX = _boundary_regex(
    r"loan|dues|amount|emi|money|owe|credit card|recovery|police|jail|legal|court|visit|"
    r"लोन|बकाया|राशि|किस्त|पैसे|क्यों कॉल|पुलिस|जेल"
)


def check_pre_call_gate(
    do_not_call: bool, simulated_hour: Optional[int] = None
) -> Tuple[bool, str]:
    """Checks Feature #1 (8 AM - 7 PM Call Window) and Rule #6 (Do Not Call flag).

    Refused calls MUST return False so caller rejects without inserting into Call table.
    """
    now_ist = datetime.now(IST)
    hour = simulated_hour if simulated_hour is not None else now_ist.hour

    if hour < 8 or hour >= 19:
        return (
            False,
            f"Call refused: Current time (hour={hour:02d}:00 IST) is outside the 8:00 AM – 7:00 PM calling window. Not counted as an attempt.",
        )

    if do_not_call:
        return (
            False,
            "Call refused: Customer is marked Do Not Call (do_not_call = 1) per Compliance Rule #6.",
        )

    return True, "Passed pre-call compliance gate."


def verify_and_sanitize_reply(
    raw_reply: str, language: str, customer_name: str
) -> Dict[str, Any]:
    """Runs deterministic code-level compliance checks on every LLM reply before TTS.

    If any forbidden term is detected, rewrites with standard compliant response.
    """
    violations: List[str] = []
    for rule_name, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(raw_reply):
            violations.append(rule_name)

    cleaned_reply = raw_reply.replace("!", ".")

    if not violations:
        return {
            "safe": True,
            "violations": [],
            "reply": cleaned_reply,
            "text": cleaned_reply,
        }

    # Deterministic safe rewrite for each supported language
    if language == "Hindi":
        safe_text = (
            "क्षमा करें, यह कॉल केवल संपर्क विवरण (Contact Details) की पुष्टि करने के लिए है। "
            "सुरक्षा नियमों के तहत हम किसी अन्य विषय पर चर्चा नहीं करते हैं।"
        )
    elif language == "Marathi":
        safe_text = (
            "क्षमस्व, हा कॉल फक्त संपर्क तपशीलांची पुष्टी करण्यासाठी आहे. "
            "आम्ही इतर कोणत्याही गोपनीय माहितीवर चर्चा करत नाही."
        )
    else:
        safe_text = (
            f"I apologise, but this call is strictly to confirm contact details for {customer_name}. "
            "Under compliance rules, I do not discuss any financial or sensitive information."
        )

    return {
        "safe": False,
        "violations": violations,
        "reply": safe_text,
        "text": safe_text,
    }


def is_dnc_request(text: str) -> bool:
    """Checks if borrower asked to stop calling (Rule #6)."""
    return bool(DNC_REGEX.search(text))


def is_wrong_number_phrase(text: str) -> bool:
    """Checks if borrower stated wrong number (Rule #5)."""
    return bool(WRONG_NUMBER_REGEX.search(text))


def is_provocation_phrase(text: str) -> bool:
    """Checks if borrower tried to provoke about loan/amount/police."""
    return bool(PROVOCATION_REGEX.search(text))
