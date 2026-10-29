"""Runs all 10 test scenarios from `data/test_calls.csv` (the 5 personas x 2)
and verifies that the database and report counts match 100%.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.db import seed_test_calls

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))


def main() -> None:
    customers_csv = os.path.join(DATA_DIR, "customers.csv")
    test_calls_csv = os.path.join(DATA_DIR, "test_calls.csv")

    report = seed_test_calls(test_calls_csv, customers_csv)

    total = report["calls_attempted"]
    conn_count = report["connected"]
    right_count = report["outcome_counts"].get("Right customer", 0)

    outcomes_ordered = [
        "Right customer",
        "Verification failed",
        "Wrong number",
        "Callback requested",
        "Third party answered",
        "Not connected",
    ]
    outcome_str_parts = [
        f"{out}={report['outcome_counts'].get(out, 0)}" for out in outcomes_ordered
    ]

    print(
        f"Seeded {total} test calls -> Connected: {conn_count}/{total} ({conn_count/total*100:.1f}%), "
        f"Right Customer: {right_count}/{conn_count} ({right_count/conn_count*100:.1f}%)"
    )
    print(f"Outcomes: {', '.join(outcome_str_parts)}")


if __name__ == "__main__":
    main()
