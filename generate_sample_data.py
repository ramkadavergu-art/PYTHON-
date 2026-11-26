"""Generate the sample datasets in sample_data/ (deterministic).

    python scripts/generate_sample_data.py

Creates:
  sample_data/sample_data.csv / sample_data.json  - ~40 realistic rows (normal, boundary, edge cases)
  sample_data/legacy_import_with_errors.csv       - messy legacy file to demo bulk import + error log
"""
from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CATEGORIES, SAMPLE_DIR, load_rules  # noqa: E402
from app.ledger import Ledger  # noqa: E402
from app.storage import CSV_FIELDS  # noqa: E402

ANCHOR = date(2026, 11, 20)
CUSTOMERS = [("C001", "Aarav Sharma"), ("C002", "Priya Reddy"), ("C003", "Kiran Kumar"),
             ("C004", "Sneha Iyer"), ("C005", "Rohit Verma"), ("C006", "Meera Nair"),
             ("C007", "Arjun Singh"), ("C008", "Divya Menon")]
NOTES = ["", "", "", "Festival offer", "Weekend shopping", "Home delivery", "Walk-in", "Online order"]


def day(offset: int) -> str:
    return (ANCHOR - timedelta(days=offset)).isoformat()


def build_events(rng: random.Random):
    names = dict(CUSTOMERS)
    events = [  # (date, cid, category, amount, note) - deliberate boundary / edge cases
        (day(150), "C001", "Grocery", 0.01, "Boundary: minimum amount (0 points)"),
        (day(140), "C002", "Grocery", 9.99, "Boundary: just under one point"),
        (day(139), "C002", "Grocery", 10.00, "Boundary: exactly one point"),
        (day(120), "C003", "Electronics", 50000.00, "Boundary: maximum purchase limit"),
        (day(100), "C004", "Dining", 25000.00, "Edge: large purchase (warning threshold)"),
        (day(130), "C008", "Beauty", 499.99, "Edge: fractional points rounding"),
    ]
    for off in (145, 128, 112):                       # C007 goes quiet -> Inactive customer
        events.append((day(off), "C007", rng.choice(CATEGORIES), round(rng.triangular(300, 2500, 700), 2), "Early visit"))
    for cid, _ in CUSTOMERS:
        if cid == "C007":
            continue
        for _ in range(rng.randint(3, 5)):
            events.append((day(rng.randint(1, 110)), cid, rng.choice(CATEGORIES),
                           round(rng.triangular(80, 6500, 1200), 2), rng.choice(NOTES)))
    return sorted(events), names


def main() -> None:
    rng = random.Random(42)
    rules = load_rules()
    ledger = Ledger([], rules, path=SAMPLE_DIR / "_unused.json")
    events, names = build_events(rng)
    for d, cid, cat, amount, note in events:
        ledger.add_purchase(cid, names[cid], d, cat, amount, note, persist=False)
        bal = ledger.balance(cid, as_of=d)
        if bal >= 300 and rng.random() < 0.3:          # sometimes redeem on the same visit
            pts = min(bal // 100 * 100, 100 * rng.randint(1, 5))
            bill = max(round(rng.uniform(400, 3500), 2), pts * 0.1 * 2 + 1)
            ledger.add_redemption(cid, d, pts, round(bill, 2), cat, "Points redeemed at checkout", persist=False)
    txns = sorted(ledger.transactions, key=lambda t: (t.date, t.txn_id))
    assert 30 <= len(txns) <= 50, f"expected 30-50 rows, got {len(txns)}"

    SAMPLE_DIR.mkdir(exist_ok=True)
    with open(SAMPLE_DIR / "sample_data.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(t.to_dict() for t in txns)
    (SAMPLE_DIR / "sample_data.json").write_text(
        json.dumps({"schema_version": 1, "transactions": [t.to_dict() for t in txns]}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    legacy = [  # row 2.. : deliberately messy legacy export
        "customer_id,customer_name,date,category,type,amount,points,notes",
        "C101,Ishaan Gupta,2026-09-02,Grocery,earn,1200.50,,Good row",
        "C101,Ishaan Gupta,2026-09-20,Electronics,earn,18000,,Good row",
        "C101,Ishaan Gupta,2026-10-05,Dining,redeem,900,100,Good redemption",
        "X12,Bad Id Person,2026-09-03,Grocery,earn,500,,Bad customer id",
        "C102,Nisha Rao,2026-09-04,Grocery,earn,-250,,Negative amount",
        "C102,Nisha Rao,2026-09-05,Grocery,earn,abc,,Non-numeric amount",
        "C102,Nisha Rao,2026-02-31,Apparel,earn,900,,Impossible date",
        "C102,Nisha Rao,2027-12-01,Apparel,earn,900,,Future date",
        "C102,Nisha Rao,2026-09-10,Toys,earn,700,,Unknown category",
        "C103,,2026-09-11,Beauty,earn,640,,Blank name",
        "C103,Varun Das,2026-09-12,Beauty,swap,640,,Unknown type",
        "C104,Tara Joshi,2026-09-14,Apparel,earn,2499,,Good row",
        "C104,Tara Joshi,2026-09-14,Apparel,earn,2499,,Duplicate of previous row",
        "C104,Tara Joshi,2026-09-30,Grocery,redeem,5000,5000,Redeems more than balance",
        "C105,Farhan Ali,2026-10-01,Home & Kitchen,earn,50000,,Boundary: max purchase",
        "C105,Farhan Ali,2026-10-02,Home & Kitchen,earn,50000.01,,Just over the limit",
        "C106,Lakshmi Pillai,2026-10-10,Grocery,earn,Rs. 1,850.00,,Currency text in amount (too many columns)",
        "C106,Lakshmi Pillai,2026-10-12,Dining,earn,\"Rs. 1,850.00\",,Currency text parsed OK",
    ]
    (SAMPLE_DIR / "legacy_import_with_errors.csv").write_text("\n".join(legacy) + "\n", encoding="utf-8")
    print(f"Wrote {len(txns)} sample rows and the legacy import file to {SAMPLE_DIR}")


if __name__ == "__main__":
    main()
