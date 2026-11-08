from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealpilot.workflow import WorkflowError, WorkflowStore, _amount, _required_documents


ROOT = Path(__file__).resolve().parents[2]


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary_directory.name)
        self.clock_path = temporary_path / "clock.json"
        self.store = WorkflowStore(
            ROOT,
            database_path=temporary_path / "workflow.sqlite3",
            clock_path=self.clock_path,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_amount_parser_handles_suffixes_and_rejects_ranges(self) -> None:
        self.assertEqual(_amount("$1.2m"), 1_200_000)
        self.assertEqual(_amount("60 months"), 60)
        self.assertIsNone(_amount("approx. $400-500k"))

    def test_bank_statement_check_requires_six_recent_full_months(self) -> None:
        months = [
            ("2026-02-01", "2026-02-28"),
            ("2026-03-01", "2026-03-31"),
            ("2026-04-01", "2026-04-30"),
            ("2026-05-01", "2026-05-31"),
            ("2026-06-01", "2026-06-30"),
            ("2026-10-01", "2026-10-20"),
        ]
        pages = [
            (Path(f"statement-{index}.pdf"), 1, f"Statement period\n{start} to {end}")
            for index, (start, end) in enumerate(months)
        ]
        requirements = _required_documents(
            {"businessEstablished": {"value": "2010-01-01"}},
            [],
            pages,
            datetime(2026, 11, 2, 8, tzinfo=timezone(timedelta(hours=-6))),
        )
        statements = next(item for item in requirements if item["key"] == "bank_statement")
        self.assertEqual(statements["status"], "missing")
        self.assertEqual(statements["monthCount"], 2)

    def test_historical_sla_baseline_uses_stage_event_records(self) -> None:
        baseline = self.store._historical_sla()
        self.assertTrue(baseline["available"])
        self.assertEqual((baseline["events"], baseline["breaches"]), (1288, 162))
        product = next(item for item in baseline["byStage"] if item["stage"] == "Product")
        committed = next(item for item in baseline["byStage"] if item["stage"] == "Committed")
        self.assertEqual((product["events"], product["breaches"]), (197, 28))
        self.assertEqual((committed["events"], committed["breaches"]), (192, 28))

    def test_snapshot_extracts_packets_and_required_evidence(self) -> None:
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot["loans"]), 13)
        self.assertEqual(snapshot["historicalSla"]["events"], 1288)
        expected_checks = {
            "STMT_TAMPER",
            "REV_INFLATION",
            "ID_MISMATCH",
            "DUP_EIN",
            "SHELL_ADDRESS",
        }
        self.assertTrue(all(
            {check["code"] for check in loan["checks"]} == expected_checks
            for loan in snapshot["loans"]
        ))
        lone_star = next(
            item for item in snapshot["loans"]
            if item["folder"] == "LSFD_docs"
        )
        self.assertEqual(lone_star["borrower"], "Lone Star Family Dental PLLC")
        first = snapshot["loans"][0]
        self.assertTrue(first["application"])
        self.assertTrue(first["requiredDocuments"])
        self.assertEqual(
            {check["code"] for check in first["checks"]},
            {"STMT_TAMPER", "REV_INFLATION", "ID_MISMATCH", "DUP_EIN", "SHELL_ADDRESS"},
        )
        self.assertTrue(first["ownerName"])
        loopwise = next(
            item for item in snapshot["loans"] if "Loopwise Labs" in item["borrower"]
        )
        statements = next(
            item for item in loopwise["requiredDocuments"]
            if item["key"] == "bank_statement"
        )
        self.assertEqual(statements["monthCount"], 5)
        self.assertIn("5 of 6", statements["label"])
        granite = next(
            item for item in snapshot["loans"]
            if "Granite Bay Building Supply" in item["borrower"]
        )
        granite_checks = {check["code"]: check for check in granite["checks"]}
        self.assertEqual(granite_checks["STMT_TAMPER"]["status"], "flagged")
        self.assertIn("$48,200.00", granite_checks["STMT_TAMPER"]["evidence"])
        self.assertEqual(granite_checks["REV_INFLATION"]["status"], "flagged")
        self.assertIn("3.06x", granite_checks["REV_INFLATION"]["evidence"])
        self.assertEqual(
            {
                code for code, check in granite_checks.items()
                if check["status"] == "flagged"
            },
            {"STMT_TAMPER", "REV_INFLATION"},
        )

    def test_intake_blockers_do_not_require_credit_stage_documents(self) -> None:
        self.store.snapshot()
        with self.store._connection() as connection:
            deal_id = connection.execute(
                "SELECT id FROM workflow_deals ORDER BY borrower LIMIT 1"
            ).fetchone()["id"]
            packet = next(
                item for item in self.store._seed_and_refresh(connection)
                if item["id"] == deal_id
            )
            deal = connection.execute(
                "SELECT * FROM workflow_deals WHERE id=?",
                (deal_id,),
            ).fetchone()
            blockers = self.store._blockers("Intake", packet, deal)
        credit_only = {
            "bank_statement",
            "financial_statements",
            "tax_return",
            "tax_extension",
            "equipment_quote",
        }
        credit_only_missing = {
            requirement["label"] for requirement in packet["requirements"]
            if requirement["key"] in credit_only and requirement["status"] != "received"
        }
        self.assertFalse(credit_only_missing.intersection(blockers))

    def test_assigned_owner_can_request_information_without_approval_gate(self) -> None:
        first = self.store.snapshot()["loans"][0]
        result = self.store.act(
            first["id"],
            {
                "action": "request_info",
                "actor": first["owner"],
                "note": "Please provide the missing item.",
            },
        )
        updated = next(item for item in result["loans"] if item["id"] == first["id"])
        self.assertEqual(updated["status"], "awaiting_information")
        self.assertEqual(updated["history"][0]["event_type"], "information_requested")

    def test_human_hold_lifecycle_requires_review_and_rationale(self) -> None:
        first = self.store.snapshot()["loans"][0]
        with self.assertRaises(WorkflowError):
            self.store.act(
                first["id"],
                {"action": "request_hold", "actor": first["owner"]},
            )
        self.store.act(
            first["id"],
            {
                "action": "request_hold",
                "actor": first["owner"],
                "note": "Cross-deal payment relationship requires verification.",
            },
        )
        with self.assertRaises(WorkflowError):
            self.store.act(
                first["id"],
                {
                    "action": "place_hold",
                    "actor": first["owner"],
                    "note": "Unauthorized test attempt.",
                },
            )
        placed = self.store.act(
            first["id"],
            {
                "action": "place_hold",
                "actor": "victor.hale",
                "note": "Review the payment account against supplier records.",
            },
        )
        held = next(item for item in placed["loans"] if item["id"] == first["id"])
        self.assertEqual(held["fraudHold"], "placed")
        released = self.store.act(
            first["id"],
            {
                "action": "release_hold",
                "actor": "victor.hale",
                "note": "Independent verification completed.",
            },
        )
        cleared = next(item for item in released["loans"] if item["id"] == first["id"])
        self.assertEqual(cleared["fraudHold"], "none")
        self.assertEqual(cleared["fraudReview"], "cleared")

    def test_human_approval_requires_the_assigned_owner(self) -> None:
        first = self.store.snapshot()["loans"][0]
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE workflow_deals SET stage='Committee' WHERE id=?",
                (first["id"],),
            )
        committee = next(
            item for item in self.store.snapshot()["loans"] if item["id"] == first["id"]
        )
        with self.assertRaises(WorkflowError):
            self.store.act(first["id"], {"action": "approve", "actor": "unauthorized"})
        with self.assertRaises(WorkflowError):
            self.store.act(
                first["id"],
                {"action": "approve", "actor": committee["owner"]},
            )
        result = self.store.act(
            first["id"],
            {
                "action": "approve",
                "actor": committee["owner"],
                "note": "Approval recorded for workflow test.",
            },
        )
        updated = next(item for item in result["loans"] if item["id"] == first["id"])
        self.assertEqual(updated["stage"], "Product")
        self.assertEqual(updated["history"][0]["event_type"], "stage_advanced")

    def test_credit_officer_resolution_is_persisted_with_rationale(self) -> None:
        initial = self.store.snapshot()
        deal = next(
            item for item in initial["loans"]
            if any(check["status"] in ("flagged", "not_evaluable") for check in item["checks"])
        )
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE workflow_deals SET stage='Credit' WHERE id=?",
                (deal["id"],),
            )
        credit_deal = next(
            item for item in self.store.snapshot()["loans"] if item["id"] == deal["id"]
        )
        check = next(
            check for check in credit_deal["checks"]
            if check["status"] in ("flagged", "not_evaluable")
        )
        result = self.store.act(
            deal["id"],
            {
                "action": "resolve_check",
                "actor": credit_deal["owner"],
                "checkCode": check["code"],
                "note": "Reviewed source documents and documented the decision.",
            },
        )
        updated = next(item for item in result["loans"] if item["id"] == deal["id"])
        reviewed = next(item for item in updated["checks"] if item["code"] == check["code"])
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertIn("documented the decision", reviewed["evidence"])

    def test_simulation_clock_creates_three_sla_escalation_levels(self) -> None:
        first = self.store.snapshot()["loans"][0]
        self.store.advance_clock("8h")
        self.store.advance_clock("4h")
        self.store.advance_clock("4h")
        alerts = self.store.snapshot()["alerts"]
        levels = {
            alert["level"] for alert in alerts if alert["dealId"] == first["id"]
        }
        self.assertEqual(levels, {1, 2, 3})
        self.assertIn("RM", next(
            alert["message"] for alert in alerts if alert["dealId"] == first["id"]
        ))
        persisted_clock = json.loads(self.clock_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted_clock["now"], "2026-11-03T00:00-06:00")


if __name__ == "__main__":
    unittest.main()
