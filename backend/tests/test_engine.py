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
    _transaction_signals,
    _csv_transactions,
    _document_review_signals,
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
            {
                "id": f"signal-{index}",
                "family": family,
                "borrowers": ["Borrower A"],
                "title": f"Signal {index}",
                "evidence": [],
            }
            for index, family in enumerate(
                ("payment_relationship", "payment_relationship", "document_identifier")
            )
        ]
        score = _risk_scores(signals, packets)["borrower a"]
        self.assertEqual(score["families"], ["document_identifier", "payment_relationship"])
        self.assertEqual(score["score"], 6)
        self.assertEqual(
            sum(item["points"] for item in score["scoreBreakdown"]),
            score["score"],
        )
        self.assertEqual(
            [item["points"] for item in score["scoreBreakdown"]],
            [3, 3],
        )

    def test_family_priority_increases_with_corroborating_evidence(self) -> None:
        packets = [
            type("Packet", (), {"name": name})()
            for name in ("Borrower A", "Borrower B", "Borrower C")
        ]
        weak = {
            "id": "weak",
            "family": "payment_relationship",
            "borrowers": ["Borrower A"],
            "severity": "Moderate",
            "title": "Limited evidence",
            "evidence": [{
                "kind": "payment_record",
                "source": "quote.pdf:page 1",
                "text": "One observation",
            }],
        }
        strong = {
            "id": "strong",
            "family": "payment_relationship",
            "borrowers": ["Borrower A", "Borrower B", "Borrower C"],
            "severity": "High",
            "title": "Corroborated evidence",
            "evidence": [
                {
                    "kind": "payment_record",
                    "source": f"quote-{index}.pdf:page 1",
                    "text": "Supporting observation",
                }
                for index in range(4)
            ] + [{
                "kind": "historical_account",
                "source": "deals.csv:line 4",
                "text": "Historical corroboration",
            }],
        }
        weak_score = _risk_scores([weak], packets)["borrower a"]["scoreBreakdown"][0]
        strong_score = _risk_scores([strong], packets)["borrower a"]["scoreBreakdown"][0]
        self.assertEqual(weak_score["points"], 3)
        self.assertEqual(weak_score["urgency"], "Low")
        self.assertEqual(strong_score["points"], 25)
        self.assertEqual(strong_score["urgency"], "Urgent")
        self.assertGreater(strong_score["points"], weak_score["points"])
        self.assertIn("5 distinct source documents +8", strong_score["rationale"])
        self.assertIn("2 corroborating evidence types +2", strong_score["rationale"])
        self.assertIn("3 connected applicants +3", strong_score["rationale"])

    def test_sequential_document_numbers_do_not_score_without_corroboration(self) -> None:
        packets = [type("Packet", (), {"name": "Borrower A"})()]
        score = _risk_scores(
            [{"family": "document_identifier", "borrowers": ["Borrower A"]}],
            packets,
        )["borrower a"]
        self.assertEqual(score["score"], 0)
        self.assertEqual(score["families"], [])

    def test_transaction_detector_generalizes_to_new_references_and_accounts(self) -> None:
        packets = []
        for index, name in enumerate(("Business One", "Business Two", "Business Three")):
            packet = Packet(name, name, [], name)
            packet.transactions = [
                {
                    "date": f"2026-07-0{reference + 1}",
                    "description": f"ACH CREDIT BILL-{reference + 3}A-{index}",
                    "amount": float(4000 + reference * 125 + index * 75),
                    "direction": "credit",
                    "reference": f"BILL-{reference + 3}A",
                    "destinationLast4": "9284",
                    "wholeDollar": True,
                    "source": f"{name}/statement.pdf:page 1",
                }
                for reference in range(3)
            ]
            packets.append(packet)

        signals, behavior = _transaction_signals(
            packets,
            minimum_shared_references=3,
            minimum_destination_transfers=3,
            minimum_round_amount_transactions=3,
            round_amount_threshold=0.9,
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["transactionSummary"]["sharedReferenceCount"], 3)
        self.assertEqual(signals[0]["transactionSummary"]["sharedDestinations"], ["9284"])
        self.assertEqual(len(signals[0]["borrowers"]), 3)
        self.assertTrue(signals[0]["scoreContribution"])
        self.assertEqual(len(behavior), 3)
        self.assertTrue(all(not signal["scoreContribution"] for signal in behavior))

    def test_csv_statement_parser_skips_bank_export_preamble(self) -> None:
        folder = ROOT / "participant" / "participant" / "shared_folder" / "tyler loopwise loan stuff"
        transactions = _csv_transactions(folder, ROOT)
        self.assertGreater(len(transactions), 0)
        self.assertTrue(all(transaction["source"].endswith(".csv") for transaction in transactions))
        self.assertTrue(all(transaction["date"].startswith("2026-") for transaction in transactions))

    def test_document_issues_are_reported_without_affecting_fraud_score(self) -> None:
        root = Path.cwd()
        pages = [
            Page(
                root / "application.pdf",
                1,
                "Federal EIN\n12-3456789\nAmount requested\n$100,000.00\n"
                "Page 1 of 1",
            ),
            Page(
                root / "request.pdf",
                1,
                "We are requesting $80,000.00.\n"
                "Employer Identification Number\n12-3456789\nPage 1 of 2",
            ),
        ]
        packet = Packet("Example LLC", "Example LLC", pages, "example")
        findings = _document_review_signals([packet], root)
        self.assertEqual(
            {finding["family"] for finding in findings},
            {"document_reconciliation", "document_completeness"},
        )
        self.assertTrue(all(not finding["scoreContribution"] for finding in findings))
        self.assertTrue(any("100,000.00" in item["text"] for item in findings[0]["evidence"]))

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
        self.assertEqual(len(result["scores"]), 13)
        self.assertEqual(len({score["folder"] for score in result["scores"]}), 13)
        lone_star = next(
            score for score in result["scores"]
            if score["borrower"] == "Lone Star Family Dental PLLC"
        )
        self.assertEqual(lone_star["folder"], "LSFD_docs")
        self.assertTrue(all(
            score["score"] == sum(item["points"] for item in score["scoreBreakdown"])
            and all(1 <= item["points"] <= 25 for item in score["scoreBreakdown"])
            for score in result["scores"]
        ))
        self.assertEqual(result["mlModel"]["algorithm"], "Isolation Forest")
        self.assertEqual(result["mlModel"]["trainingRows"], 245)
        self.assertEqual(result["mlModel"]["excludedKnownRuleRows"], 12)
        self.assertEqual(result["mlModel"]["scoredApplications"], 13)
        self.assertIn("not a fraud probability", result["mlModel"]["limitations"])
        self.assertTrue(all("mlAnomaly" in score for score in result["scores"]))
        ml_findings = [
            signal for signal in signals if signal["family"] == "ml_anomaly"
        ]
        self.assertEqual(
            result["mlModel"]["outlierApplications"],
            len(ml_findings),
        )
        self.assertTrue(all(
            signal["scoreContribution"] is False
            and signal["mlAnomaly"]["isOutlier"]
            and signal["evidence"][0]["source"]
            for signal in ml_findings
        ))

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
        structure_signals = [
            signal for signal in signals
            if signal["family"] == "financial_structure_similarity"
        ]
        self.assertTrue(structure_signals)
        self.assertTrue(all(
            not signal["scoreContribution"]
            and signal["classification"] == "shared_relationship_observation"
            for signal in structure_signals
        ))
        expected_scores = {
            "Ridgeway Freight Logistics LLC": 74,
            "Pecan Grove Catering Co LLC": 74,
            "Summit Peak Flooring LLC": 58,
        }
        for borrower, expected_score in expected_scores.items():
            borrower_score = next(
                score for score in result["scores"]
                if score["borrower"] == borrower
            )
            self.assertEqual(borrower_score["score"], expected_score)
            self.assertEqual(
                sum(item["points"] for item in borrower_score["scoreBreakdown"]),
                expected_score,
            )
            self.assertEqual(
                len(borrower_score["scoreBreakdown"]),
                len(borrower_score["families"]),
            )
            self.assertTrue(all(
                1 <= item["points"] <= 25
                and item["urgency"] in {"Low", "Moderate", "Elevated", "High", "Urgent"}
                and item["rationale"]
                for item in borrower_score["scoreBreakdown"]
            ))
            self.assertEqual(
                {item["family"] for item in borrower_score["scoreBreakdown"]},
                set(borrower_score["families"]),
            )
            self.assertNotIn("deposit_behavior", borrower_score["families"])

        ridgeway_deposit = next(
            signal for signal in signals
            if signal["family"] == "deposit_behavior"
            and "Ridgeway Freight Logistics LLC" in signal["borrowers"]
        )
        self.assertEqual(ridgeway_deposit["depositBehavior"]["industryCode"], "484110")
        self.assertEqual(ridgeway_deposit["depositBehavior"]["industryPeerCount"], 8)
        self.assertEqual(ridgeway_deposit["depositBehavior"]["comparisonDealCount"], 8)
        self.assertEqual(ridgeway_deposit["depositBehavior"]["comparisonScope"], "industry")
        self.assertEqual(ridgeway_deposit["depositBehavior"]["dealLevelMean"], 1.0)
        self.assertFalse(
            ridgeway_deposit["depositBehavior"]["historicalChannelComparisonAvailable"]
        )
        monthly_evidence = [
            item for item in ridgeway_deposit["evidence"]
            if item["kind"] == "monthly_deposit_transactions"
        ]
        self.assertEqual(len(monthly_evidence), 6)
        self.assertEqual(
            {item["source"].split("/")[-1].split(":")[0] for item in monthly_evidence},
            {f"Bank_Statement_2026-{month:02d}.pdf" for month in range(5, 11)},
        )
        self.assertTrue(all(":page 1" in item["source"] for item in monthly_evidence))
        self.assertTrue(all("Transactions:" in item["text"] for item in monthly_evidence))
        self.assertFalse(ridgeway_deposit["scoreContribution"])
        self.assertEqual(ridgeway_deposit["riskDimension"], "behavioral_anomaly")

        preparer = next(signal for signal in signals if signal["family"] == "preparer_network")
        self.assertGreaterEqual(len(preparer["borrowers"]), 2)
        self.assertTrue(all(item["source"] for item in preparer["evidence"]))
        self.assertTrue(
            any(node["kind"] == "preparer_network" for node in result["graph"]["nodes"])
        )
        transaction = next(
            signal for signal in result["signals"]
            if signal["family"] == "transaction_relationship"
        )
        self.assertEqual(transaction["transactionSummary"]["sharedReferenceCount"], 34)
        self.assertEqual(transaction["transactionSummary"]["sharedDestinations"], ["8810"])
        self.assertEqual(transaction["transactionSummary"]["matchedPostingDates"], 34)
        self.assertTrue(any(
            "18 transfers" in item["text"] and "$186,600.00" in item["text"]
            for item in transaction["evidence"]
        ))
        for borrower in (
            "Ridgeway Freight Logistics LLC",
            "Pecan Grove Catering Co LLC",
            "Summit Peak Flooring LLC",
        ):
            behavior = next(
                signal for signal in result["signals"]
                if signal["family"] == "whole_dollar_transactions"
                and borrower in signal["borrowers"]
            )
            self.assertIn("123 of 123", behavior["evidence"][0]["text"])
        document_findings = [
            signal for signal in result["signals"]
            if signal["classification"] == "document_review_issue"
        ]
        self.assertTrue(any("74-3169524" in " ".join(
            evidence["text"] for evidence in signal["evidence"]
        ) and "74-3196524" in " ".join(
            evidence["text"] for evidence in signal["evidence"]
        ) for signal in document_findings))
        self.assertTrue(any("205,000.00" in " ".join(
            evidence["text"] for evidence in signal["evidence"]
        ) for signal in document_findings))
        self.assertTrue(any("page 1 of 2" in " ".join(
            evidence["text"] for evidence in signal["evidence"]
        ).casefold() for signal in document_findings))
        self.assertTrue(any(investigation["borrowers"] and
                            len(investigation["borrowers"]) >= 3 and
                            any("Ridgeway Freight" in name for name in investigation["borrowers"])
                            for investigation in result["investigations"]))
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
