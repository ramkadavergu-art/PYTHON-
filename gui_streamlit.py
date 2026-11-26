"""Loyalty Points Calculator - Streamlit GUI.

Run:  streamlit run gui_streamlit.py
Uses the same core (app/) and the same data files as the terminal app.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from app.alerts import generate_alerts
from app.analytics import customer_table, summary, to_df
from app.charts import category_figure, trend_figure
from app.config import APP_NAME, CATEGORIES, EXPORT_DIR, SAMPLE_DIR, load_rules
from app.exporter import (export_all, render_audit_log, render_customer_csv, render_markdown,
                          render_transactions_csv)
from app.importer import errors_to_csv, import_csv, import_text
from app.ledger import Ledger
from app.rules import earn_points, next_tier, points_value, sorted_tiers
from app.search import search
from app.validation import ValidationError, clean_customer_id

st.set_page_config(page_title=APP_NAME, page_icon="\U0001F381", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; max-width: 1200px;}
      div[data-testid="stMetric"] {background: #eef1ee; border-radius: 8px; padding: 0.75rem 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

rules = load_rules()
SYM = rules["currency"]
ledger = Ledger.load(rules=rules)
if ledger.notice:
    st.warning(ledger.notice)


def money(x: float) -> str:
    return f"{SYM}{x:,.2f}"


def need_data() -> bool:
    if ledger.transactions:
        return False
    st.info("No records yet. Record a purchase, import a CSV, or load the sample dataset.")
    if st.button("Load sample data", type="primary"):
        res = import_csv(SAMPLE_DIR / "sample_data.csv", ledger)
        st.toast(f"Imported {res.imported} sample rows")
        st.rerun()
    return True


# ------------------------------------------------------------------ pages
def page_dashboard():
    st.title("Dashboard")
    if need_data():
        return
    s = summary(ledger.transactions, rules)
    row1 = st.columns(4)
    row1[0].metric("Customers", s["customers"])
    row1[1].metric("Total sales", money(s["total_sales"]))
    row1[2].metric("Points outstanding", f"{s['points_outstanding']:,}")
    row1[3].metric("Redemption rate", f"{s['redemption_rate_pct']}%")
    row2 = st.columns(4)
    row2[0].metric("Average purchase", money(s["avg_purchase"]))
    row2[1].metric("Points issued", f"{s['points_issued']:,}")
    row2[2].metric("Points redeemed", f"{s['points_redeemed']:,}")
    row2[3].metric("Liability", money(s["liability_value"]))
    st.caption(s["trend"])

    alerts = generate_alerts(ledger.transactions, rules)
    serious = [a for a in alerts if a.level != "INFO"]
    if serious:
        with st.expander(f"{len(serious)} warning(s) need attention", expanded=True):
            for a in serious:
                (st.error if a.level == "CRITICAL" else st.warning)(a.message)

    left, right = st.columns(2)
    with left:
        st.subheader("Monthly sales")
        st.bar_chart(s["monthly_df"].set_index("month")[["sales"]])
        st.subheader("Points earned vs redeemed")
        st.line_chart(s["monthly_df"].set_index("month")[["points_earned", "points_redeemed"]])
    with right:
        st.subheader("Sales by category")
        st.bar_chart(s["category_df"].set_index("category")[["sales"]])
        st.subheader("Tier distribution")
        st.bar_chart(s["tier_df"].set_index("tier")[["customers"]])
    st.subheader("Top customers")
    st.dataframe(s["customers_df"].head(5), hide_index=True)


def page_calculator():
    st.title("Points calculator")
    st.caption("A what-if tool. Nothing is saved.")
    left, right = st.columns(2)
    with left:
        st.subheader("Earning")
        amount = st.number_input(f"Purchase amount ({SYM})", min_value=0.0, max_value=float(rules["max_purchase"]),
                                 value=1500.0, step=50.0)
        cat = st.selectbox("Category", CATEGORIES)
        st.table({"Tier": [t["name"] for t in sorted_tiers(rules)],
                  "Multiplier": [f"x{t['multiplier']}" for t in sorted_tiers(rules)],
                  "Points earned": [earn_points(amount, cat, t["multiplier"], rules) for t in sorted_tiers(rules)]})
    with right:
        st.subheader("Redeeming")
        pts = st.number_input("Points to redeem", min_value=0, value=500, step=rules["redeem_step"])
        st.metric("Discount value", money(points_value(int(pts), rules)))
        st.caption(f"{rules['points_per_redeem_unit']} points = {SYM}{rules['redeem_unit_value']:g}. "
                   f"Minimum {rules['min_redeem_points']} points. Discount is capped at "
                   f"{rules['max_redeem_pct_of_bill']}% of the bill.")


def page_purchase():
    st.title("Record a purchase")
    with st.form("purchase_form"):
        c1, c2 = st.columns(2)
        cid = c1.text_input("Customer ID", placeholder="C001")
        name = c2.text_input("Customer name", help="Leave blank for existing customers.")
        d = c1.date_input("Date", value=date.today(), max_value=date.today())
        cat = c2.selectbox("Category", CATEGORIES)
        amount = c1.text_input(f"Purchase amount ({SYM})", placeholder="1250.50")
        notes = c2.text_input("Notes (optional)")
        submitted = st.form_submit_button("Save purchase", type="primary")
    if submitted:
        try:
            txn = ledger.add_purchase(cid, name, d, cat, amount, notes)
            st.success(f"Saved {txn.txn_id}: {txn.points} points earned ({txn.tier} tier). "
                       f"New balance: {ledger.balance(txn.customer_id):,} points.")
        except ValidationError as exc:
            st.error(str(exc))


def page_redeem():
    st.title("Redeem points")
    if need_data():
        return
    with st.form("redeem_form"):
        c1, c2 = st.columns(2)
        cid = c1.selectbox("Customer", ledger.customer_ids(),
                           format_func=lambda c: f"{c} - {ledger.name_of(c)} ({ledger.balance(c):,} pts)")
        d = c2.date_input("Date", value=date.today(), max_value=date.today())
        cat = c1.selectbox("Bill category", CATEGORIES)
        bill = c2.text_input(f"Bill amount ({SYM})", placeholder="2000")
        pts = c1.text_input("Points to redeem", placeholder=str(rules["redeem_step"]))
        submitted = st.form_submit_button("Redeem points", type="primary")
    if submitted:
        try:
            txn = ledger.add_redemption(cid, d, pts, bill, cat)
            st.success(f"Saved {txn.txn_id}: {-txn.points} points = {money(txn.discount)} off. "
                       f"Balance: {ledger.balance(cid):,} points.")
        except ValidationError as exc:
            st.error(str(exc))


def page_search():
    st.title("Transactions and search")
    if need_data():
        return
    c1, c2, c3, c4 = st.columns(4)
    kw = c1.text_input("Keyword", placeholder="name, ID, notes")
    cat = c2.selectbox("Category", ["All"] + CATEGORIES)
    typ = c3.selectbox("Type", ["All", "earn", "redeem"])
    tier = c4.selectbox("Tier", ["All"] + [t["name"] for t in sorted_tiers(rules)])
    c5, c6, c7 = st.columns(3)
    dfrom = c5.date_input("From", value=None)
    dto = c6.date_input("To", value=None)
    minamt = c7.number_input("Minimum amount", min_value=0.0, value=0.0, step=100.0)
    results = search(ledger.transactions, kw, "" if cat == "All" else cat, "" if typ == "All" else typ, "",
                     "" if tier == "All" else tier, dfrom.isoformat() if dfrom else "",
                     dto.isoformat() if dto else "", minamt or None)
    st.caption(f"{len(results)} of {len(ledger.transactions)} records")
    st.dataframe(to_df(results).assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d")), hide_index=True)
    st.download_button("Download results (CSV)", render_transactions_csv(results), "search_results.csv", "text/csv")


def page_customers():
    st.title("Customers")
    if need_data():
        return
    table = customer_table(ledger.transactions, rules)
    st.dataframe(table, hide_index=True)
    st.subheader("Customer detail")
    cid = st.selectbox("Customer", table["customer_id"], format_func=lambda c: f"{c} - {ledger.name_of(c)}")
    life, bal = ledger.lifetime(cid), ledger.balance(cid)
    a, b, c = st.columns(3)
    a.metric("Tier", ledger.tier(cid)["name"])
    b.metric("Balance", f"{bal:,} pts", money(points_value(bal, rules)))
    c.metric("Lifetime points", f"{life:,}")
    nxt = next_tier(life, rules)
    if nxt:
        target = life + nxt[1]
        st.progress(min(life / target, 1.0), text=f"{nxt[1]:,} more lifetime points to reach {nxt[0]['name']}")
    else:
        st.success("Top tier reached.")
    st.dataframe(to_df(ledger.history(cid)[::-1]).assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d")),
                 hide_index=True)


def page_import():
    st.title("Bulk CSV import")
    st.caption("Required columns: customer_id, customer_name, date, category, type, amount. "
               "Optional: points (needed for redeem rows), notes. Duplicates and invalid rows are rejected and logged.")
    up = st.file_uploader("Choose a CSV file", type="csv")
    c1, c2 = st.columns(2)
    run = c1.button("Run import", type="primary", disabled=up is None)
    demo = c2.button("Import the messy demo file")
    res = None
    if run and up is not None:
        try:
            res = import_text(up.getvalue().decode("utf-8-sig"), ledger)
        except UnicodeDecodeError:
            st.error("That file is not UTF-8 text. Save it as CSV (UTF-8) and try again.")
    elif demo:
        res = import_csv(SAMPLE_DIR / "legacy_import_with_errors.csv", ledger)
    if res is None:
        return
    if res.header_error:
        st.error(res.header_error)
        return
    m = st.columns(3)
    m[0].metric("Rows read", res.total)
    m[1].metric("Imported", res.imported)
    m[2].metric("Rejected", res.rejected)
    if res.errors:
        st.subheader("Row-by-row error log")
        st.dataframe([{"row": e.row, "field": e.field, "problem": e.message, "raw": e.raw} for e in res.errors],
                     hide_index=True)
        st.download_button("Download error log (CSV)", errors_to_csv(res.errors), "import_errors.csv", "text/csv")
    else:
        st.success("Every row imported cleanly.")


def page_reports():
    st.title("Reports and export")
    if need_data():
        return
    s = summary(ledger.transactions, rules)
    tabs = st.tabs(["Summary", "Charts", "Downloads"])
    with tabs[0]:
        st.markdown(render_markdown(ledger.transactions, rules))
    with tabs[1]:
        st.pyplot(trend_figure(s["monthly_df"], rules))
        st.pyplot(category_figure(s["category_df"], rules))
    with tabs[2]:
        a, b = st.columns(2)
        a.download_button("Markdown summary", render_markdown(ledger.transactions, rules), "summary_report.md", "text/markdown")
        a.download_button("Plain-text audit log", render_audit_log(ledger.transactions, rules), "audit_log.txt", "text/plain")
        b.download_button("Customer report (CSV)", render_customer_csv(ledger.transactions, rules), "customer_report.csv", "text/csv")
        b.download_button("Transactions (CSV)", render_transactions_csv(ledger.transactions), "transactions_report.csv", "text/csv")
        if st.button("Also write all files to the exports/ folder"):
            paths = export_all(ledger.transactions, rules)
            st.success(f"Wrote {len(paths)} files to {EXPORT_DIR}")


def page_alerts():
    st.title("Warnings")
    if need_data():
        return
    alerts = generate_alerts(ledger.transactions, rules)
    if not alerts:
        st.success("No warnings. Everything is within limits.")
    for a in alerts:
        {"CRITICAL": st.error, "WARNING": st.warning, "INFO": st.info}[a.level](f"{a.message}")


def page_rules():
    st.title("Rules")
    st.caption("To change these, add overrides to data/rules.json and reload.")
    st.json(rules)


PAGES = {
    "Dashboard": page_dashboard, "Points calculator": page_calculator, "Record purchase": page_purchase,
    "Redeem points": page_redeem, "Transactions and search": page_search, "Customers": page_customers,
    "Bulk import": page_import, "Reports and export": page_reports, "Warnings": page_alerts, "Rules": page_rules,
}

st.sidebar.title(APP_NAME)
choice = st.sidebar.radio("Go to", list(PAGES), label_visibility="collapsed")
PAGES[choice]()
st.sidebar.caption(f"{len(ledger.transactions)} records in {ledger.path.name}")
