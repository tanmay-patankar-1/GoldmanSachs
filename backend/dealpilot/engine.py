from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

from pypdf import PdfReader


LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "llp",
    "ltd",
    "pllc",
}
FINANCIAL_METRICS = (
    "revenue",
    "cost of goods sold",
    "gross profit",
    "payroll",
    "rent & occupancy",
    "other operating",
    "net income",
)
EXPENSE_METRICS = (
    "cost of goods sold",
    "payroll",
    "rent & occupancy",
    "other operating",
)
AMOUNT_RE = re.compile(r"-?\$[\d,]+(?:\.\d{2})?")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
QUOTE_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,5})-?(\d{3,})\b")


@dataclass
class Page:
    path: Path
    number: int
    text: str


@dataclass
class Packet:
    folder: str
    name: str
    pages: list[Page]
    source_name: str
    quotes: list[dict[str, Any]] = field(default_factory=list)
    financials: dict[str, float] = field(default_factory=dict)
    preparer: str | None = None


def normalize_supplier(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", name.casefold())
    return " ".join(token for token in tokens if token not in LEGAL_SUFFIXES)


def _relative_source(path: Path, root: Path, page: int | None = None) -> str:
    source = path.resolve().relative_to(root.resolve()).as_posix()
    return f"{source}:page {page}" if page is not None else source


def _read_pages(path: Path) -> list[Page]:
    try:
        reader = PdfReader(str(path))
        return [
            Page(path, number, page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]
    except Exception as exc:
        raise RuntimeError(f"Unable to read PDF {path}: {exc}") from exc


def _money_value(value: str) -> float:
    return float(value.replace("$", "").replace(",", ""))


def _financial_signature(pages: list[Page]) -> dict[str, float]:
    for page in pages:
        text = page.text
        if not re.search(r"\bFY\s*2025\b", text, re.I):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        signature: dict[str, float] = {}
        for index, line in enumerate(lines):
            label = re.sub(r"\s+", " ", line).casefold()
            if label not in FINANCIAL_METRICS:
                continue
            for candidate in lines[index + 1 : index + 4]:
                match = AMOUNT_RE.fullmatch(candidate)
                if match:
                    signature[label] = _money_value(match.group())
                    break
        return signature
    return {}


def _packet_name(folder: str, pages: list[Page]) -> str:
    for page in pages:
        text = page.text
        customer = re.search(r"\bCustomer\s*\n\s*([^\n]+)", text, re.I)
        if customer:
            return customer.group(1).strip()
        statement_name = re.search(
            r"([^\n]+?)\s*(?:\ufffd|[-–—])?\s*Financial Statements",
            text,
            re.I,
        )
        if statement_name:
            candidate = statement_name.group(1).strip()
            if candidate and "bookkeeping" not in candidate.casefold():
                return candidate
    return folder


def _quote_records(pages: list[Page], root: Path) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for page in pages:
        if not re.search(r"\b(?:quote|quotation|invoice)\b", page.text, re.I):
            continue
        id_match = QUOTE_ID_RE.search(page.text)
        if not id_match:
            continue
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        customer_index = next(
            (index for index, line in enumerate(lines) if line.casefold() == "customer"),
            None,
        )
        supplier = ""
        if customer_index is not None and customer_index > 0:
            supplier = lines[1] if len(lines) > 1 else ""
        else:
            remit_index = next(
                (index for index, line in enumerate(lines) if "remit to" in line.casefold()),
                None,
            )
            if remit_index is not None and remit_index > 1:
                supplier = lines[1]
        supplier = re.split(r"\s*[·�–—]\s*", supplier, maxsplit=1)[0].strip()

        account_match = re.search(
            r"(?:account|acct)(?:\s*(?:ending|last\s*4|#|no\.?))?[\s:#-]*(\d{4})\b",
            page.text,
            re.I,
        )
        bank_match = re.search(r"Remit to:\s*([^�\n]+)", page.text, re.I)
        date_match = re.search(
            r"(?:quote|invoice)\s*date\s*\n\s*(\d{4}-\d{2}-\d{2})",
            page.text,
            re.I,
        )
        quotes.append(
            {
                "id": id_match.group(0),
                "prefix": id_match.group(1),
                "number": int(id_match.group(2)),
                "supplier": supplier,
                "supplierKey": normalize_supplier(supplier),
                "accountLast4": account_match.group(1) if account_match else None,
                "bank": bank_match.group(1).strip() if bank_match else None,
                "date": date_match.group(1) if date_match else None,
                "source": _relative_source(page.path, root, page.number),
                "borrower": "",
            }
        )
    return quotes


def _preparer(pages: list[Page]) -> tuple[str | None, Page | None]:
    for page in pages:
        match = re.search(r"Prepared by:\s*([^\n]+)", page.text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(), page
    return None, None


def _preparer_details(pages: list[Page], root: Path) -> dict[str, str] | None:
    for page in pages:
        match = re.search(r"Prepared by:\s*([^\n]+)", page.text, re.I)
        if not match:
            continue
        line = match.group(1).strip()
        details = re.split(r"\s*[�|]\s*", line, maxsplit=1)
        identity = [part.strip() for part in details[0].split(",") if part.strip()]
        name = identity[0] if identity else ""
        if not name or name.casefold() in {"self-prepared", "self prepared"}:
            continue
        firm = next(
            (
                part for part in identity[1:]
                if part.casefold() not in {"cpa", "ea", "owner"}
            ),
            "",
        )
        phone_match = re.search(
            r"(?<!\d)(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)",
            line,
        )
        email_match = re.search(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            line,
            re.I,
        )
        return {
            "name": name,
            "firm": firm,
            "phone": "".join(phone_match.groups()) if phone_match else "",
            "email": email_match.group().casefold() if email_match else "",
            "source": _relative_source(page.path, root, page.number),
        }
    return None


def _read_packets(shared_folder: Path, root: Path) -> list[Packet]:
    packets: list[Packet] = []
    if not shared_folder.is_dir():
        return packets
    for folder in sorted(path for path in shared_folder.iterdir() if path.is_dir()):
        pages = [
            page
            for path in sorted(folder.rglob("*.pdf"))
            for page in _read_pages(path)
        ]
        if not pages:
            continue
        name = _packet_name(folder.name, pages)
        packet = Packet(folder.name, name, pages, _relative_source(folder, root))
        packet.quotes = _quote_records(pages, root)
        for quote in packet.quotes:
            quote["borrower"] = name
        packet.financials = _financial_signature(pages)
        packet.preparer, _ = _preparer(pages)
        packets.append(packet)
    return packets


def detect_sequential_identifiers(
    quotes: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for quote in quotes:
        if quote.get("prefix") and quote.get("supplierKey") is not None:
            groups[(quote.get("supplierKey", ""), quote["prefix"])].append(quote)

    sequences: list[list[dict[str, Any]]] = []
    for records in groups.values():
        by_number: dict[int, dict[str, Any]] = {}
        for record in records:
            by_number.setdefault(record["number"], record)
        ordered = [by_number[number] for number in sorted(by_number)]
        for start in range(len(ordered)):
            run = [ordered[start]]
            for candidate in ordered[start + 1 :]:
                if candidate["number"] != run[-1]["number"] + 1:
                    break
                run.append(candidate)
            if len(run) >= 3 and len({row["borrower"] for row in run}) >= 3:
                sequences.append(run)
                break
    return sequences


def _financial_similarity(
    left: dict[str, float], right: dict[str, float]
) -> tuple[str, list[str]]:
    shared = sorted(set(left) & set(right))
    if len(shared) < 5:
        return "", shared
    deltas = [
        abs(left[key] - right[key]) / max(abs(left[key]), abs(right[key]), 1.0)
        for key in shared
    ]
    if all(delta == 0 for delta in deltas):
        return "exact", shared
    if sum(delta <= 0.01 for delta in deltas) / len(deltas) >= 0.8 and max(deltas) <= 0.02:
        return "near", shared
    return "", shared


def _deposit_amounts(pages: list[Page]) -> dict[str, list[float]]:
    amounts: dict[str, list[float]] = defaultdict(list)
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines()]
        for index, line in enumerate(lines):
            if not DATE_RE.fullmatch(line):
                continue
            description = next(
                (candidate for candidate in lines[index + 1 : index + 3] if candidate),
                "",
            )
            if not re.search(r"\b(deposit|credit|transfer in|wire in)\b", description, re.I):
                continue
            month = line[6:10] + "-" + line[0:2]
            for candidate in lines[index + 2 : index + 6]:
                match = AMOUNT_RE.fullmatch(candidate)
                if match:
                    amounts[month].append(_money_value(match.group()))
                    break
    return amounts


def _historical_data(history_dir: Path, root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    deals_path = history_dir / "deals.csv"
    if not deals_path.is_file():
        raise FileNotFoundError(f"Historical deals file not found: {deals_path}")
    with deals_path.open(encoding="utf-8-sig", newline="") as stream:
        deals = list(csv.DictReader(stream))
    line_by_deal = {row["deal_id"]: index + 2 for index, row in enumerate(deals)}

    deposit_path = history_dir / "monthly_deposits.csv"
    monthly: dict[str, list[float]] = defaultdict(list)
    if deposit_path.is_file():
        with deposit_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                try:
                    monthly[row["deal_id"]].append(
                        float(row["pct_deposits_multiple_of_100"])
                    )
                except (KeyError, ValueError):
                    continue
    historical_behavior = [
        {
            "deal_id": deal_id,
            "meanRoundDepositPct": sum(values) / len(values),
        }
        for deal_id, values in monthly.items()
        if values
    ]
    labels: dict[str, int] = defaultdict(int)
    for row in deals:
        label = row.get("fraud_label", "none") or "none"
        if label.casefold() != "none":
            labels[label] += 1
    baseline = {
        "historicalDeals": len(deals),
        "knownFraudLabels": dict(sorted(labels.items())),
        "knownFraudLabelCount": sum(labels.values()),
        "depositBehaviorDeals": len(historical_behavior),
        "behaviorNote": (
            "Deposit round-number rates are available for historical deals; "
            "they are descriptive context and are not used as fraud evidence."
        ),
        "labelNote": (
            "Historical fraud_label values describe only the bank's existing "
            "controls and are excluded from cross-deal features and scores."
        ),
    }

    records: list[dict[str, Any]] = []
    for row in deals:
        supplier = row.get("equipment_vendor", "").strip()
        account = row.get("vendor_remit_acct_last4", "").strip()
        if not supplier or not account:
            continue
        records.append(
            {
                "borrower": row.get("legal_name", ""),
                "borrowerKey": normalize_supplier(row.get("legal_name", "")),
                "supplier": supplier,
                "supplierKey": normalize_supplier(supplier),
                "accountLast4": account,
                "dealId": row.get("deal_id", ""),
                "date": row.get("application_date", ""),
                "status": row.get("final_status", ""),
                "repaymentStatus": row.get("repayment_status", ""),
                "fraudLabel": row.get("fraud_label", "none"),
                "source": (
                    f"{_relative_source(deals_path, root)}:"
                    f"line {line_by_deal[row['deal_id']]}"
                ),
                "current": False,
            }
        )
    return deals, baseline, records


def _historical_preparers(
    deals: list[dict[str, Any]], history_dir: Path, root: Path
) -> list[dict[str, Any]]:
    source_path = history_dir / "deals.csv"
    records: list[dict[str, Any]] = []
    for line, row in enumerate(deals, start=2):
        name = row.get("preparer_name", "").strip()
        phone = re.sub(r"\D", "", row.get("preparer_phone", ""))
        if not name or not phone:
            continue
        records.append(
            {
                "borrower": row.get("legal_name", ""),
                "borrowerKey": normalize_supplier(row.get("legal_name", "")),
                "name": name.split(",", maxsplit=1)[0].strip(),
                "firm": row.get("preparer_firm", "").strip(),
                "phone": phone[-10:],
                "email": row.get("preparer_email", "").strip().casefold(),
                "dealId": row.get("deal_id", ""),
                "date": row.get("application_date", ""),
                "status": row.get("final_status", ""),
                "repaymentStatus": row.get("repayment_status", ""),
                "source": f"{_relative_source(source_path, root)}:line {line}",
                "current": False,
            }
        )
    return records


def _expense_proportions(financials: dict[str, float]) -> dict[str, float]:
    revenue = financials.get("revenue", 0)
    if revenue <= 0:
        return {}
    return {
        metric: financials[metric] / revenue
        for metric in EXPENSE_METRICS
        if metric in financials
    }


def _similar_expense_structure(
    left: dict[str, float], right: dict[str, float]
) -> tuple[bool, list[str]]:
    left_ratios = _expense_proportions(left)
    right_ratios = _expense_proportions(right)
    shared = sorted(set(left_ratios) & set(right_ratios))
    matches = [
        metric for metric in shared
        if abs(left_ratios[metric] - right_ratios[metric]) <= 0.02
    ]
    return len(matches) >= 3, matches


def _evidence(text: str, source: str, kind: str) -> dict[str, str]:
    return {"text": text, "source": source, "kind": kind}


def _historical_deals(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "id": row["dealId"],
            "borrower": row["borrower"],
            "date": row["date"],
            "status": row["status"],
            "repaymentStatus": row["repaymentStatus"],
            "source": row["source"],
        }
        for row in sorted(records, key=lambda item: item.get("date", ""), reverse=True)
        if not row.get("current")
    ]


def _make_signal(
    signal_id: str,
    family: str,
    title: str,
    description: str,
    severity: str,
    records: list[dict[str, Any]],
    evidence: list[dict[str, str]],
    actions: list[str],
) -> dict[str, Any]:
    current_names = sorted(
        {record["borrower"] for record in records if record.get("current")}
    )
    history = _historical_deals(records)
    return {
        "id": signal_id,
        "family": family,
        "scoreFamily": family,
        "title": title,
        "classification": "new_cross_deal_lead",
        "severity": severity,
        "description": description,
        "borrowers": current_names,
        "historicalDeals": history,
        "evidence": evidence,
        "recommendedActions": actions,
        "scoreContribution": True,
    }


def _payment_signals(
    packets: list[Packet],
    historical_payments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_payments: list[dict[str, Any]] = []
    for packet in packets:
        for quote in packet.quotes:
            if not quote.get("accountLast4") or not quote.get("supplierKey"):
                continue
            current_payments.append(
                {
                    "borrower": packet.name,
                    "borrowerKey": normalize_supplier(packet.name),
                    "supplier": quote["supplier"],
                    "supplierKey": quote["supplierKey"],
                    "accountLast4": quote["accountLast4"],
                    "dealId": None,
                    "date": quote["date"] or "",
                    "status": "current_application",
                    "repaymentStatus": "",
                    "fraudLabel": "none",
                    "source": quote["source"],
                    "current": True,
                }
            )
    signals: list[dict[str, Any]] = []
    history_by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in historical_payments:
        history_by_supplier[record["supplierKey"]].append(record)

    current_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in current_payments:
        current_by_account[record["accountLast4"]].append(record)
        current_by_supplier[record["supplierKey"]].append(record)

    def account_change_for(current: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
        current_keys = {record["supplierKey"] for record in current}
        history = [
            record for key in current_keys for record in history_by_supplier.get(key, [])
        ]
        by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in history:
            by_account[record["accountLast4"]].append(record)
        if not by_account:
            return False, [], history
        preferred = max(by_account, key=lambda account: len(by_account[account]))
        preferred_count = len(by_account[preferred])
        history_count = len(history)
        established = history_count >= 3 and preferred_count / history_count >= 0.7
        changed = established and any(
            record["accountLast4"] != preferred for record in current
        )
        return changed, by_account.get(preferred, []), history

    emitted_suppliers: set[str] = set()
    for account, current in current_by_account.items():
        borrowers = {record["borrowerKey"] for record in current}
        if len(borrowers) < 2:
            continue
        account_change, preferred_history, matching_history = account_change_for(current)
        related_history = [
            record for record in historical_payments
            if record["accountLast4"] == account
            or record["supplierKey"] in {item["supplierKey"] for item in current}
        ]
        records = current + related_history
        names = ", ".join(sorted({record["borrower"] for record in current}))
        evidence = [
            _evidence(
                f"Account ending {account} appears in current documents for "
                f"{len(borrowers)} distinct borrowers: {names}.",
                current[0]["source"],
                "current_payment_destination",
            )
        ]
        for record in current[1:]:
            evidence.append(
                _evidence(
                    f"{record['borrower']} lists account ending {account} for "
                    f"{record['supplier']}.",
                    record["source"],
                    "current_payment_destination",
                )
            )
        if account_change and preferred_history:
            evidence.append(
                _evidence(
                    f"Historical records show account ending "
                    f"{preferred_history[0]['accountLast4']} in "
                    f"{len(preferred_history)} of {len(matching_history)} deals for a "
                    "similarly named supplier; the current destination differs.",
                    preferred_history[0]["source"],
                    "historical_account_comparison",
                )
            )
        evidence.append(
            _evidence(
                "Name normalization removes legal suffixes for candidate linking only; "
                "it does not establish that suppliers are the same legal entity.",
                current[0]["source"],
                "normalization_caveat",
            )
        )
        signal = _make_signal(
            f"payment-{len(signals) + 1}",
            "payment_relationship",
            (
                "Shared payment destination and historical account change"
                if account_change
                else "Shared supplier payment destination"
            ),
            (
                "Multiple applications share a destination that differs from a well-supported "
                "historical supplier account. Confirm the beneficiary independently before payment."
                if account_change
                else "Multiple current applications reference the same payment destination. "
                "This can be legitimate; sharing alone is an observation, not evidence of fraud."
            ),
            "Elevated" if account_change else "Moderate",
            records,
            evidence,
            [
                "Confirm the supplier legal entity using independently sourced contact details.",
                "Verify the receiving account with the supplier using a known-good phone number.",
                "Compare the quote, purchase order, and payment instructions across linked applications.",
            ],
        )
        signal["supplierKeys"] = sorted({record["supplierKey"] for record in current})
        signal["scoreFamily"] = "supplier_relationship"
        signal["scoreContribution"] = account_change
        if not account_change:
            signal["classification"] = "shared_relationship_observation"
        signals.append(signal)
        emitted_suppliers.update(signal["supplierKeys"])

    for supplier_key, current in current_by_supplier.items():
        if supplier_key in emitted_suppliers:
            continue
        account_change, preferred_history, matching_history = account_change_for(current)
        if not account_change or not preferred_history:
            continue
        preferred_account = preferred_history[0]["accountLast4"]
        records = current + matching_history
        evidence = [
            _evidence(
                f"{record['borrower']} currently lists account ending "
                f"{record['accountLast4']} for {record['supplier']}.",
                record["source"],
                "current_payment_destination",
            )
            for record in current
        ]
        evidence.append(
            _evidence(
                f"Historical records show account ending {preferred_account} in "
                f"{len(preferred_history)} of {len(matching_history)} deals for this "
                "normalized supplier candidate.",
                preferred_history[0]["source"],
                "historical_account_comparison",
            )
        )
        signal = _make_signal(
            f"payment-{len(signals) + 1}",
            "payment_relationship",
            "Supplier payment account differs from established history",
            "The current destination differs from the supplier candidate's predominant "
            "historical destination. A change can be legitimate; independently verify the "
            "supplier and beneficiary.",
            "Elevated",
            records,
            evidence,
            [
                "Verify the account change using supplier contact details already on file.",
                "Confirm the legal entity and beneficiary before disbursement.",
            ],
        )
        signal["supplierKeys"] = [supplier_key]
        signal["scoreFamily"] = "supplier_relationship"
        signals.append(signal)
    return signals, current_payments


def _supplier_name_variation_signals(
    current_payments: list[dict[str, Any]],
    historical_payments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    history_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in current_payments:
        current_by_key[record["supplierKey"]].append(record)
    for record in historical_payments:
        history_by_key[record["supplierKey"]].append(record)

    signals: list[dict[str, Any]] = []
    for supplier_key, current in current_by_key.items():
        related = current + history_by_key.get(supplier_key, [])
        names: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in related:
            names[record["supplier"]].append(record)
        if len(names) < 2:
            continue
        evidence = [
            _evidence(
                f"{record['borrower']} lists supplier as “{record['supplier']}”. "
                "Suffix-stripped normalization links this as a candidate only.",
                record["source"],
                "supplier_name_variant",
            )
            for record in current
        ]
        evidence.extend(
            _evidence(
                f"Historical records list supplier as “{name}”; this is a name candidate, "
                "not proof of the same legal entity.",
                records[0]["source"],
                "historical_supplier_name_variant",
            )
            for name, records in sorted(names.items())
            if not any(record["current"] and record["supplier"] == name for record in records)
        )
        signal = _make_signal(
            f"supplier-name-variation-{len(signals) + 1}",
            "supplier_name_variation",
            "Supplier-name variation requires identity verification",
            "Supplier names become similar after removing legal suffixes. This is a candidate "
            "relationship only; confirm legal identity and payment authorization independently.",
            "Moderate",
            related,
            evidence,
            [
                "Confirm each supplier's legal name and tax identity from independent records.",
                "Verify whether the payment destination belongs to the same legal entity.",
            ],
        )
        signal["entityKey"] = supplier_key
        signal["scoreFamily"] = "supplier_relationship"
        signal["scoreContribution"] = False
        signal["classification"] = "shared_relationship_observation"
        signals.append(signal)
    return signals


def _supplier_concentration_signals(
    current_payments: list[dict[str, Any]],
    historical_payments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    historical_counts: dict[str, set[str]] = defaultdict(set)
    for record in historical_payments:
        if record.get("dealId"):
            historical_counts[record["supplierKey"]].add(record["dealId"])
    distribution = sorted(len(deals) for deals in historical_counts.values())
    percentile = (
        distribution[min(len(distribution) - 1, int(0.95 * (len(distribution) - 1)))]
        if distribution else 2
    )
    threshold = max(3, percentile)
    current_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in current_payments:
        current_by_key[record["supplierKey"]].append(record)

    signals: list[dict[str, Any]] = []
    for supplier_key, records in current_by_key.items():
        borrowers = {record["borrowerKey"] for record in records}
        if len(borrowers) < threshold:
            continue
        evidence = [
            _evidence(
                f"{record['borrower']} lists {record['supplier']} as an equipment supplier.",
                record["source"],
                "supplier_concentration",
            )
            for record in records
        ]
        evidence.append(
            _evidence(
                f"{len(borrowers)} current borrowers share this supplier candidate; "
                f"the historical 95th-percentile supplier count is {percentile} distinct deal(s).",
                records[0]["source"],
                "historical_supplier_concentration",
            )
        )
        related_history = [
            record for record in historical_payments
            if record["supplierKey"] == supplier_key
        ]
        signal = _make_signal(
            f"supplier-concentration-{len(signals) + 1}",
            "supplier_concentration",
            "Supplier concentration exceeds the historical reference",
            "A high share of current applications references this supplier candidate. "
            "Concentration can reflect ordinary vendor activity and should be verified, "
            "not treated as proof of coordination.",
            "Moderate",
            records + related_history,
            evidence,
            [
                "Confirm each borrower independently selected the supplier.",
                "Check for shared ownership, common referral channels, or coordinated purchasing.",
            ],
        )
        signal["entityKey"] = supplier_key
        signal["scoreFamily"] = "supplier_relationship"
        signals.append(signal)
    return signals


def _preparer_signals(
    packets: list[Packet],
    historical_preparers: list[dict[str, Any]],
    root: Path,
) -> list[dict[str, Any]]:
    current: list[dict[str, Any]] = []
    for packet in packets:
        details = _preparer_details(packet.pages, root)
        if not details or not details["phone"]:
            continue
        current.append(
            {
                **details,
                "borrower": packet.name,
                "borrowerKey": normalize_supplier(packet.name),
                "dealId": None,
                "current": True,
            }
        )
    by_contact: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in historical_preparers + current:
        phone = re.sub(r"\D", "", record.get("phone", ""))[-10:]
        name_key = normalize_supplier(record.get("name", ""))
        if phone and name_key:
            by_contact[(phone, name_key)].append(record)

    signals: list[dict[str, Any]] = []
    for (phone, _), records in by_contact.items():
        borrowers = {record["borrowerKey"] for record in records}
        current_borrowers = {
            record["borrowerKey"] for record in records if record["current"]
        }
        if len(borrowers) < 3 or len(current_borrowers) < 3:
            continue
        evidence = [
            _evidence(
                f"{record['borrower']} lists preparer {record['name']}"
                + (f" at {record['firm']}" if record.get("firm") else "")
                + f" with contact ending {phone[-4:]}.",
                record["source"],
                "preparer_contact",
            )
            for record in records
        ]
        signal = _make_signal(
            f"preparer-network-{len(signals) + 1}",
            "preparer_network",
            "Shared financial preparer and contact across borrowers",
            f"The same preparer identity and phone contact appear across "
            f"{len(borrowers)} distinct businesses, including {len(current_borrowers)} "
            "current applications. Shared accountants can be legitimate; verify the "
            "engagement and source of each statement.",
            "Moderate",
            records,
            evidence,
            [
                "Confirm the preparer independently and verify each engagement.",
                "Obtain statements directly from the preparer using an independently verified contact.",
            ],
        )
        signal["entityKey"] = f"{phone}:{normalize_supplier(records[0]['name'])}"
        signal["scoreFamily"] = "preparer_network"
        signal["scoreContribution"] = False
        signal["classification"] = "shared_relationship_observation"
        signals.append(signal)
    return signals


def _financial_structure_signals(
    packets: list[Packet], root: Path
) -> list[dict[str, Any]]:
    eligible = [
        packet for packet in packets
        if len(_expense_proportions(packet.financials)) >= 3
    ]
    ordered = sorted(eligible, key=lambda packet: normalize_supplier(packet.name))
    groups: set[frozenset[str]] = set()
    packets_by_key = {normalize_supplier(packet.name): packet for packet in ordered}
    for seed_index, seed in enumerate(ordered):
        group = [seed]
        for candidate in ordered[seed_index + 1 :]:
            if all(
                _similar_expense_structure(member.financials, candidate.financials)[0]
                for member in group
            ):
                group.append(candidate)
        if len(group) >= 3:
            groups.add(frozenset(normalize_supplier(packet.name) for packet in group))

    signals: list[dict[str, Any]] = []
    for keys in sorted(groups, key=lambda group: sorted(group)):
        group = [packets_by_key[key] for key in sorted(keys)]
        records = [
            {
                "borrower": packet.name,
                "borrowerKey": normalize_supplier(packet.name),
                "current": True,
                "dealId": None,
            }
            for packet in group
        ]
        evidence: list[dict[str, str]] = []
        for packet in group:
            page = next(
                (page for page in packet.pages if _financial_signature([page])),
                packet.pages[0],
            )
            ratios = _expense_proportions(packet.financials)
            ratio_text = ", ".join(
                f"{metric} {ratios[metric]:.1%}" for metric in sorted(ratios)
            )
            evidence.append(
                _evidence(
                    f"{packet.name} FY2025 expense-to-revenue proportions: {ratio_text}.",
                    _relative_source(page.path, root, page.number),
                    "financial_structure",
                )
            )
        evidence.append(
            _evidence(
                "Candidate structures require at least three expense categories within "
                "2 percentage points across at least three borrowers. This is a similarity "
                "screen, not proof of copied statements.",
                evidence[0]["source"],
                "financial_structure_method",
            )
        )
        signal = _make_signal(
            f"financial-structure-{len(signals) + 1}",
            "financial_structure_similarity",
            "Similar financial expense proportions across businesses",
            "Several unrelated borrower statements have closely matching expense-to-revenue "
            "proportions. Industry and accounting practices can create similarity; verify the "
            "underlying records.",
            "Moderate",
            records,
            evidence,
            [
                "Compare underlying ledger and source records rather than document layout.",
                "Ask the preparer to confirm each business's independent accounting data.",
            ],
        )
        signal["entityKey"] = ",".join(sorted(keys))
        signal["scoreFamily"] = "financial_similarity"
        signals.append(signal)
    return signals


def _quote_signals(packets: list[Packet]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    quotes = [quote for packet in packets for quote in packet.quotes]
    sequences = detect_sequential_identifiers(quotes)
    signals: list[dict[str, Any]] = []
    for sequence in sequences:
        records = [
            {
                "borrower": quote["borrower"],
                "borrowerKey": normalize_supplier(quote["borrower"]),
                "dealId": None,
                "current": True,
            }
            for quote in sequence
        ]
        identifiers = ", ".join(quote["id"] for quote in sequence)
        evidence = [
            _evidence(
                f"{quote['id']} appears in the {quote['borrower']} packet.",
                quote["source"],
                "document_identifier",
            )
            for quote in sequence
        ]
        evidence.append(
            _evidence(
                "Identifiers are consecutive across independent borrower packets; "
                "supplier numbering may legitimately be sequential.",
                sequence[0]["source"],
                "sequence_caveat",
            )
        )
        signals.append(
            _make_signal(
                f"quote-sequence-{len(signals) + 1}",
                "document_identifier",
                "Sequential supplier document identifiers",
                f"Quote identifiers {identifiers} occur across distinct borrowers. "
                "This can be normal supplier numbering; verify issuance and document provenance "
                "rather than treating sequence alone as fraud.",
                "Elevated",
                records,
                evidence,
                [
                    "Request supplier confirmation of each quote number and issue date.",
                    "Compare original files and metadata; do not rely on layout similarity alone.",
                ],
            )
        )
    return signals, quotes


def _financial_signals(packets: list[Packet], root: Path) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for left_index, left in enumerate(packets):
        if len(left.financials) < 5:
            continue
        for right in packets[left_index + 1 :]:
            if len(right.financials) < 5 or normalize_supplier(left.name) == normalize_supplier(right.name):
                continue
            similarity, shared = _financial_similarity(left.financials, right.financials)
            if not similarity:
                continue
            records = [
                {
                    "borrower": left.name,
                    "borrowerKey": normalize_supplier(left.name),
                    "current": True,
                    "dealId": None,
                },
                {
                    "borrower": right.name,
                    "borrowerKey": normalize_supplier(right.name),
                    "current": True,
                    "dealId": None,
                },
            ]
            left_doc = next(
                page for page in left.pages if _financial_signature([page])
            )
            right_doc = next(
                page for page in right.pages if _financial_signature([page])
            )
            comparison = "exactly match" if similarity == "exact" else "are near-duplicates"
            evidence = [
                _evidence(
                    f"{left.name} FY2025 line items: "
                        + ", ".join(
                            f"{key.capitalize()} ${left.financials[key]:,.2f}"
                            for key in shared
                        ),
                    _relative_source(left_doc.path, root, left_doc.number),
                    "financial_line_items",
                ),
                _evidence(
                    f"{right.name} FY2025 line items: "
                        + ", ".join(
                            f"{key.capitalize()} ${right.financials[key]:,.2f}"
                            for key in shared
                        ),
                    _relative_source(right_doc.path, root, right_doc.number),
                    "financial_line_items",
                ),
                _evidence(
                    "Comparison uses labeled financial values, not page layout or document template.",
                    _relative_source(left_doc.path, root, left_doc.number),
                    "template_control",
                ),
            ]
            signals.append(
                _make_signal(
                    f"financial-duplicate-{len(signals) + 1}",
                    "financial_data_similarity",
                    "Matching FY2025 financial line items",
                    f"At least five labeled FY2025 values {comparison} across unrelated "
                    "borrower packets. A shared preparer or template can explain layout similarity, "
                    "but not identical business-specific values; verify original statements.",
                    "High" if similarity == "exact" else "Elevated",
                    records,
                    evidence,
                    [
                        "Obtain statements directly from the preparer or borrower using verified contacts.",
                        "Compare source files and audit trails; confirm the values were not copied in error.",
                    ],
                )
            )
            signals[-1]["scoreFamily"] = "financial_similarity"
    return signals


def _deposit_behavior_signals(
    packets: list[Packet],
    historical_behavior: list[dict[str, Any]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history_values = sorted(
        row["meanRoundDepositPct"] for row in historical_behavior
    )
    if len(history_values) < 20:
        return [], {"available": False, "reason": "Insufficient historical reference deals."}
    percentile_index = min(len(history_values) - 1, int(0.95 * (len(history_values) - 1)))
    threshold = history_values[percentile_index]
    signals: list[dict[str, Any]] = []
    evaluated = 0
    for packet in packets:
        monthly = _deposit_amounts(packet.pages)
        usable = {month: values for month, values in monthly.items() if values}
        total = sum(len(values) for values in usable.values())
        if len(usable) < 3 or total < 10:
            continue
        evaluated += 1
        round_counts = [
            sum(
                1 for amount in values
                if abs(amount / 100 - round(amount / 100)) < 1e-9
            )
            for values in usable.values()
        ]
        monthly_rates = [
            count / len(values)
            for count, values in zip(round_counts, usable.values())
        ]
        round_count = sum(round_counts)
        observed = sum(monthly_rates) / len(monthly_rates)
        if observed <= threshold:
            continue
        source_page = next(
            page for page in packet.pages
            if _deposit_amounts([page])
        )
        record = {
            "borrower": packet.name,
            "borrowerKey": normalize_supplier(packet.name),
            "current": True,
            "dealId": None,
        }
        signals.append(
            _make_signal(
                f"deposit-behavior-{len(signals) + 1}",
                "deposit_behavior",
                "Round-number deposit behavior outside historical range",
                "The share of observed deposits divisible by $100 exceeds the 95th percentile "
                "of historical deal-level averages. This is a behavioral outlier only and is not "
                "evidence of fraud or repayment trouble.",
                "Moderate",
                [record],
                [
                    _evidence(
                        f"{round_count} of {total} deposits across {len(usable)} statement months "
                        f"({round_count / total:.1%} aggregate) are multiples of $100; mean "
                        f"monthly rate is {observed:.1%}, versus the historical 95th-percentile "
                        f"reference of {threshold:.1%}.",
                        _relative_source(source_page.path, root, source_page.number),
                        "deposit_behavior",
                    )
                ],
                [
                    "Compare deposit entries with invoices and point-of-sale records.",
                    "Review the full statement context; do not make an adverse decision from this feature.",
                ],
            )
        )
    return signals, {
        "available": True,
        "historicalDeals": len(history_values),
        "referencePercentile": 95,
        "threshold": threshold,
        "currentPacketsEvaluated": evaluated,
        "method": (
            "Current statement transactions are evaluated only with at least three months "
            "and ten qualifying deposits; compare deal-level round-deposit proportions with "
            "the empirical historical 95th percentile."
        ),
    }


def _repayment_behavior_cohorts(
    historical_behavior: list[dict[str, Any]],
) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in historical_behavior:
        status = row.get("repaymentStatus", "")
        if status:
            grouped[status].append(row["meanRoundDepositPct"])
    cohorts: dict[str, dict[str, float | int]] = {}
    for status, values in sorted(grouped.items()):
        ordered = sorted(values)
        midpoint = len(ordered) // 2
        median = (
            ordered[midpoint]
            if len(ordered) % 2
            else (ordered[midpoint - 1] + ordered[midpoint]) / 2
        )
        cohorts[status] = {
            "deals": len(values),
            "meanRoundDepositPct": round(sum(values) / len(values), 4),
            "medianRoundDepositPct": round(median, 4),
        }
    return cohorts


def _risk_scores(signals: list[dict[str, Any]], packets: list[Packet]) -> dict[str, dict[str, Any]]:
    families_by_borrower: dict[str, set[str]] = defaultdict(set)
    for signal in signals:
        if not signal.get("scoreContribution", True):
            continue
        for borrower in signal["borrowers"]:
            families_by_borrower[normalize_supplier(borrower)].add(
                signal.get("scoreFamily", signal["family"])
            )
    family_count = 4
    scores: dict[str, dict[str, Any]] = {}
    for packet in packets:
        key = normalize_supplier(packet.name)
        families = sorted(families_by_borrower.get(key, set()))
        scores[key] = {
            "borrower": packet.name,
            "score": round(100 * min(len(families), family_count) / family_count),
            "families": families,
            "method": (
                "Uncalibrated review-priority index: 25 points per distinct scoring family, "
                "capped at four. Supplier account reuse, name variation, concentration, and "
                "account-history differences count once; financial-value and expense-structure "
                "similarity count once. Shared-preparer links are informational and add no points "
                "without independent evidence. Historical fraud labels and operational delays "
                "do not contribute."
            ),
        }
    return scores


def _node(node_id: str, label: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"id": node_id, "label": label, "kind": kind, **extra}


def _supplier_entity_id(name: str) -> str:
    exact_name = " ".join(re.findall(r"[a-z0-9]+", name.casefold()))
    return f"supplier:{exact_name.replace(' ', '-')}"


def _graph(
    packets: list[Packet],
    historical_payments: list[dict[str, Any]],
    current_payments: list[dict[str, Any]],
    quote_signals: list[dict[str, Any]],
    financial_signals: list[dict[str, Any]],
    payment_signals: list[dict[str, Any]],
    deposit_signals: list[dict[str, Any]],
    supplementary_signals: list[dict[str, Any]],
    root: Path,
) -> dict[str, list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_edge(
        source_id: str,
        target_id: str,
        relationship: str,
        evidence: str,
        signal_id: str | None = None,
    ) -> None:
        key = (source_id, target_id, relationship)
        edge = edges.setdefault(
            key,
            {
                "source": source_id,
                "target": target_id,
                "relationship": relationship,
                "evidence": [],
                "signalIds": [],
            },
        )
        if evidence not in edge["evidence"]:
            edge["evidence"].append(evidence)
        if signal_id and signal_id not in edge["signalIds"]:
            edge["signalIds"].append(signal_id)

    packet_by_key: dict[str, Packet] = {}
    app_id_by_key: dict[str, str] = {}
    for packet in packets:
        borrower_key = normalize_supplier(packet.name)
        app_id = f"current:{borrower_key.replace(' ', '-')}"
        packet_by_key[borrower_key] = packet
        app_id_by_key[borrower_key] = app_id
        nodes[app_id] = _node(app_id, packet.name, "application", period="current")
        business_id = f"business:{borrower_key.replace(' ', '-')}"
        nodes[business_id] = _node(business_id, packet.name, "business")
        source = next(
            (
                _relative_source(page.path, root, page.number)
                for page in packet.pages
                if re.search(r"Financial Statements|P&L and balance sheet", page.text, re.I)
            ),
            packet.source_name,
        )
        add_edge(app_id, business_id, "applicant", source)

        preparer, preparer_page = _preparer(packet.pages)
        if preparer and preparer_page:
            preparer_id = f"preparer:{normalize_supplier(preparer).replace(' ', '-')}"
            nodes[preparer_id] = _node(preparer_id, preparer, "preparer")
            add_edge(
                app_id,
                preparer_id,
                "prepared_by",
                _relative_source(preparer_page.path, root, preparer_page.number),
            )
        for quote in packet.quotes:
            quote_node = f"quote:{quote['id'].casefold()}"
            nodes[quote_node] = _node(quote_node, quote["id"], "document_identifier")
            add_edge(app_id, quote_node, "references", quote["source"])
            supplier_id = _supplier_entity_id(quote["supplier"])
            nodes[supplier_id] = _node(supplier_id, quote["supplier"], "supplier")
            add_edge(app_id, supplier_id, "quotes_from", quote["source"])
            account = quote.get("accountLast4")
            if account:
                account_id = f"payment-account:{account}"
                nodes[account_id] = _node(
                    account_id, f"Account ending {account}", "payment_account"
                )
                add_edge(supplier_id, account_id, "remits_to", quote["source"])

    for record in sorted(
        historical_payments, key=lambda item: item["date"], reverse=True
    ):
        supplier_key = record["supplierKey"]
        if not any(
            payment["supplierKey"] == supplier_key for payment in current_payments
        ):
            continue
        supplier_id = _supplier_entity_id(record["supplier"])
        nodes.setdefault(
            supplier_id, _node(supplier_id, record["supplier"], "supplier")
        )
        historical_id = f"historical:{record['dealId']}"
        nodes[historical_id] = _node(
            historical_id,
            record["dealId"],
            "historical_deal",
            borrower=record["borrower"],
            status=record["status"],
            date=record["date"],
            source=record["source"],
        )
        add_edge(historical_id, supplier_id, "used_supplier", record["source"])
        account_id = f"payment-account:{record['accountLast4']}"
        nodes[account_id] = _node(
            account_id, f"Account ending {record['accountLast4']}", "payment_account"
        )
        add_edge(supplier_id, account_id, "historical_destination", record["source"])

    related_payments = historical_payments + current_payments
    names_by_candidate: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in related_payments:
        names_by_candidate[record["supplierKey"]][record["supplier"]].append(record)
    current_supplier_keys = {record["supplierKey"] for record in current_payments}
    for candidate_key, variants in names_by_candidate.items():
        if candidate_key not in current_supplier_keys or len(variants) < 2:
            continue
        candidate_id = f"supplier-name-candidate:{candidate_key.replace(' ', '-')}"
        nodes[candidate_id] = _node(
            candidate_id,
            "Similar supplier-name candidate",
            "supplier_name_candidate",
        )
        signal_id = next(
            (
                signal["id"] for signal in supplementary_signals
                if signal["family"] == "supplier_name_variation"
                and signal.get("entityKey") == candidate_key
            ),
            None,
        )
        for supplier_name, records in variants.items():
            supplier_id = _supplier_entity_id(supplier_name)
            for record in records:
                add_edge(
                    supplier_id,
                    candidate_id,
                    "name_similarity_candidate",
                    record["source"],
                    signal_id,
                )

    for signal in payment_signals:
        for borrower in signal["borrowers"]:
            key = normalize_supplier(borrower)
            app_id = app_id_by_key.get(key)
            if not app_id:
                continue
            packet = packet_by_key[key]
            for quote in packet.quotes:
                if quote.get("accountLast4"):
                    add_edge(
                        app_id,
                        f"payment-account:{quote['accountLast4']}",
                        "shared_destination",
                        quote["source"],
                        signal["id"],
                    )
        for record in signal["historicalDeals"]:
            add_edge(
                f"historical:{record['id']}",
                f"payment-account:{next((row['accountLast4'] for row in historical_payments if row['dealId'] == record['id']), '')}",
                "historical_exposure",
                record["source"],
                signal["id"],
            )

    for signal in quote_signals:
        group_id = f"quote-sequence:{signal['id']}"
        nodes[group_id] = _node(group_id, signal["title"], "document_pattern")
        for borrower in signal["borrowers"]:
            app_id = app_id_by_key.get(normalize_supplier(borrower))
            if app_id:
                matching_evidence = next(
                    item["source"] for item in signal["evidence"]
                    if borrower in item["text"]
                )
                add_edge(
                    app_id, group_id, "shares_document_sequence",
                    matching_evidence, signal["id"]
                )

    for packet in packets:
        if packet.financials:
            financial_id = f"financial-document:{normalize_supplier(packet.name).replace(' ', '-')}"
            nodes[financial_id] = _node(
                financial_id, f"{packet.name} FY2025 statements", "financial_document"
            )
            app_id = app_id_by_key[normalize_supplier(packet.name)]
            financial_page = next(
                page for page in packet.pages if _financial_signature([page])
            )
            add_edge(
                app_id, financial_id, "submitted_financials",
                _relative_source(financial_page.path, root, financial_page.number),
                next(
                    (
                        signal["id"] for signal in financial_signals
                        if packet.name in signal["borrowers"]
                    ),
                    None,
                ),
            )
    for signal in financial_signals:
        doc_nodes = [
            f"financial-document:{normalize_supplier(name).replace(' ', '-')}"
            for name in signal["borrowers"]
        ]
        if len(doc_nodes) >= 2:
            relation = (
                "similar_expense_structure"
                if signal["family"] == "financial_structure_similarity"
                else "matching_financial_values"
            )
            for left_index, right_index in combinations(range(len(doc_nodes)), 2):
                left_name = signal["borrowers"][left_index]
                right_name = signal["borrowers"][right_index]
                pair_evidence = [
                    item["source"] for item in signal["evidence"]
                    if left_name.casefold() in item["text"].casefold()
                    or right_name.casefold() in item["text"].casefold()
                ]
                add_edge(
                    doc_nodes[left_index], doc_nodes[right_index], relation,
                    "; ".join(pair_evidence), signal["id"]
                )

    for signal in deposit_signals:
        pattern_id = f"behavior-pattern:{signal['id']}"
        nodes[pattern_id] = _node(
            pattern_id, signal["title"], "behavioral_pattern"
        )
        for borrower in signal["borrowers"]:
            app_id = app_id_by_key.get(normalize_supplier(borrower))
            if app_id:
                evidence = signal["evidence"][0]["source"]
                add_edge(
                    app_id, pattern_id, "deposit_behavior_outlier",
                    evidence, signal["id"]
                )

    for signal in supplementary_signals:
        if signal["family"] == "supplier_name_variation":
            pattern_id = f"supplier-name-candidate:{signal['entityKey'].replace(' ', '-')}"
            nodes.setdefault(
                pattern_id,
                _node(pattern_id, "Similar supplier-name candidate", "supplier_name_candidate"),
            )
            relationship = "candidate_supplier_name"
        else:
            pattern_id = f"pattern:{signal['id']}"
            kind = (
                "supplier_pattern"
                if signal["family"] == "supplier_concentration"
                else "preparer_network"
                if signal["family"] == "preparer_network"
                else "financial_pattern"
            )
            label = (
                f"Preparer contact ending {signal['entityKey'].split(':', 1)[0][-4:]}"
                if signal["family"] == "preparer_network"
                else signal["title"]
            )
            nodes[pattern_id] = _node(pattern_id, label, kind)
            relationship = signal["family"]
        for borrower in signal["borrowers"]:
            app_id = app_id_by_key.get(normalize_supplier(borrower))
            if not app_id:
                continue
            packet = packet_by_key[normalize_supplier(borrower)]
            evidence = next(
                (
                    item["source"] for item in signal["evidence"]
                    if borrower.casefold() in item["text"].casefold()
                ),
                signal["evidence"][0]["source"],
            )
            add_edge(app_id, pattern_id, relationship, evidence, signal["id"])
            if signal["family"] == "supplier_concentration":
                for quote in packet.quotes:
                    if quote["supplierKey"] == signal["entityKey"]:
                        add_edge(
                            _supplier_entity_id(quote["supplier"]),
                            pattern_id,
                            "concentrated_supplier",
                            quote["source"],
                            signal["id"],
                        )
            if signal["family"] == "preparer_network":
                details = _preparer_details(packet.pages, root)
                if details:
                    preparer_id = (
                        f"preparer:{normalize_supplier(details['name']).replace(' ', '-')}"
                    )
                    nodes.setdefault(
                        preparer_id, _node(preparer_id, details["name"], "preparer")
                    )
                    add_edge(
                        preparer_id, pattern_id, "shared_preparer_contact",
                        details["source"], signal["id"],
                    )
        for record in signal["historicalDeals"]:
            historical_id = f"historical:{record['id']}"
            nodes.setdefault(
                historical_id,
                _node(
                    historical_id,
                    record["id"],
                    "historical_deal",
                    borrower=record["borrower"],
                    status=record["status"],
                    date=record["date"],
                    source=record["source"],
                ),
            )
            add_edge(
                historical_id,
                pattern_id,
                relationship,
                record["source"],
                signal["id"],
            )

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _behavior_reference(
    signals: list[dict[str, Any]], behavior: dict[str, Any]
) -> dict[str, Any]:
    return {"historicalCohort": behavior, "signals": [
        signal for signal in signals if signal["family"] == "deposit_behavior"
    ]}


def analyze(data_root: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    participant_root = data_root / "participant" / "participant"
    history_dir = participant_root / "history"
    shared_folder = participant_root / "shared_folder"
    deals, baseline, historical_payments = _historical_data(history_dir, data_root)
    packets = _read_packets(shared_folder, data_root)
    historical_preparer_records = _historical_preparers(
        deals, history_dir, data_root
    )
    payment_signals, current_payments = _payment_signals(
        packets, historical_payments
    )
    supplier_name_signals = _supplier_name_variation_signals(
        current_payments, historical_payments
    )
    supplier_concentration_signals = _supplier_concentration_signals(
        current_payments, historical_payments
    )
    preparer_signals = _preparer_signals(
        packets, historical_preparer_records, data_root
    )
    quote_signals, _ = _quote_signals(packets)
    financial_signals = _financial_signals(packets, data_root)
    financial_structure_signals = _financial_structure_signals(packets, data_root)
    financial_signals.extend(financial_structure_signals)

    historical_behavior: list[dict[str, Any]] = []
    monthly_path = history_dir / "monthly_deposits.csv"
    if monthly_path.is_file():
        with (history_dir / "deals.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            historical_rows = list(csv.DictReader(stream))
        repayment_by_id = {row["deal_id"]: row.get("repayment_status", "") for row in historical_rows}
        monthly: dict[str, list[float]] = defaultdict(list)
        with monthly_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                try:
                    monthly[row["deal_id"]].append(
                        float(row["pct_deposits_multiple_of_100"])
                    )
                except (KeyError, ValueError):
                    continue
        historical_behavior = [
            {
                "deal_id": deal_id,
                "meanRoundDepositPct": sum(values) / len(values),
                "repaymentStatus": repayment_by_id.get(deal_id, ""),
            }
            for deal_id, values in monthly.items()
            if values
        ]
    deposit_signals, behavior_baseline = _deposit_behavior_signals(
        packets, historical_behavior, data_root
    )
    supplementary_signals = (
        supplier_name_signals
        + supplier_concentration_signals
        + preparer_signals
        + financial_structure_signals
    )
    signals = (
        payment_signals
        + supplier_name_signals
        + supplier_concentration_signals
        + quote_signals
        + financial_signals
        + preparer_signals
        + deposit_signals
    )
    scores = _risk_scores(signals, packets)
    graph = _graph(
        packets,
        historical_payments,
        current_payments,
        quote_signals,
        financial_signals,
        payment_signals,
        deposit_signals,
        supplementary_signals,
        data_root,
    )
    baseline["depositBehavior"] = behavior_baseline
    baseline["depositBehavior"]["repaymentCohorts"] = _repayment_behavior_cohorts(
        historical_behavior
    )
    baseline["knownFraudLabels"] = {
        code: baseline["knownFraudLabels"].get(code, 0)
        for code in (
            "STMT_TAMPER",
            "REV_INFLATION",
            "ID_MISMATCH",
            "DUP_EIN",
            "SHELL_ADDRESS",
        )
    }
    return {
        "generatedAt": date.today().isoformat(),
        "summary": {
            "historicalDeals": len(deals),
            "currentPackets": len(packets),
            "crossDealSignals": len(signals),
            "linkedApplications": len(
                {name for signal in signals for name in signal["borrowers"]}
            ),
            "graphNodes": len(graph["nodes"]),
            "graphRelationships": len(graph["edges"]),
        },
        "baseline": baseline,
        "scoreMethod": (
            "Uncalibrated review-priority index: 25 points per distinct scoring family, "
            "capped at four (supplier relationships, document identifiers, financial "
            "similarity, and deposit behavior). Supplier account reuse, "
            "name variation, concentration, and account history count once; financial-value "
            "and expense-structure similarity count once. Shared-preparer relationships and "
            "other shared-relationship observations are shown but add no points without "
            "independent evidence. This is not a probability of fraud and is not trained on "
            "historical fraud labels."
        ),
        "scores": list(scores.values()),
        "signals": signals,
        "behavior": _behavior_reference(signals, behavior_baseline),
        "graph": graph,
    }
