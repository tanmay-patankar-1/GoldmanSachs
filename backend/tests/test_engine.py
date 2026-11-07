from __future__ import annotations

import unittest
from pathlib import Path
import shutil
import tempfile

from dealpilot.engine import (
    Page,
    Packet,
    _expense_proportions,
    _payment_signals,
    _financial_similarity,
    _preparer_details,
    _preparer_signals,
    _risk_scores,
    _similar_expense_structure,
    _supplier_concentration_signals,
    _supplier_name_variation_signals,
    analyze,
    detect_sequential_identifiers,
    normalize_supplier,
)


ROOT = Path(__file__).resolve().parents[2]


class PatternDetectionTests(unittest.TestCase):
    def test_supplier_suffix_normalization_does_not_change_core_name(self) -> None:
        self.assertEqual(
            normalize_supplier("Northstar Supply, Inc."),
            normalize_supplier("Northstar Supply LLC"),
        )

    def test_sequential_identifiers_require_distinct_deals_and_three_numbers(self) -> None:
        quotes = [
            {"id": "QX-811", "prefix": "QX", "number": 811, "supplierKey": "vendor", "borrower": "A"},
            {"id": "QX-812", "prefix": "QX", "number": 812, "supplierKey": "vendor", "borrower": "B"},
            {"id": "QX-813", "prefix": "QX", "number": 813, "supplierKey": "vendor", "borrower": "C"},
        ]
        self.assertEqual(len(detect_sequential_identifiers(quotes)), 1)
        self.assertEqual(
            detect_sequential_identifiers(quotes[:2]),
            [],
        )
        repeated_borrower = [*quotes[:2], {**quotes[2], "borrower": "A"}]
        self.assertEqual(detect_sequential_identifiers(repeated_borrower), [])
        gap = [quotes[0], quotes[1], {**quotes[2], "number": 814, "id": "QX-814"}]
        self.assertEqual(detect_sequential_identifiers(gap), [])

    def test_financial_matching_uses_values_not_templates(self) -> None:
        left = {f"metric-{index}": float(index) for index in range(7)}
        right = dict(left)
        self.assertEqual(_financial_similarity(left, right)[0], "exact")
        different_business_values = {key: value * 1.15 for key, value in left.items()}
        self.assertEqual(_financial_similarity(left, different_business_values)[0], "")
        right["metric-6"] += 0.01
        self.assertEqual(_financial_similarity(left, right)[0], "near")
        self.assertEqual(_financial_similarity({"revenue": 5}, {"revenue": 5})[0], "")

    def test_legitimate_shared_supplier_is_shown_without_score_contribution(self) -> None:
        packets = []
        for borrower in ("Borrower One LLC", "Borrower Two Inc.", "Borrower Three Ltd."):
            packet = Packet(borrower, borrower, [], borrower)
            packet.quotes = [{
                "borrower": borrower,
                "supplier": "Regional Equipment Co",
                "supplierKey": "regional equipment",
                "accountLast4": "1234",
                "source": "quote.pdf:page 1",
                "date": "2026-01-01",
            }]
            packets.append(packet)
        history = [{
            "borrower": "Prior Customer LLC",
            "borrowerKey": "prior customer",
            "supplier": "Regional Equipment Inc.",
            "supplierKey": "regional equipment",
            "accountLast4": "1234",
            "dealId": "H-2601-001",
            "date": "2026-01-01",
            "source": "deals.csv:line 2",
            "current": False,
            "status": "funded",
            "repaymentStatus": "current",
            "fraudLabel": "none",
        }]
        signals, _ = _payment_signals(packets, history)
        self.assertEqual(len(signals), 1)
        self.assertFalse(signals[0]["scoreContribution"])
        scores = _risk_scores(signals, packets)
        self.assertTrue(all(score["score"] == 0 for score in scores.values()))

    def test_correlated_evidence_family_counts_once(self) -> None:
        packets = [
            type("Packet", (), {"name": "Borrower A"})(),
        ]
        signals = [
            {"family": "payment_relationship", "borrowers": ["Borrower A"]},
            {"family": "payment_relationship", "borrowers": ["Borrower A"]},
            {"family": "document_identifier", "borrowers": ["Borrower A"]},
        ]
        score = _risk_scores(signals, packets)["borrower a"]
        self.assertEqual(score["families"], ["document_identifier", "payment_relationship"])
        self.assertEqual(score["score"], 50)

    def test_supplier_name_variation_is_only_a_candidate_observation(self) -> None:
        current = [{
            "borrower": "Current Borrower",
            "borrowerKey": "current borrower",
            "supplier": "Northstar Supply LLC",
            "supplierKey": "northstar supply",
            "accountLast4": "1111",
            "dealId": None,
            "date": "",
            "status": "current_application",
            "repaymentStatus": "",
            "fraudLabel": "none",
            "source": "quote.pdf:page 1",
            "current": True,
        }]
        history = [{
            **current[0],
            "borrower": "Prior Borrower",
            "borrowerKey": "prior borrower",
            "supplier": "Northstar Supply Inc.",
            "accountLast4": "2222",
            "dealId": "H-1",
            "source": "deals.csv:line 2",
            "current": False,
        }]
        signal = _supplier_name_variation_signals(current, history)[0]
        self.assertEqual(signal["family"], "supplier_name_variation")
        self.assertFalse(signal["scoreContribution"])
        self.assertEqual(len(signal["evidence"]), 2)

    def test_supplier_concentration_uses_historical_reference(self) -> None:
        history = []
        for index in range(4):
            history.append({
                "supplierKey": f"historic {index}",
                "dealId": f"H-{index}",
            })
        current = [
            {
                "supplierKey": "shared vendor",
                "supplier": "Shared Vendor",
                "borrowerKey": f"borrower {index}",
                "borrower": f"Borrower {index}",
                "source": f"quote-{index}.pdf:page 1",
                "current": True,
                "dealId": None,
            }
            for index in range(5)
        ]
        signals = _supplier_concentration_signals(current, history)
        self.assertEqual(len(signals), 1)
        self.assertIn("5 current borrowers", signals[0]["evidence"][-1]["text"])

    def test_preparer_network_requires_shared_identity_and_contact(self) -> None:
        root = Path.cwd()
        packets = [
            Packet(
                f"Borrower {index}",
                f"Borrower {index}",
                [Page(root / f"statement-{index}.pdf", 1,
                      "Prepared by: A. Person, Person Firm � 214-555-0100")],
                f"Borrower {index}",
            )
            for index in range(3)
        ]
        history = [{
            "borrower": "Historical Borrower",
            "borrowerKey": "historical borrower",
            "name": "A. Person",
            "firm": "Person Firm",
            "phone": "2145550100",
            "source": "deals.csv:line 2",
            "dealId": "H-1",
            "date": "2025-01-01",
            "status": "funded",
            "repaymentStatus": "current",
            "current": False,
        }]
        signals = _preparer_signals(packets, history, root)
        self.assertEqual(len(signals), 1)
        self.assertEqual(len(signals[0]["borrowers"]), 3)
        self.assertFalse(signals[0]["scoreContribution"])
        self.assertTrue(any("ending 0100" in item["text"] for item in signals[0]["evidence"]))

    def test_preparer_contact_is_not_inferred_from_borrower_phone(self) -> None:
        root = Path.cwd()
        page = Page(
            root / "statement.pdf",
            1,
            "Prepared by: A. Person, Person Firm\nBusiness phone 214-555-0100",
        )
        details = _preparer_details([page], root)
        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details["phone"], "")

    def test_expense_structure_compares_ratios_not_templates(self) -> None:
        left = {
            "revenue": 1000,
            "cost of goods sold": 400,
            "payroll": 200,
            "rent & occupancy": 100,
            "other operating": 150,
        }
        near = {
            "revenue": 2000,
            "cost of goods sold": 800,
            "payroll": 404,
            "rent & occupancy": 200,
            "other operating": 300,
        }
        unrelated = {
            **near,
            "cost of goods sold": 1600,
            "payroll": 900,
            "rent & occupancy": 700,
        }
        self.assertEqual(_expense_proportions(left)["payroll"], 0.2)
        self.assertTrue(_similar_expense_structure(left, near)[0])
        self.assertFalse(_similar_expense_structure(left, unrelated)[0])


class DatasetIntegrationTests(unittest.TestCase):
    def test_dataset_evidence_is_extracted_with_sources(self) -> None:
        result = analyze(ROOT)
        signals = result["signals"]
        self.assertEqual(result["summary"]["historicalDeals"], 257)
        self.assertEqual(result["summary"]["currentPackets"], 13)

        payment = next(signal for signal in signals if signal["family"] == "payment_relationship")
        self.assertEqual(len(payment["borrowers"]), 3)
        self.assertGreaterEqual(len(payment["historicalDeals"]), 20)
        self.assertTrue(all(item["source"] for item in payment["evidence"]))
        self.assertTrue(any("4471" in item["text"] for item in payment["evidence"]))
        self.assertTrue(any("9902" in item["text"] for item in payment["evidence"]))

        name_variation = next(
            signal for signal in signals if signal["family"] == "supplier_name_variation"
        )
        self.assertFalse(name_variation["scoreContribution"])
        self.assertTrue(any("Metroplex Equipment Supply LLC" in item["text"] for item in name_variation["evidence"]))
        self.assertTrue(any("Metroplex Equipment Supply, Inc." in item["text"] for item in name_variation["evidence"]))

        sequence = next(signal for signal in signals if signal["family"] == "document_identifier")
        self.assertTrue(all(
            any(quote in item["text"] for item in sequence["evidence"])
            for quote in ("MQ-0201", "MQ-0202", "MQ-0203")
        ))

        duplicate = next(
            signal for signal in signals if signal["family"] == "financial_data_similarity"
        )
        self.assertEqual(len(duplicate["borrowers"]), 2)
        self.assertTrue(any("Revenue $470,400.00" in item["text"] for item in duplicate["evidence"]))
        self.assertTrue(any(signal["family"] == "financial_structure_similarity" for signal in signals))
        preparer = next(signal for signal in signals if signal["family"] == "preparer_network")
        self.assertGreaterEqual(len(preparer["borrowers"]), 2)
        self.assertTrue(all(item["source"] for item in preparer["evidence"]))
        self.assertTrue(
            any(node["kind"] == "preparer_network" for node in result["graph"]["nodes"])
        )
        self.assertTrue(result["scoreMethod"].startswith("Uncalibrated review-priority index"))
        self.assertIn("DUP_EIN", result["baseline"]["knownFraudLabels"])
        cohorts = result["baseline"]["depositBehavior"]["repaymentCohorts"]
        self.assertGreater(
            cohorts["30_days_past_due"]["meanRoundDepositPct"],
            cohorts["current"]["meanRoundDepositPct"],
        )

    def test_reanalysis_picks_up_new_packet_folders(self) -> None:
        participant = ROOT / "participant" / "participant"
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            target = data_root / "participant" / "participant"
            history = target / "history"
            shared = target / "shared_folder"
            history.mkdir(parents=True)
            shared.mkdir()
            shutil.copy2(participant / "history" / "deals.csv", history / "deals.csv")
            shutil.copy2(
                participant / "history" / "monthly_deposits.csv",
                history / "monthly_deposits.csv",
            )

            first = "Pecan Grove Catering"
            shutil.copytree(participant / "shared_folder" / first, shared / first)
            first_result = analyze(data_root)
            self.assertEqual(first_result["summary"]["currentPackets"], 1)

            second = "Ridgeway Freight Logistics"
            shutil.copytree(participant / "shared_folder" / second, shared / second)
            second_result = analyze(data_root)
            self.assertEqual(second_result["summary"]["currentPackets"], 2)

            third = "Summit Peak Flooring"
            shutil.copytree(participant / "shared_folder" / third, shared / third)
            third_result = analyze(data_root)
            self.assertEqual(third_result["summary"]["currentPackets"], 3)
            self.assertGreater(
                third_result["summary"]["crossDealSignals"],
                first_result["summary"]["crossDealSignals"],
            )


if __name__ == "__main__":
    unittest.main()
