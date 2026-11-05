"""
identity_verifier.py - Deterministic Identity & Surname Collision Verification for Shaha Finlease Voicebot.

Implements RBI Fair Practices Code compliant entity verification:
Prevents LLM token-hallucination false positives (e.g. Rahul Kumar confirming for Rahul Sharma).
"""

import re
from typing import Tuple, Optional, Set

# Standardized canonical transliteration map covering English and Devanagari
SURNAME_CANONICAL_MAP = {
    "sharma": "sharma", "शर्मा": "sharma",
    "kumar": "kumar",   "कुमार": "kumar",
    "verma": "verma",   "वर्मा": "verma",
    "singh": "singh",   "सिंह": "singh",
    "patil": "patil",   "पाटिल": "patil",
    "kulkarni": "kulkarni", "कुलकर्णी": "kulkarni",
    "deshmukh": "deshmukh", "देशमुख": "deshmukh",
    "mehta": "mehta",   "मेहता": "mehta",
    "gupta": "gupta",   "गुप्ता": "gupta",
    "yadav": "yadav",   "यादव": "yadav",
    "joshi": "joshi",   "जोशी": "joshi",
    "nair": "nair",     "नायर": "nair",
    "menon": "menon",   "मेनन": "menon",
    "pillai": "pillai", "पिल्लई": "pillai",
    "iyer": "iyer",     "अय्यर": "iyer",
    "iyengar": "iyengar", "अय्यंगार": "iyengar",
    "reddy": "reddy",   "रेड्डी": "reddy",
    "rao": "rao",       "राव": "rao",
    "mishra": "mishra", "मिश्रा": "mishra",
    "pandey": "pandey", "पांडेय": "pandey",
    "tiwari": "tiwari", "तिवारी": "tiwari",
    "choudhary": "choudhary", "चौधरी": "choudhary",
    "chauhan": "chauhan", "चौहान": "chauhan",
    "patel": "patel",   "पटेल": "patel",
    "shah": "shah",     "शाह": "shah",
    "bose": "bose",     "बोस": "bose",
    "banerjee": "banerjee", "बनर्जी": "banerjee",
    "chatterjee": "chatterjee", "चटर्जी": "chatterjee",
    "mukherjee": "mukherjee", "मुखर्जी": "mukherjee",
    "das": "das",       "दास": "das",
    "dutta": "dutta",   "दत्ता": "dutta",
    "roy": "roy",       "रॉय": "roy",
    "sen": "sen",       "सेन": "sen",
    "ghosh": "ghosh",   "घोष": "ghosh",
    "saxena": "saxena", "सक्सेना": "saxena",
    "bhatia": "bhatia", "भाटिया": "bhatia",
    "kapoor": "kapoor", "कपूर": "kapoor",
    "khanna": "khanna", "खन्ना": "khanna",
    "malhotra": "malhotra", "मलहोत्रा": "malhotra",
    "agarwal": "agarwal", "aggarwal": "agarwal", "अग्रवाल": "agarwal",
    "jain": "jain",     "जैन": "jain",
    "chopra": "chopra", "चोपड़ा": "chopra",
    "shetty": "shetty", "शेट्टी": "shetty",
    "hegde": "hegde",   "हेगड़े": "hegde",
    "gowda": "gowda",   "गौड़ा": "gowda",
    "bhat": "bhat",     "भट": "bhat",
    "kaur": "kaur",     "कौर": "kaur",
    "gill": "gill",     "गिल": "gill",
}

FIRST_NAME_CANONICAL_MAP = {
    "rahul": "rahul", "राहुल": "rahul",
    "priya": "priya", "प्रिया": "priya",
    "amit": "amit", "अमित": "amit",
    "sneha": "sneha", "स्नेहा": "sneha",
    "vikram": "vikram", "विक्रम": "vikram",
    "anjali": "anjali", "अंजलि": "anjali", "अंजली": "anjali",
    "rohan": "rohan", "रोहन": "rohan",
    "kavita": "kavita", "कविता": "kavita",
    "suresh": "suresh", "सुरेश": "suresh",
    "neha": "neha", "नेहा": "neha",
    "himanshu": "himanshu", "हिमांशु": "himanshu",
    "sunil": "sunil", "सुनील": "sunil",
    "rohit": "rohit", "रोहित": "rohit",
    "aarav": "aarav", "आरव": "aarav",
    "pooja": "pooja", "पूजा": "pooja",
}

COMMON_HONORIFICS: Set[str] = {
    "ji", "जी", "bhai", "भाई", "sahab", "साहब", "saheb", "sir", "सर", "madam", "मैडम",
    "tai", "ताई", "dada", "दादा", "shri", "श्री", "smt", "श्रीमती"
}

STOP_WORDS: Set[str] = {
    "main", "mein", "mai", "me", "hum", "speaking", "here", "shaha", "finlease",
    "मैं", "हम", "ही", "तो", "भी", "है", "हूँ", "hoon", "hu", "hai", "bol", "raha",
    "rahi", "बोल", "रहा", "रही", "आहे", "होय", "हो", "haan", "yes", "ji", "जी"
}

DENIAL_PATTERNS = re.compile(
    r"\b(wrong number|galat number|गलत नंबर|नहीं जानता|nahi janta|not rahul|not me|main nahi|mein nahi hu|mai nahi hu|चुकीचा नंबर|नाहीयेत|wrong person|galat call|who are you calling)\b",
    re.IGNORECASE
)

RELATIVE_PATTERNS = re.compile(
    r"\b(brother|sister|wife|husband|father|mother|relative|friend|not at home|out|busy|call back|later|"
    r"भाई|बहन|पत्नी|पति|पिता|माता|बेटा|बेटी|घर पर नहीं|बाहर गए|बाहर गई|नाहीयेत|उद्या कॉल करा|बाद में कॉल|उनका भाई|उनकी पत्नी|उनके पिता|उनका|उनकी|उनके)\b",
    re.IGNORECASE
)

AFFIRMATIVE_PATTERNS = re.compile(
    r"\b(haan|yes|speaking|bol raha hoon|bol raha hu|बोल रहा हूँ|बोल रही हूँ|बोल रहा हु|बोलतोय|बोलतेय|yes speaking|this is he|this is she|i am|sahi number hai|correct|yep|yeah|ji haan|जी हाँ|होय|हो)\b",
    re.IGNORECASE
)

STATED_NAME_PATTERNS = [
    re.compile(r"(?:mera naam|my name is|this is|i am|naam hai)\s+([a-zA-Z\u0900-\u097F]+(?:\s+[a-zA-Z\u0900-\u097F]+)?)", re.IGNORECASE),
    re.compile(r"(?:main|mein|mai|मैं)\s+([a-zA-Z\u0900-\u097F]+(?:\s+[a-zA-Z\u0900-\u097F]+)?)\s+(?:bol raha hoon|bol raha hu|bol rahi hu|बोल रहा हूँ|बोल रही हूँ|बोलतोय)", re.IGNORECASE),
    re.compile(r"^([a-zA-Z\u0900-\u097F]+(?:\s+[a-zA-Z\u0900-\u097F]+)?)\s+(?:bol raha hoon|bol raha hu|bol rahi hu|बोल रहा हूँ|बोल रही हूँ|बोलतोय|here)$", re.IGNORECASE),
]


def verify_target_identity(
    expected_full_name: str,
    user_spoken_text: str
) -> Tuple[str, Optional[str]]:
    """
    Deterministic identity and surname collision verification.

    Returns:
        ("CONFIRMED", reason)  -> Customer verified
        ("DISCREPANCY", reason)-> Surname/Identity explicitly conflicts
        ("AMBIGUOUS", reason)  -> Partial match only (e.g., said "haan main rahul bol raha hu")
        ("DENIAL", reason)     -> "Galat number hai" / "Main nahi hu"
    """
    text_clean = user_spoken_text.lower().strip()

    # 1. Check for explicit negative indicators
    if DENIAL_PATTERNS.search(text_clean):
        return "DENIAL", "User explicitly denied identity / marked wrong number"

    # 1b. Check for relative / third-party indicators
    if RELATIVE_PATTERNS.search(text_clean):
        return "AMBIGUOUS", "Relative or third-party answered; requires callback handling"

    # Tokenize expected target name
    expected_tokens = [w.lower() for w in re.findall(r"[a-zA-Z\u0900-\u097F]+", expected_full_name)]
    expected_first = expected_tokens[0] if expected_tokens else ""
    expected_last = expected_tokens[-1] if len(expected_tokens) > 1 else ""

    expected_first_canon = FIRST_NAME_CANONICAL_MAP.get(expected_first, expected_first)
    expected_last_canon = SURNAME_CANONICAL_MAP.get(expected_last, expected_last)
    expected_canonical_tokens = [
        FIRST_NAME_CANONICAL_MAP.get(t, SURNAME_CANONICAL_MAP.get(t, t))
        for t in expected_tokens
    ]

    # Tokenize input words
    spoken_tokens = [w.lower() for w in re.findall(r"[a-zA-Z\u0900-\u097F]+", text_clean)]
    spoken_canonical = [
        FIRST_NAME_CANONICAL_MAP.get(w, SURNAME_CANONICAL_MAP.get(w, w))
        for w in spoken_tokens
    ]

    # 2. Check for Conflicting Surnames (e.g. Rahul Kumar != Rahul Sharma)
    if expected_last:
        for word in spoken_tokens:
            if word in SURNAME_CANONICAL_MAP:
                canonical_spoken = SURNAME_CANONICAL_MAP[word]
                # If caller uttered a recognized surname that does NOT match expected surname
                if canonical_spoken != expected_last_canon:
                    # Allow only if it was already in the expected name tokens
                    if canonical_spoken not in expected_canonical_tokens:
                        return "DISCREPANCY", f"Surname mismatch: Expected '{expected_last}', caller said '{word}'"

    # 3. Check for explicitly stated different full/first names
    for pat in STATED_NAME_PATTERNS:
        match = pat.search(text_clean)
        if match:
            raw_stated = match.group(1).lower().strip()
            stated_tokens = [
                w for w in re.findall(r"[a-zA-Z\u0900-\u097F]+", raw_stated)
                if w not in COMMON_HONORIFICS and w not in STOP_WORDS
            ]
            if stated_tokens:
                # Check if any stated token is a recognized surname mismatch
                for st in stated_tokens:
                    if st in SURNAME_CANONICAL_MAP:
                        st_canon = SURNAME_CANONICAL_MAP[st]
                        if st_canon != expected_last_canon and st_canon not in expected_canonical_tokens:
                            return "DISCREPANCY", f"Stated surname '{st}' does not match expected '{expected_last}'"

                # Check if multi-word stated name conflicts with expected last name
                if len(stated_tokens) >= 2 and expected_last:
                    last_st_canon = SURNAME_CANONICAL_MAP.get(stated_tokens[-1], stated_tokens[-1])
                    if last_st_canon != expected_last_canon and last_st_canon not in expected_canonical_tokens:
                        return "DISCREPANCY", f"Stated full name '{' '.join(stated_tokens)}' does not match expected '{expected_full_name}'"

                # Check stated first name
                st_first_canon = FIRST_NAME_CANONICAL_MAP.get(stated_tokens[0], stated_tokens[0])
                if st_first_canon != expected_first_canon and stated_tokens[0] not in ("haan", "yes", "ji", "जी"):
                    # If it's a recognized first name and doesn't match expected
                    if stated_tokens[0] in FIRST_NAME_CANONICAL_MAP or stated_tokens[0] not in expected_tokens:
                        return "DISCREPANCY", f"Stated name '{stated_tokens[0]}' does not match expected '{expected_first}'"

    # 4. Check for Full Name Match vs First Name Only
    first_name_matched = (expected_first in spoken_tokens) or (expected_first_canon in spoken_canonical)
    last_name_matched = bool(expected_last) and (
        (expected_last in spoken_tokens) or (expected_last_canon in spoken_canonical)
    )

    if first_name_matched and last_name_matched:
        return "CONFIRMED", "Full name matched"

    if first_name_matched and not expected_last:
        return "CONFIRMED", "First name matched (no surname in record)"

    if first_name_matched and not last_name_matched:
        # Caller only said "Haan main Rahul bol raha hu" without mentioning Sharma
        # Under RBI rules, you MUST trigger 2-Factor secondary verification (Birth Year)
        return "AMBIGUOUS", "First name matched only; requires secondary verification factor"

    # 5. Check for direct confirmation words ("Haan, bol raha hu" / "Yes, speaking")
    if AFFIRMATIVE_PATTERNS.search(text_clean):
        return "AMBIGUOUS", "Affirmative phrase without full name; requires secondary factor"

    return "AMBIGUOUS", "Identity unverified"
