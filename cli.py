#!/usr/bin/env python3
"""Loyalty Points Calculator - menu-driven terminal application.

Run:  python cli.py
At any prompt type 'q' to cancel and return to the menu.
"""
from __future__ import annotations

import sys
from datetime import date

from app import ui
from app.alerts import generate_alerts
from app.analytics import customer_table, summary
from app.config import APP_NAME, CATEGORIES, EXPORT_DIR, SAMPLE_DIR
from app.exporter import export_all
from app.importer import import_csv
from app.ledger import Ledger
from app.rules import earn_points, get_tier, next_tier, points_value, sorted_tiers
from app.search import search
from app.validation import (InputCancelled, ValidationError, ask, clean_amount, clean_category, clean_customer_id,
                            clean_date, clean_name, clean_notes, clean_points, clean_type, optional)

SAMPLE_CSV = SAMPLE_DIR / "sample_data.csv"
LEGACY_CSV = SAMPLE_DIR / "legacy_import_with_errors.csv"
TXN_HEADERS = ["ID", "Date", "Cust", "Name", "Category", "Type", "Amount", "Points", "Tier"]
TXN_ALIGN = ["l", "l", "l", "l", "l", "l", "r", "r", "l"]


def txn_rows(txns):
    return [[t.txn_id, t.date, t.customer_id, t.customer_name, t.category, t.type,
             f"{t.amount:,.2f}", f"{t.points:+d}", t.tier] for t in txns]


def category_cleaner(rules):
    def clean(v):
        v = str(v).strip()
        if v.isdigit() and 1 <= int(v) <= len(CATEGORIES):
            v = CATEGORIES[int(v) - 1]
        return clean_category(v, rules)
    return clean


def show_categories():
    print("  Categories: " + "  ".join(f"{i}) {c}" for i, c in enumerate(CATEGORIES, 1)))


# ---------------------------------------------------------------- actions
def record_purchase(ledger: Ledger):
    rules = ledger.rules
    print(ui.heading("Record a purchase (earn points)"))
    cid = ask("Customer ID (e.g. C001): ", clean_customer_id)
    existing = ledger.name_of(cid)
    if existing:
        print(f"  Existing customer: {existing} - {ledger.tier(cid)['name']} tier, {ledger.balance(cid):,} points")
        name = existing
    else:
        name = ask("Customer name: ", clean_name)
    d = ask("Date [YYYY-MM-DD, Enter = today]: ", clean_date, default=date.today().isoformat())
    show_categories()
    cat = ask("Category (number or name): ", category_cleaner(rules))
    amount = ask(f"Purchase amount ({rules['currency']}): ", lambda v: clean_amount(v, rules))
    notes = ask("Notes (optional): ", clean_notes, default="")
    txn = ledger.add_purchase(cid, name, d, cat, amount, notes)
    print(ui.green(f"\n  Saved {txn.txn_id}: {txn.points} points earned "
                   f"({txn.tier} tier). New balance: {ledger.balance(cid):,} points."))


def redeem_points(ledger: Ledger):
    rules = ledger.rules
    print(ui.heading("Redeem points"))
    cid = ask("Customer ID: ", clean_customer_id)
    name = ledger.name_of(cid)
    if not name:
        print(ui.red(f"  ! Unknown customer {cid}. Record a purchase first."))
        return
    bal = ledger.balance(cid)
    print(f"  {name}: {bal:,} points available (worth {ui.money(points_value(bal, rules), rules['currency'])}).")
    d = ask("Date [YYYY-MM-DD, Enter = today]: ", clean_date, default=date.today().isoformat())
    show_categories()
    cat = ask("Bill category (number or name): ", category_cleaner(rules))
    bill = ask(f"Bill amount ({rules['currency']}): ", lambda v: clean_amount(v, rules))
    while True:  # retry the points entry until the redemption rules accept it
        pts = ask(f"Points to redeem (multiples of {rules['redeem_step']}): ", clean_points)
        try:
            txn = ledger.add_redemption(cid, d, pts, bill, cat, "")
            break
        except ValidationError as exc:
            print(ui.red(f"  ! {exc}"))
    print(ui.green(f"\n  Saved {txn.txn_id}: {-txn.points} points redeemed = "
                   f"{ui.money(txn.discount, rules['currency'])} off. Balance: {ledger.balance(cid):,} points."))


def view_transactions(ledger: Ledger):
    print(ui.heading("All transactions (newest first)"))
    ui.paginate(TXN_HEADERS, txn_rows(search(ledger.transactions)), ledger.rules["page_size"], TXN_ALIGN, "Transactions")


def search_records(ledger: Ledger):
    rules = ledger.rules
    print(ui.heading("Search records") + "\n  Press Enter to skip any filter.")
    kw = ask("Keyword (name / id / notes): ", str, default="")
    show_categories()
    cat = ask("Category: ", optional(category_cleaner(rules)), default="")
    typ = ask("Type (earn/redeem): ", optional(clean_type), default="")
    dfrom = ask("From date (YYYY-MM-DD): ", optional(clean_date), default="")
    dto = ask("To date (YYYY-MM-DD): ", optional(clean_date), default="")
    minamt = ask("Minimum amount: ", optional(lambda v: clean_amount(v, rules)), default="")
    results = search(ledger.transactions, kw, cat, typ, "", "", dfrom, dto, minamt if minamt != "" else None)
    print(f"\n  {len(results)} match(es).")
    ui.paginate(TXN_HEADERS, txn_rows(results), rules["page_size"], TXN_ALIGN, "Results")


def customer_lookup(ledger: Ledger):
    rules, sym = ledger.rules, ledger.rules["currency"]
    print(ui.heading("Customer lookup"))
    cid = ask("Customer ID: ", clean_customer_id)
    name = ledger.name_of(cid)
    if not name:
        print(ui.red(f"  ! No records for {cid}."))
        return
    row = customer_table(ledger.transactions, rules).set_index("customer_id").loc[cid]
    life = ledger.lifetime(cid)
    nxt = next_tier(life, rules)
    print(f"  {ui.bold(name)} ({cid})  |  tier {row['tier']}  |  status {row['status']}")
    print(f"  Balance {ledger.balance(cid):,} pts (worth {ui.money(points_value(ledger.balance(cid), rules), sym)})"
          f"  |  lifetime {life:,}  |  spend {ui.money(row['total_spend'], sym)}")
    print(f"  Next tier: {nxt[0]['name']} in {nxt[1]:,} more lifetime points" if nxt else "  Top tier reached.")
    ui.paginate(TXN_HEADERS, txn_rows(ledger.history(cid)[::-1]), rules["page_size"], TXN_ALIGN, "History")


def show_summary(ledger: Ledger):
    rules, sym = ledger.rules, ledger.rules["currency"]
    s = summary(ledger.transactions, rules)
    print(ui.heading("Executive summary"))
    if not s["transactions"]:
        print("  No data yet. Record purchases or load the sample data from the menu.")
        return
    print(ui.table(["Metric", "Value"], [
        ["Data range", f"{s['first_date']} to {s['last_date']}"],
        ["Customers", s["customers"]], ["Transactions", f"{s['transactions']} ({s['purchases']} earn / {s['redemptions']} redeem)"],
        ["Total sales", ui.money(s["total_sales"], sym)], ["Average purchase", ui.money(s["avg_purchase"], sym)],
        ["Points issued", f"{s['points_issued']:,}"], ["Points redeemed", f"{s['points_redeemed']:,}"],
        ["Points outstanding", f"{s['points_outstanding']:,}"],
        ["Outstanding liability", ui.money(s["liability_value"], sym)],
        ["Discounts given", ui.money(s["discount_given"], sym)],
        ["Redemption rate", f"{s['redemption_rate_pct']}%"],
        ["Avg points / purchase", s["avg_points_per_purchase"]], ["Avg balance / customer", s["avg_balance"]],
    ], ["l", "r"], 40))
    print(ui.heading("Sales by category"))
    cdf = s["category_df"]
    print(ui.table(["Category", "Purchases", f"Sales ({sym})", "Points"],
                   [[r.category, r.purchases, f"{r.sales:,.2f}", r.points] for r in cdf.itertuples()], ["l", "r", "r", "r"]))
    print(ui.heading("Tier distribution"))
    print(ui.table(["Tier", "Customers", "Points balance"],
                   [[r.tier, r.customers, f"{r.points_balance:,}"] for r in s["tier_df"].itertuples()], ["l", "r", "r"]))
    print(ui.heading("Top customers by lifetime points"))
    print(ui.table(["ID", "Name", "Tier", "Balance", "Lifetime", f"Spend ({sym})", "Status"],
                   [[r.customer_id, r.name, r.tier, f"{r.balance:,}", f"{r.lifetime_points:,}",
                     f"{r.total_spend:,.2f}", r.status] for r in s["customers_df"].head(5).itertuples()],
                   ["l", "l", "l", "r", "r", "r", "l"]))
    print(ui.heading("Monthly sales trend"))
    mdf = s["monthly_df"]
    top = float(mdf["sales"].max()) if len(mdf) else 0
    for r in mdf.itertuples():
        print(f"  {r.month}  {ui.bar(r.sales, top)}  {sym}{r.sales:>11,.2f}   +{r.points_earned} / -{r.points_redeemed} pts")
    print("\n  Trend: " + ui.cyan(s["trend"]))
    n = len([a for a in generate_alerts(ledger.transactions, rules) if a.level != "INFO"])
    print("  Status: " + (ui.yellow(f"{n} warning(s) - see menu option 7") if n else ui.green("no warnings")))


def show_alerts(ledger: Ledger):
    print(ui.heading("Threshold warnings"))
    alerts = generate_alerts(ledger.transactions, ledger.rules)
    if not alerts:
        print(ui.green("  No warnings. Everything is within limits."))
    colour = {"CRITICAL": ui.red, "WARNING": ui.yellow, "INFO": ui.dim}
    for a in alerts:
        print(f"  {colour[a.level](f'[{a.level}]')} {a.message}")


def export_reports(ledger: Ledger):
    if not ledger.transactions:
        print("  Nothing to export yet.")
        return
    paths = export_all(ledger.transactions, ledger.rules)
    print(ui.green(f"\n  Exported {len(paths)} files to {EXPORT_DIR}:"))
    for p in paths:
        print("   - " + p.name)


def bulk_import(ledger: Ledger):
    print(ui.heading("Bulk CSV import"))
    print("  Required columns: customer_id, customer_name, date, category, type, amount (optional: points, notes)")
    print(f"  Try the messy demo file: {LEGACY_CSV}")
    path = ask("CSV path [Enter = demo file]: ", str, default=str(LEGACY_CSV))
    res = import_csv(path, ledger)
    if res.header_error:
        print(ui.red(f"  ! {res.header_error}"))
        return
    print(f"\n  Rows read: {res.total}   Imported: {ui.green(str(res.imported))}   Rejected: {ui.red(str(res.rejected))}")
    if res.errors:
        print(ui.table(["Row", "Field", "Problem"], [[e.row, e.field, e.message] for e in res.errors[:25]],
                       ["r", "l", "l"], 70))
        if len(res.errors) > 25:
            print(f"  ... and {len(res.errors) - 25} more")
        print(f"  Full row-by-row error log: {res.log_path}")


def calculator(ledger: Ledger):
    rules, sym = ledger.rules, ledger.rules["currency"]
    print(ui.heading("Points calculator (what-if, nothing is saved)"))
    amount = ask(f"Purchase amount ({sym}): ", lambda v: clean_amount(v, rules))
    show_categories()
    cat = ask("Category: ", category_cleaner(rules))
    print(ui.table(["Tier", "Multiplier", "Points earned"],
                   [[t["name"], f"x{t['multiplier']}", earn_points(amount, cat, t["multiplier"], rules)]
                    for t in sorted_tiers(rules)], ["l", "r", "r"]))
    print(f"  Redemption value: {rules['points_per_redeem_unit']} points = {sym}{rules['redeem_unit_value']:g} off "
          f"(min {rules['min_redeem_points']} pts, max {rules['max_redeem_pct_of_bill']}% of the bill).")


def load_sample(ledger: Ledger):
    print(ui.heading("Load sample data"))
    res = import_csv(SAMPLE_CSV, ledger)
    if res.header_error:
        print(ui.red(f"  ! {res.header_error}"))
        return
    print(ui.green(f"  Imported {res.imported} of {res.total} sample rows") +
          (f" ({res.rejected} skipped, e.g. duplicates from an earlier load)." if res.rejected else "."))


def make_charts(ledger: Ledger):
    from app.charts import save_charts
    if not ledger.transactions:
        print("  Nothing to chart yet.")
        return
    from datetime import datetime
    paths = save_charts(summary(ledger.transactions, ledger.rules), ledger.rules, EXPORT_DIR,
                        datetime.now().strftime("%Y%m%d_%H%M%S"))
    print(ui.green("  Saved charts:"))
    for p in paths:
        print("   - " + str(p))


MENU = [
    ("1", "Record a purchase", record_purchase),
    ("2", "Redeem points", redeem_points),
    ("3", "View all transactions", view_transactions),
    ("4", "Search records", search_records),
    ("5", "Customer lookup", customer_lookup),
    ("6", "Executive summary", show_summary),
    ("7", "Warnings & alerts", show_alerts),
    ("8", "Export reports (MD / TXT / CSV + charts)", export_reports),
    ("9", "Bulk import from CSV", bulk_import),
    ("10", "Points calculator (what-if)", calculator),
    ("11", "Save charts as PNG", make_charts),
    ("12", "Load sample data", load_sample),
]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ledger = Ledger.load()
    try:
        ledger.rules["currency"].encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        ledger.rules["currency"] = "Rs."
    print(ui.banner(APP_NAME.upper(), "Small Business & Retail  |  LocalImpact Challenge #90"))
    if ledger.notice:
        print(ui.yellow("  Note: " + ledger.notice))
    print(f"  {len(ledger.transactions)} record(s) loaded from {ledger.path.name}.")
    if not ledger.transactions:
        try:
            if input("  Data is empty. Load the sample dataset now? [y/N]: ").strip().lower() == "y":
                load_sample(ledger)
        except EOFError:
            pass

    actions = {k: fn for k, _, fn in MENU}
    while True:
        print(ui.heading("MAIN MENU"))
        for key, label, _ in MENU:
            print(f"  {key:>2}) {label}")
        print("   0) Exit")
        try:
            choice = input("\nSelect an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return
        if choice == "0":
            print("Data saved. Goodbye!")
            return
        action = actions.get(choice)
        if not action:
            print(ui.red("  ! Please choose a number from the menu."))
            continue
        try:
            action(ledger)
        except InputCancelled:
            print(ui.dim("  Cancelled."))
        except KeyboardInterrupt:
            print(ui.dim("\n  Cancelled."))
        except ValidationError as exc:
            print(ui.red(f"  ! {exc}"))


if __name__ == "__main__":
    main()
