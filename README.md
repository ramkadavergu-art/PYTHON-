# Loyalty Points Calculator

LocalImpact Challenge, Project #90 (Small Business & Retail). A Python app that turns purchase values into
loyalty points, handles redemptions, validates every input, saves everything locally, and reports on it.
It ships with two front-ends that share one core: a **menu-driven terminal app** and a **Streamlit GUI**.

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python cli.py                      # terminal app
streamlit run gui_streamlit.py     # web GUI (opens in your browser)
python -m unittest discover -s tests -v   # 18 unit tests
```

The terminal app needs only Python 3.10+ and pandas/matplotlib. Streamlit is needed only for the GUI.
On first launch with an empty data file, both apps offer to load the 48-row sample dataset.

## Brief requirement -> where it lives

| Requirement | Implementation |
|---|---|
| Interactive terminal interface | `cli.py` (menu loop, ANSI colour, paginated fixed-width tables, `q` cancels any prompt) |
| Defensive input validation | `app/validation.py`: one cleaner per field, `ask()` loops until valid; same cleaners guard the GUI and the importer |
| Core points engine | `app/rules.py` (earn, tiers, redemption checks) and `app/ledger.py` |
| Local persistence (JSON/CSV) | `app/storage.py`: JSON store `data/transactions.json` plus an automatic CSV mirror; atomic writes |
| Corrupt / missing file handling | Missing file -> fresh template. Unreadable or wrong-shaped file -> backed up as `*.corrupt-<time>` and replaced. Bad records are skipped and reported |
| Record query and search | `app/search.py`: keyword, category, type, tier, customer, date range, amount |
| Executive summary | `app/analytics.py`: totals, averages, tiers, top customers, monthly trend, status |
| Multi-format export | `app/exporter.py`: Markdown summary, plain-text audit log, customer CSV, transactions CSV, PNG charts |
| Threshold warnings | `app/alerts.py`: limit breach, large purchase, zero-point purchase, large redemption, high balance, inactive customers |
| Bulk CSV import | `app/importer.py`: format validation, duplicate detection, row-by-row error log in `logs/import_errors.csv` |
| GUI | `gui_streamlit.py`: Dashboard, Calculator, Record, Redeem, Search, Customers, Import, Reports, Warnings, Rules |
| Pandas/NumPy, Matplotlib | `analytics.py`, `charts.py` |
| 30-50 row sample data (CSV + JSON) | `sample_data/sample_data.csv` and `.json` (48 rows incl. boundary and edge cases) |

## Business rules (defaults, all in `app/config.py`)

- **Earning:** `floor(amount / 10 x category multiplier x tier multiplier)`. Categories: Electronics x1.5, Apparel and Beauty x1.25, others x1.0.
- **Tiers** (by lifetime earned points): Bronze 0 (x1.0), Silver 1,000 (x1.25), Gold 3,000 (x1.5). The tier is evaluated as of the transaction date.
- **Redeeming:** 100 points = 10 off. Minimum 100 points, multiples of 100, cannot exceed the balance, and the discount cannot exceed 50% of the bill. Redemption bills do not earn points.
- **Limits:** purchases from 0.01 to 50,000. Warning above 25,000. Balance warning above 3,000 points. Inactive after 90 days.
- **Currency:** the rupee sign. To change any rule, create `data/rules.json` with only the keys you want to override, for example `{"currency": "$", "rupees_per_point": 1}`.

## Suggested demo (matches section 9 of the brief)

1. `python cli.py`, accept the sample data, show the banner and menu.
2. Option 1: enter a bad customer ID (`X12`), a negative amount, `abc`, and a date like `2026-13-45`. Each is rejected and re-prompted. Then enter valid values.
3. Option 6 for the summary tables and trend bars, option 7 for the warnings.
4. Option 9 with the default demo file: 6 rows import, 12 are rejected, and each problem is listed with its row number.
5. Exit, run `python cli.py` again, and show the record count is preserved (persistence).
6. Option 8 to export reports and charts to `exports/`, or run the Streamlit GUI for the visual version.

## Project layout

```
cli.py                  terminal app            gui_streamlit.py   web GUI
app/                    shared core (config, models, validation, rules, storage, ledger,
                        search, analytics, alerts, charts, exporter, importer, ui)
sample_data/            sample CSV/JSON and a messy legacy CSV for the import demo
scripts/                generate_sample_data.py (re-creates the sample files)
tests/                  unit tests (validation, rules, storage recovery, import, analytics)
data/ exports/ logs/    runtime files (created automatically)
```

## CSV import format

Required columns: `customer_id, customer_name, date, category, type, amount`. Optional: `points`, `notes`.
`type` is `earn` or `redeem`. Redeem rows need `points`. Earn points are always recomputed from the rules.
Rows are applied in date order so balances are correct, and rows that duplicate an existing record are rejected.
