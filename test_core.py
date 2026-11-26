"""Run with:  python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage  # noqa: E402
from app.alerts import generate_alerts  # noqa: E402
from app.analytics import summary  # noqa: E402
from app.config import load_rules  # noqa: E402
from app.exporter import render_audit_log, render_markdown  # noqa: E402
from app.importer import import_csv, import_text  # noqa: E402
from app.ledger import Ledger  # noqa: E402
from app.rules import check_redemption, earn_points, get_tier  # noqa: E402
from app.search import search  # noqa: E402
from app.validation import (InputCancelled, ValidationError, ask, clean_amount, clean_customer_id,  # noqa: E402
                            clean_date, clean_name, clean_points)

RULES = load_rules(Path("/nonexistent/rules.json"))


class TestValidation(unittest.TestCase):
    def test_amount_valid_and_formats(self):
        self.assertEqual(clean_amount("1,250.50", RULES), 1250.5)
        self.assertEqual(clean_amount("\u20b9 99", RULES), 99.0)
        self.assertEqual(clean_amount(0.01, RULES), 0.01)

    def test_amount_rejects_bad_values(self):
        for bad in ["", "abc", "-5", "0", "nan", "inf", "50000.01", None]:
            with self.assertRaises(ValidationError, msg=str(bad)):
                clean_amount(bad, RULES)

    def test_ids_names_dates_points(self):
        self.assertEqual(clean_customer_id(" c001 "), "C001")
        for bad in ["", "X1", "C1", "C12345678"]:
            with self.assertRaises(ValidationError):
                clean_customer_id(bad)
        with self.assertRaises(ValidationError):
            clean_name("123")
        for bad in ["2026-02-31", "31/01/2026", "2999-01-01", "1999-12-31", ""]:
            with self.assertRaises(ValidationError):
                clean_date(bad)
        self.assertEqual(clean_points("300"), 300)
        for bad in ["0", "-1", "1.5", "x", ""]:
            with self.assertRaises(ValidationError):
                clean_points(bad)

    def test_ask_loops_until_valid_and_cancels(self):
        with mock.patch("builtins.input", side_effect=["", "abc", "-1", "250"]):
            self.assertEqual(ask("> ", lambda v: clean_amount(v, RULES), printer=lambda *_: None), 250.0)
        with mock.patch("builtins.input", side_effect=["q"]):
            with self.assertRaises(InputCancelled):
                ask("> ", clean_name)


class TestRules(unittest.TestCase):
    def test_earn_boundaries(self):
        self.assertEqual(earn_points(0.01, "Grocery", 1.0, RULES), 0)
        self.assertEqual(earn_points(9.99, "Grocery", 1.0, RULES), 0)
        self.assertEqual(earn_points(10.0, "Grocery", 1.0, RULES), 1)
        self.assertEqual(earn_points(1000, "Electronics", 1.5, RULES), 225)   # 100 x 1.5 x 1.5

    def test_tiers(self):
        self.assertEqual(get_tier(0, RULES)["name"], "Bronze")
        self.assertEqual(get_tier(999, RULES)["name"], "Bronze")
        self.assertEqual(get_tier(1000, RULES)["name"], "Silver")
        self.assertEqual(get_tier(3000, RULES)["name"], "Gold")

    def test_redemption_rules(self):
        self.assertEqual(check_redemption(100, 500, 200, RULES), 10.0)
        for pts, bal, bill in [(50, 500, 500), (150, 500, 500), (600, 500, 5000), (1000, 5000, 100)]:
            with self.assertRaises(ValidationError):
                check_redemption(pts, bal, bill, RULES)


class TestLedgerStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tx.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_creates_template(self):
        led = Ledger.load(self.path, RULES)
        self.assertEqual(led.transactions, [])
        self.assertTrue(self.path.exists())
        self.assertIn("fresh", led.notice)

    def test_corrupt_file_is_backed_up(self):
        self.path.write_text("{not json", encoding="utf-8")
        led = Ledger.load(self.path, RULES)
        self.assertEqual(led.transactions, [])
        self.assertTrue(list(Path(self.tmp.name).glob("tx.json.corrupt-*")))
        self.assertEqual(json.loads(self.path.read_text())["transactions"], [])

    def test_empty_and_wrong_shape_files(self):
        self.path.write_text("", encoding="utf-8")
        self.assertEqual(Ledger.load(self.path, RULES).transactions, [])
        self.path.write_text('"just a string"', encoding="utf-8")
        self.assertEqual(Ledger.load(self.path, RULES).transactions, [])

    def test_invalid_records_skipped_good_kept(self):
        led = Ledger.load(self.path, RULES)
        led.add_purchase("C001", "Asha Rao", "2026-01-10", "Grocery", 500)
        data = json.loads(self.path.read_text())
        data["transactions"].append({"txn_id": "T9", "oops": 1})
        self.path.write_text(json.dumps(data), encoding="utf-8")
        again = Ledger.load(self.path, RULES)
        self.assertEqual(len(again.transactions), 1)
        self.assertIn("Skipped 1", again.notice)

    def test_persistence_survives_restart(self):
        led = Ledger.load(self.path, RULES)
        led.add_purchase("C001", "Asha Rao", "2026-01-10", "Electronics", 2000)
        reloaded = Ledger.load(self.path, RULES)
        self.assertEqual(reloaded.balance("C001"), led.balance("C001"))
        self.assertEqual(reloaded.balance("C001"), 300)
        self.assertTrue(self.path.with_suffix(".csv").exists())

    def test_purchase_and_redeem_flow(self):
        led = Ledger.load(self.path, RULES)
        led.add_purchase("C001", "Asha Rao", "2026-01-10", "Grocery", 5000)      # 500 pts
        led.add_purchase("C001", "", "2026-01-11", "Grocery", 5000)              # existing name reused
        self.assertEqual(led.balance("C001"), 500 + 500)
        t = led.add_redemption("C001", "2026-01-12", 300, 1000, "Grocery")
        self.assertEqual((t.points, t.discount), (-300, 30.0))
        self.assertEqual(led.balance("C001"), 700)
        with self.assertRaises(ValidationError):
            led.add_redemption("C001", "2026-01-12", 5000, 90000 % 50000, "Grocery")
        with self.assertRaises(ValidationError):
            led.add_redemption("C777", "2026-01-12", 100, 500)
        with self.assertRaises(ValidationError):
            led.add_purchase("C001", "Someone Else", "2026-01-12", "Grocery", 100)

    def test_tier_upgrade_applies_bonus(self):
        led = Ledger.load(self.path, RULES)
        led.add_purchase("C001", "Asha Rao", "2026-01-01", "Other", 10000)       # 1000 pts -> Silver
        t = led.add_purchase("C001", "", "2026-01-02", "Other", 1000)           # 100 x 1.25
        self.assertEqual((t.tier, t.points), ("Silver", 125))


class TestImportAnalyticsSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = Ledger.load(Path(self.tmp.name) / "tx.json", RULES)
        self.logs = Path(self.tmp.name) / "logs"

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_good_and_bad_rows(self):
        csv_text = ("customer_id,customer_name,date,category,type,amount,points\n"
                    "C001,Asha Rao,2026-03-01,Grocery,earn,2000,\n"
                    "C001,Asha Rao,2026-03-05,Grocery,redeem,800,100\n"
                    "C001,Asha Rao,2026-03-01,Grocery,earn,2000,\n"          # duplicate
                    "BAD,Asha Rao,2026-03-01,Grocery,earn,10,\n"
                    "C002,Ravi K,2026-03-02,Toys,earn,10,\n"
                    "C003,Meena S,2026-03-03,Dining,redeem,500,100\n")       # unknown / no balance
        res = import_text(csv_text, self.led, self.logs)
        self.assertEqual((res.total, res.imported, res.rejected), (6, 2, 4))
        self.assertTrue(res.log_path.exists())
        self.assertEqual(self.led.balance("C001"), 200 - 100)

    def test_import_header_problems(self):
        self.assertIn("Missing required", import_text("a,b\n1,2\n", self.led, self.logs).header_error)
        self.assertIn("empty", import_text("", self.led, self.logs).header_error)
        self.assertIn("not found", import_csv("/no/such/file.csv", self.led, self.logs).header_error)

    def test_summary_search_alerts_exports(self):
        self.led.add_purchase("C001", "Asha Rao", "2026-02-01", "Electronics", 50000)
        self.led.add_purchase("C002", "Ravi K", "2026-03-01", "Grocery", 0.01)
        self.led.add_redemption("C001", "2026-03-02", 1000, 5000, "Electronics")
        s = summary(self.led.transactions, RULES)
        self.assertEqual(s["points_issued"], s["points_redeemed"] + s["points_outstanding"])
        self.assertEqual(s["customers"], 2)
        self.assertEqual(len(search(self.led.transactions, keyword="asha")), 2)
        self.assertEqual(len(search(self.led.transactions, category="Grocery")), 1)
        self.assertEqual(len(search(self.led.transactions, type_="redeem")), 1)
        codes = {a.code for a in generate_alerts(self.led.transactions, RULES)}
        self.assertIn("LIMIT_BREACH", codes)
        self.assertIn("ZERO_POINTS", codes)
        self.assertIn("Executive Summary", render_markdown(self.led.transactions, RULES))
        self.assertIn("LEDGER CHECK", render_audit_log(self.led.transactions, RULES))

    def test_empty_ledger_is_safe(self):
        s = summary([], RULES)
        self.assertEqual(s["transactions"], 0)
        self.assertEqual(generate_alerts([], RULES), [])
        self.assertIn("No warnings", render_markdown([], RULES))


if __name__ == "__main__":
    unittest.main()
