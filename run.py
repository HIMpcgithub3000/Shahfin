"""One-Command Local Launcher for Shaha Finlease Voicebot.

Initializes SQL database (dbo.Customer & dbo.Call), pre-seeds 10 dummy customers,
and starts Uvicorn on http://localhost:8000.
"""
import os
import sys
import uvicorn
from dotenv import load_dotenv

# Detect directory context (supports running from shaha-finlease-voicebot or workspace root)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(CURRENT_DIR, "backend")):
    PROJECT_DIR = CURRENT_DIR
elif os.path.isdir(os.path.join(CURRENT_DIR, "shaha-finlease-voicebot", "backend")):
    PROJECT_DIR = os.path.join(CURRENT_DIR, "shaha-finlease-voicebot")
else:
    PROJECT_DIR = CURRENT_DIR

sys.path.insert(0, PROJECT_DIR)
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

from backend.db import init_db_and_seed_customers

def main():
    print("=" * 76)
    print("   SHAHA FINLEASE — RIGHT-PARTY VERIFICATION VOICEBOT")
    print("   Automated Pre-Calling Contact Confirmation System (RBI-Registered NBFC)")
    print("=" * 76)

    csv_path = os.path.join(PROJECT_DIR, "data", "customers.csv")
    print(f"\n[*] Initializing Database & Pre-seeding 10 Customers from:")
    print(f"    {csv_path}")
    init_db_and_seed_customers(csv_path)
    print("    [✓] Tables ready: dbo.Customer & dbo.Call (PostgreSQL)")

    print("\n[*] Starting Local Server:")
    print("    -> Console & Report UI: http://localhost:8000")
    print("    -> API Documentation:   http://localhost:8000/docs")
    print("\n[*] Included Endpoints:")
    print("    - Screen 1: Call Console (Voice loop, Persona Auto-Play, 15s Watchdog)")
    print("    - Screen 2: Report & Call Log (Real-time SQL Analytics & 3-line summaries)")
    print("    - Preflight Gatekeeper: 8 AM - 7 PM IST calling window & DNC enforcement")
    print("    - Edge TTS Audio:       Streaming natural Indian neural voices (hi/en/mr)")
    print("\n[*] Press CTRL+C to stop the server.\n" + "=" * 76 + "\n")

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()

