from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from dealpilot.ml_model import HistoricalAnomalyModel, train_historical_anomaly_model


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
TRANSACTION_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
TRANSACTION_AMOUNT_RE = re.compile(r"^-?\$?[\d,]+\.\d{2}$")
TRANSACTION_REFERENCE_RE = re.compile(
    r"\b((?:INV(?:OICE)?|REF(?:ERENCE)?|TRACE|CHK|CHECK)[#:\s-]*"
    r"[A-Z0-9]+(?:-[A-Z0-9]+){1,4})\b",
    re.I,
)
DESTINATION_ACCOUNT_RE = re.compile(
    r"\b(?:TO|DEST(?:INATION)?(?:\s+ACCOUNT)?|ACCOUNT\s+ENDING)\s+"
    r"(?:X{2,}|\*{2,})?(\d{4})\b",
    re.I,
)


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
    transactions: list[dict[str, Any]] = field(default_factory=list)
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


def _transaction_record(
    date_value: str,
    description: str,
    amount: float,
    source: str,
) -> dict[str, Any]:
    reference = TRANSACTION_REFERENCE_RE.search(description)
    destination = DESTINATION_ACCOUNT_RE.search(description)
    return {
        "date": date_value,
        "description": re.sub(r"\s+", " ", description).strip(),
        "amount": abs(amount),
        "direction": "credit" if re.search(r"\b(?:CREDIT|DEPOSIT|TRANSFER IN|WIRE IN)\b", description, re.I) else "debit",
        "reference": reference.group(1).upper() if reference else None,
        "destinationLast4": destination.group(1) if destination else None,
        "wholeDollar": amount.is_integer(),
        "source": source,
    }


def _statement_transactions(packet: Packet, root: Path) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    for page in packet.pages:
        if not re.search(r"\b(?:bank|checking|statement)\b", page.text, re.I):
            continue
        lines = [line.strip() for line in page.text.splitlines()]
        for index, line in enumerate(lines):
            if not TRANSACTION_DATE_RE.fullmatch(line):
                continue
            try:
                date_value = datetime.strptime(line, "%m/%d/%Y").date().isoformat()
            except ValueError:
                continue
            description_index = index + 1
            while description_index < len(lines) and not lines[description_index]:
                description_index += 1
            if description_index >= len(lines):
                continue
            description = lines[description_index]
            amount = None
            for candidate in lines[description_index + 1 : description_index + 5]:
                if TRANSACTION_DATE_RE.fullmatch(candidate):
                    break
                if TRANSACTION_AMOUNT_RE.fullmatch(candidate):
                    amount = float(candidate.replace("$", "").replace(",", ""))
                    break
            if amount is None:
                continue
            transactions.append(
                _transaction_record(
                    date_value,
                    description,
                    amount,
                    _relative_source(page.path, root, page.number),
                )
            )
    return transactions


def _csv_transactions(folder: Path, root: Path) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*.csv")):
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                header = next(
                    (
                        row for row in reader
                        if (
                            {"details", "amount"} <= {field.strip().casefold() for field in row}
                            and bool(
                                {"posted date", "date", "transaction date"}
                                & {field.strip().casefold() for field in row}
                            )
                        )
                    ),
                    None,
                )
                if header is None:
                    continue
                fields = {
                    field.strip().casefold(): index
                    for index, field in enumerate(header)
                }
                date_field = next(
                    (fields[key] for key in ("posted date", "date", "transaction date") if key in fields),
                    None,
                )
                description_field = next(
                    (fields[key] for key in ("details", "description", "transaction description") if key in fields),
                    None,
                )
                amount_field = next(
                    (fields[key] for key in ("amount", "transaction amount") if key in fields),
                    None,
                )
                if date_field is None or description_field is None or amount_field is None:
                    continue
                for row in reader:
                    if max(date_field, description_field, amount_field) >= len(row):
                        continue
                    raw_date = row[date_field].strip()
                    parsed_date = None
                    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
                        try:
                            parsed_date = datetime.strptime(raw_date, fmt).date()
                            break
                        except ValueError:
                            continue
                    try:
                        amount = float(row[amount_field].replace(",", "").replace("$", ""))
                    except ValueError:
                        continue
                    description = row[description_field].strip()
                    if not parsed_date or not description:
                        continue
                    transactions.append(
                        _transaction_record(
                            parsed_date.isoformat(),
                            description,
                            amount,
                            _relative_source(path, root),
                        )
                    )
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise RuntimeError(f"Unable to read transaction CSV {path}: {exc}") from exc
    return transactions


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
        packet.transactions = _statement_transactions(packet, root)
        packet.transactions.extend(_csv_transactions(folder, root))
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
    industry_by_deal = {
        row.get("deal_id", ""): row.get("naics") or row.get("industry", "")
        for row in deals
    }

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
            "industry": industry_by_deal.get(deal_id, ""),
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


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        for date_format in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(value.strip(), date_format).date()
            except ValueError:
                continue
    return None


def _float_value(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _purpose_group(value: str) -> str:
    text = value.casefold()
    for group, terms in (
        ("equipment", ("equipment", "vehicle", "trailer", "machinery")),
        ("inventory", ("inventory", "stock")),
        ("payroll", ("payroll", "wages")),
        ("real_estate", ("real estate", "property", "building")),
        ("refinance", ("refinance", "refinancing", "debt")),
        ("expansion", ("expansion", "expand", "growth", "route")),
    ):
        if any(term in text for term in terms):
            return group
    return "other" if text else ""


def _monthly_ml_features(
    monthly_rows: list[dict[str, str]], revenue: float | None
) -> dict[str, float]:
    totals: list[float] = []
    counts: list[float] = []
    round_rates: list[float] = []
    largest_deposits: list[float] = []
    ending_balances: list[float] = []
    for row in monthly_rows:
        total = _float_value(row.get("total_deposits"))
        count = _float_value(row.get("deposit_count"))
        round_rate = _float_value(row.get("pct_deposits_multiple_of_100"))
        largest = _float_value(row.get("largest_deposit"))
        balance = _float_value(row.get("month_end_balance"))
        if total is not None and total >= 0:
            totals.append(total)
        if count is not None and count >= 0:
            counts.append(count)
        if round_rate is not None and 0 <= round_rate <= 1:
            round_rates.append(round_rate)
        if largest is not None and largest >= 0:
            largest_deposits.append(largest)
        if balance is not None and balance >= 0:
            ending_balances.append(balance)

    mean_total = sum(totals) / len(totals) if totals else 0.0
    total_cv = (
        math.sqrt(sum((value - mean_total) ** 2 for value in totals) / len(totals))
        / mean_total
        if mean_total > 0 else 0.0
    )
    return {
        "deposit_months": float(len(totals)),
        "mean_deposit_count": sum(counts) / len(counts) if counts else 0.0,
        "round_deposit_share": sum(round_rates) / len(round_rates) if round_rates else 0.0,
        "deposit_volume_cv": total_cv,
        "annualized_deposits_to_revenue": (
            mean_total * 12 / revenue if revenue and revenue > 0 else 0.0
        ),
        "largest_deposit_to_monthly_deposits": (
            sum(largest_deposits) / len(largest_deposits) / mean_total
            if largest_deposits and mean_total > 0 else 0.0
        ),
        "ending_balance_to_revenue": (
            sum(ending_balances) / len(ending_balances) / revenue
            if ending_balances and revenue and revenue > 0 else 0.0
        ),
        "has_deposit_history": float(bool(totals)),
    }


def _ml_features(
    *,
    amount: float | None,
    revenue: float | None,
    term: float | None,
    business_age: float | None,
    industry: str,
    product: str,
    purpose: str,
    bank: str,
    monthly_rows: list[dict[str, str]],
) -> dict[str, float | str]:
    features: dict[str, float | str] = {
        "industry": industry.strip().casefold(),
        "product": product.strip().casefold(),
        "purpose_group": _purpose_group(purpose),
        "bank": bank.strip().casefold(),
        "log_annual_revenue": math.log1p(revenue) if revenue is not None and revenue > 0 else 0.0,
        "log_loan_amount": math.log1p(amount) if amount is not None and amount > 0 else 0.0,
        "loan_to_revenue": amount / revenue if amount is not None and revenue and revenue > 0 else 0.0,
        "term_months": term or 0.0,
        "business_age_years": business_age or 0.0,
        "has_annual_revenue": float(revenue is not None and revenue > 0),
        "has_loan_amount": float(amount is not None and amount > 0),
        "has_term": float(term is not None and term > 0),
        "has_business_age": float(business_age is not None and business_age > 0),
    }
    features.update(_monthly_ml_features(monthly_rows, revenue))
    return features


def _historical_ml_features(
    deals: list[dict[str, Any]],
    monthly_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    monthly_by_deal: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in monthly_rows:
        monthly_by_deal[row.get("deal_id", "")].append(row)

    results: list[dict[str, Any]] = []
    for row in deals:
        application_date = _parse_date(row.get("application_date", ""))
        formation_date = _parse_date(row.get("formation_date", ""))
        business_age = (
            (application_date - formation_date).days / 365.25
            if application_date and formation_date and application_date >= formation_date
            else None
        )
        revenue = _float_value(row.get("annual_revenue_stated"))
        features = _ml_features(
            amount=_float_value(row.get("amount_requested")),
            revenue=revenue,
            term=_float_value(row.get("term_months")),
            business_age=business_age,
            industry=row.get("naics") or row.get("industry", ""),
            product=row.get("product", ""),
            purpose=row.get("purpose", ""),
            bank=row.get("bank_name", ""),
            monthly_rows=monthly_by_deal.get(row.get("deal_id", ""), []),
        )
        results.append({"fraud_label": row.get("fraud_label", "none"), "features": features})
    return results


def _current_ml_features(
    packet: Packet, root: Path
) -> tuple[dict[str, float | str], str]:
    established = _application_field_value(packet, r"Date business established")
    established_date = _parse_date(established)
    business_age = (
        (date.today() - established_date).days / 365.25
        if established_date and established_date <= date.today()
        else None
    )
    industry_text = _application_field_value(packet, r"Industry(?:\s*\(NAICS\))?")
    naics = re.search(r"\b(\d{6})\b", industry_text)
    bank_text = _application_field_value(packet, r"Primary bank(?:\s*/\s*account)?")
    bank = re.sub(r"\s+(?:\.{2,}|…|\*{2,}|x{2,}).*$", "", bank_text, flags=re.I)

    deposits_by_month: dict[str, list[float]] = defaultdict(list)
    for transaction in packet.transactions:
        if transaction.get("direction") != "credit":
            continue
        month = transaction["date"][:7]
        deposits_by_month[month].append(float(transaction["amount"]))
    monthly_rows = [
        {
            "total_deposits": str(sum(amounts)),
            "deposit_count": str(len(amounts)),
            "pct_deposits_multiple_of_100": str(
                sum(abs(amount / 100 - round(amount / 100)) < 1e-9 for amount in amounts)
                / len(amounts)
            ),
            "largest_deposit": str(max(amounts)),
        }
        for amounts in deposits_by_month.values()
        if amounts
    ]
    revenue = _float_value(_application_field_value(packet, r"Annual revenue(?:\s*\([^)]*\))?"))
    amount = _float_value(_application_field_value(packet, r"Amount requested"))
    term_match = re.search(
        r"(\d+(?:\.\d+)?)",
        _application_field_value(packet, r"Requested term"),
    )
    term = float(term_match.group(1)) if term_match else None
    features = _ml_features(
        amount=amount,
        revenue=revenue,
        term=term,
        business_age=business_age,
        industry=naics.group(1) if naics else industry_text,
        product=_application_field_value(packet, r"Product"),
        purpose=_application_field_value(packet, r"Use of proceeds"),
        bank=bank,
        monthly_rows=monthly_rows,
    )
    source_page = next(
        (page for page in packet.pages if "application" in page.path.name.casefold()),
        packet.pages[0],
    )
    return features, _relative_source(source_page.path, root, source_page.number)


def _application_field_value(packet: Packet, pattern: str) -> str:
    for page in packet.pages:
        lines = [line.strip() for line in page.text.splitlines()]
        for index, line in enumerate(lines):
            if re.fullmatch(pattern, line, re.I):
                return next(
                    (candidate for candidate in lines[index + 1:] if candidate),
                    "",
                )
    return ""


def _ml_anomaly_signals(
    packets: list[Packet],
    model: HistoricalAnomalyModel,
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    feature_rows: list[dict[str, float | str]] = []
    sources: list[str] = []
    for packet in packets:
        features, source = _current_ml_features(packet, root)
        feature_rows.append(features)
        sources.append(source)

    results = model.score(feature_rows)
    signals: list[dict[str, Any]] = []
    by_borrower: dict[str, dict[str, Any]] = {}
    for packet, source, result in zip(packets, sources, results):
        anomaly = {
            **result,
            "algorithm": "Isolation Forest",
        }
        by_borrower[normalize_supplier(packet.name)] = anomaly
        if not result["isOutlier"]:
            continue
        record = {
            "borrower": packet.name,
            "borrowerKey": normalize_supplier(packet.name),
            "current": True,
            "dealId": None,
        }
        signal = _make_signal(
            f"ml-anomaly-{len(signals) + 1}",
            "ml_anomaly",
            "Historical-profile anomaly flagged by ML model",
            "An unsupervised Isolation Forest found this application's combination of "
            "loan, business, and deposit features unusual relative to historical deals. "
            "This is a review lead, not evidence of fraud.",
            "Moderate",
            [record],
            [
                _evidence(
                    f"Isolation Forest anomaly rank: {result['percentile']:.1f}th "
                    f"percentile versus {model.training_rows} historical applications "
                    "without existing bank-rule labels. This percentile is not a fraud probability.",
                    source,
                    "isolation_forest_anomaly_rank",
                )
            ],
            [
                "Review the application and source statements for the features that differ from historical patterns.",
                "Verify any unusual figures with independent borrower and bank records.",
            ],
        )
        signal["scoreContribution"] = False
        signal["riskDimension"] = "behavioral_anomaly"
        signal["behaviorSeverity"] = (
            "High" if result["percentile"] >= 99.5 else "Moderate"
        )
        signal["mlAnomaly"] = anomaly
        signals.append(signal)
    return signals, by_borrower


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
        signal["classification"] = "shared_relationship_observation"
        signal["scoreContribution"] = False
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


def _transaction_signals(
    packets: list[Packet],
    minimum_shared_references: int = 3,
    minimum_destination_transfers: int = 3,
    minimum_round_amount_transactions: int = 50,
    round_amount_threshold: float = 0.95,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    references: dict[str, list[tuple[Packet, dict[str, Any]]]] = defaultdict(list)
    destinations: dict[str, list[tuple[Packet, dict[str, Any]]]] = defaultdict(list)
    for packet in packets:
        for transaction in packet.transactions:
            if transaction.get("reference"):
                references[transaction["reference"]].append((packet, transaction))
            if transaction.get("destinationLast4"):
                destinations[transaction["destinationLast4"]].append((packet, transaction))

    shared_references = {
        reference: entries
        for reference, entries in references.items()
        if len({normalize_supplier(packet.name) for packet, _ in entries}) > 1
    }
    shared_destinations = {
        account: entries
        for account, entries in destinations.items()
        if len({normalize_supplier(packet.name) for packet, _ in entries}) > 1
    }
    eligible_references = {
        reference: entries
        for reference, entries in shared_references.items()
        if len({normalize_supplier(packet.name) for packet, _ in entries}) >= 2
    }
    borrowers_with_repeated_refs = {
        normalize_supplier(packet.name)
        for entries in eligible_references.values()
        for packet, _ in entries
    }
    transfer_matches = {
        account: entries
        for account, entries in shared_destinations.items()
        if len(entries) >= minimum_destination_transfers
    }
    borrowers_with_transfer_matches = {
        normalize_supplier(packet.name)
        for entries in transfer_matches.values()
        for packet, _ in entries
    }

    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    def join(keys: set[str]) -> None:
        ordered = sorted(keys)
        for key in ordered[1:]:
            parent[find(key)] = find(ordered[0])

    for entries in eligible_references.values():
        join({normalize_supplier(packet.name) for packet, _ in entries})
    for entries in transfer_matches.values():
        join({normalize_supplier(packet.name) for packet, _ in entries})

    grouped_borrowers: dict[str, set[str]] = defaultdict(set)
    for key in borrowers_with_repeated_refs | borrowers_with_transfer_matches:
        grouped_borrowers[find(key)].add(key)

    packets_by_key = {normalize_supplier(packet.name): packet for packet in packets}
    signals: list[dict[str, Any]] = []
    for component in grouped_borrowers.values():
        component_refs = {
            reference: entries
            for reference, entries in eligible_references.items()
            if {normalize_supplier(packet.name) for packet, _ in entries} <= component
        }
        component_destinations = {
            account: entries
            for account, entries in transfer_matches.items()
            if {normalize_supplier(packet.name) for packet, _ in entries} <= component
        }
        repeated_across_three = {
            reference: entries
            for reference, entries in component_refs.items()
            if len({normalize_supplier(packet.name) for packet, _ in entries}) >= 3
        }
        if len(repeated_across_three) < minimum_shared_references and not component_destinations:
            continue
        involved = sorted(
            {
                normalize_supplier(packet.name)
                for entries in (*component_refs.values(), *component_destinations.values())
                for packet, _ in entries
            }
        )
        records = [
            {
                "borrower": packets_by_key[key].name,
                "borrowerKey": key,
                "current": True,
                "dealId": None,
            }
            for key in involved
        ]
        evidence: list[dict[str, str]] = []
        if repeated_across_three:
            all_matching = [
                (packet, transaction)
                for entries in repeated_across_three.values()
                for packet, transaction in entries
            ]
            dates_by_reference: dict[str, set[str]] = {
                reference: {transaction["date"] for _, transaction in entries}
                for reference, entries in repeated_across_three.items()
            }
            synchronized = sum(
                len(dates) < len(repeated_across_three[reference])
                for reference, dates in dates_by_reference.items()
            )
            sample_refs = ", ".join(sorted(repeated_across_three)[:6])
            evidence.append(
                _evidence(
                    f"{len(repeated_across_three)} distinct transaction references recur "
                    f"across at least three businesses ({sample_refs}"
                    f"{', …' if len(repeated_across_three) > 6 else ''}); "
                    f"{synchronized} have matching posting dates across borrowers.",
                    all_matching[0][1]["source"],
                    "shared_transaction_references",
                )
            )
            for reference, entries in list(sorted(repeated_across_three.items()))[:6]:
                details = "; ".join(
                    f"{packet.name} on {transaction['date']} for "
                    f"${transaction['amount']:,.2f}"
                    for packet, transaction in entries[:3]
                )
                evidence.append(
                    _evidence(
                        f"Reference {reference}: {details}.",
                        entries[0][1]["source"],
                        "transaction_reference_match",
                    )
                )
        for account, entries in sorted(component_destinations.items()):
            borrowers = sorted({packet.name for packet, _ in entries})
            total = sum(transaction["amount"] for _, transaction in entries)
            dates = sorted({transaction["date"] for _, transaction in entries})
            evidence.append(
                _evidence(
                    f"{len(entries)} transfers from {len(borrowers)} businesses route to "
                    f"destination ending {account}, totaling ${total:,.2f}, across "
                    f"{len(dates)} posting dates ({', '.join(borrowers)}).",
                    entries[0][1]["source"],
                    "shared_transfer_destination",
                )
            )
            for packet, transaction in entries[:3]:
                evidence.append(
                    _evidence(
                        f"{packet.name}: {transaction['date']} "
                        f"{transaction['description']} ${transaction['amount']:,.2f}.",
                        transaction["source"],
                        "transfer_transaction",
                    )
                )
        evidence.append(
            _evidence(
                "Repeated references and shared destinations are candidate relationships. "
                "Confirm invoices, counterparties, and beneficiary accounts independently.",
                evidence[0]["source"],
                "transaction_review_caveat",
            )
        )
        signal = _make_signal(
            f"transaction-cluster-{len(signals) + 1}",
            "transaction_relationship",
            "Repeated transaction references and shared destinations",
            "Multiple borrowers share transaction identifiers or transfer destinations. "
            "Amounts and timing can differ for legitimate reasons; verify underlying invoices "
            "and beneficiaries before treating the relationship as suspicious.",
            "High" if len(repeated_across_three) >= minimum_shared_references else "Elevated",
            records,
            evidence,
            [
                "Compare original invoices and customer remittance records for matched references.",
                "Verify destination ownership using independently confirmed bank details.",
                "Review transaction timing and statement records with an authorized investigator.",
            ],
        )
        signal["scoreFamily"] = "transaction_relationship"
        signal["transactionSummary"] = {
            "sharedReferenceCount": len(repeated_across_three),
            "sharedReferences": sorted(repeated_across_three),
            "sharedDestinations": sorted(component_destinations),
            "matchedPostingDates": synchronized if repeated_across_three else 0,
        }
        signals.append(signal)

    behavioral: list[dict[str, Any]] = []
    for packet in packets:
        transactions = packet.transactions
        if len(transactions) < minimum_round_amount_transactions:
            continue
        rounded = sum(transaction["wholeDollar"] for transaction in transactions)
        ratio = rounded / len(transactions)
        if ratio < round_amount_threshold:
            continue
        source = transactions[0]["source"]
        record = {
            "borrower": packet.name,
            "borrowerKey": normalize_supplier(packet.name),
            "current": True,
            "dealId": None,
        }
        behavioral.append(
            _make_signal(
                f"round-amount-{len(behavioral) + 1}",
                "whole_dollar_transactions",
                "Unusually high whole-dollar transaction share",
                "Most observed transaction amounts are whole-dollar values. This may reflect "
                "cash-heavy or batch-payment operations; it is supporting context, not proof of fraud.",
                "Moderate",
                [record],
                [
                    _evidence(
                        f"{rounded} of {len(transactions)} extracted transactions "
                        f"({ratio:.1%}) have whole-dollar amounts. Historical whole-transaction "
                        "records are not available for a directly comparable baseline.",
                        source,
                        "whole_dollar_transaction_ratio",
                    )
                ],
                [
                    "Compare transaction originals with invoices and settlement records.",
                    "Interpret this ratio in the context of the borrower's industry and payment channels.",
                ],
            )
        )
        behavioral[-1]["scoreContribution"] = False
        behavioral[-1]["riskDimension"] = "behavioral_anomaly"
        behavioral[-1]["behaviorSeverity"] = "Low"
    return signals, behavioral


def _document_review_signals(
    packets: list[Packet], root: Path
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    current_eins: dict[str, list[tuple[str, Page, str]]] = defaultdict(list)
    for packet in packets:
        record = {
            "borrower": packet.name,
            "borrowerKey": normalize_supplier(packet.name),
            "current": True,
            "dealId": None,
        }
        ein_values: dict[str, list[tuple[Page, str]]] = defaultdict(list)
        legal_names: dict[str, list[tuple[Page, str, str]]] = defaultdict(list)
        requested_amounts: dict[str, list[tuple[Page, float, str]]] = defaultdict(list)
        page_totals: dict[Path, tuple[int, int, Page]] = {}
        financial_values: dict[str, list[tuple[Page, float, str]]] = defaultdict(list)
        for page in packet.pages:
            lines = [line.strip() for line in page.text.splitlines()]
            source = _relative_source(page.path, root, page.number)
            for match in re.finditer(r"(?<!\d)(\d{2}-\d{7})(?!\d)", page.text):
                context_start = max(0, match.start() - 100)
                context_end = min(len(page.text), match.end() + 40)
                context = page.text[context_start:context_end]
                if re.search(r"\b(?:EIN|employer identification|federal tax)\b", context, re.I):
                    ein_values[match.group(1)].append((page, source))
            for index, line in enumerate(lines):
                if re.fullmatch(
                    r"(?:legal business name|name of business|business legal name)",
                    re.sub(r"\s+", " ", line),
                    re.I,
                ):
                    candidate = next((value for value in lines[index + 1 : index + 4] if value and value != "\ufffd"), "")
                    if candidate:
                        legal_names[normalize_supplier(candidate)].append((page, candidate, source))
            for index, line in enumerate(lines):
                if re.fullmatch(r"amount requested", re.sub(r"\s+", " ", line), re.I):
                    candidate = next((value for value in lines[index + 1 : index + 4] if value), "")
                    amount_match = re.search(r"\$?\s*([\d,]+(?:\.\d{2})?)", candidate)
                    if amount_match:
                        value = float(amount_match.group(1).replace(",", ""))
                        requested_amounts["application"].append((page, value, source))
                request_match = re.search(
                    r"\b(?:requesting|request(?:ed)? amount|loan request)\b[^\n$]{0,50}\$([\d,]+(?:\.\d{2})?)",
                    line,
                    re.I,
                )
                if request_match:
                    value = float(request_match.group(1).replace(",", ""))
                    requested_amounts["supporting_letter"].append((page, value, source))
                if re.search(r"\bFY\s*2025\b", page.text, re.I) and re.fullmatch(
                    r"(?:revenue|gross receipts|total income)", line, re.I
                ):
                    candidate = next((value for value in lines[index + 1 : index + 4] if value), "")
                    amount_match = re.fullmatch(r"\$?([\d,]+(?:\.\d{2})?)", candidate)
                    if amount_match:
                        value = float(amount_match.group(1).replace(",", ""))
                        financial_values["FY2025 revenue"].append((page, value, source))
            for match in re.finditer(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", page.text, re.I):
                page_number, expected = int(match.group(1)), int(match.group(2))
                current = page_totals.get(page.path)
                if current is None or expected > current[1]:
                    page_totals[page.path] = (page_number, expected, page)

        ein_distinct = len(ein_values)
        for value, occurrences in ein_values.items():
            current_eins[value].extend(
                (packet.name, page, source) for page, source in occurrences
            )
        if ein_distinct > 1:
            evidence = [
                _evidence(
                    f"EIN {value} appears in {page.path.name}.",
                    source,
                    "document_ein",
                )
                for value, occurrences in ein_values.items()
                for page, source in occurrences[:1]
            ]
            findings.append(
                _make_signal(
                    f"document-ein-{len(findings) + 1}",
                    "document_reconciliation",
                    "Business EIN differs across submitted documents",
                    "The application packet contains more than one EIN. This is a document "
                    "inconsistency requiring clarification, not a finding of identity fraud.",
                    "Moderate",
                    [record],
                    evidence,
                    ["Confirm the correct EIN against independently verified IRS records."],
                )
            )
            findings[-1]["classification"] = "document_review_issue"
            findings[-1]["scoreContribution"] = False
        if len(legal_names) > 1:
            evidence = [
                _evidence(
                    f"Document lists business name {name}.",
                    source,
                    "document_legal_name",
                )
                for occurrences in legal_names.values()
                for _, name, source in occurrences[:1]
            ]
            findings.append(
                _make_signal(
                    f"document-legal-name-{len(findings) + 1}",
                    "document_reconciliation",
                    "Business names differ across submitted documents",
                    "Documents contain different normalized legal business names. Confirm "
                    "whether the difference reflects a trade name, a legal-name change, or a record error.",
                    "Moderate",
                    [record],
                    evidence,
                    ["Confirm the current legal entity name against independently verified registration records."],
                )
            )
            findings[-1]["classification"] = "document_review_issue"
            findings[-1]["scoreContribution"] = False

        app_amounts = requested_amounts.get("application", [])
        support_amounts = requested_amounts.get("supporting_letter", [])
        if app_amounts and support_amounts:
            app_value = app_amounts[0][1]
            conflicts = [item for item in support_amounts if abs(item[1] - app_value) > 0.01]
            if conflicts:
                page, value, source = app_amounts[0]
                evidence = [
                    _evidence(f"Application amount requested: ${app_value:,.2f}.", source, "application_amount"),
                    *[
                        _evidence(
                            f"Supporting document amount requested: ${other:,.2f}.",
                            other_source,
                            "supporting_letter_amount",
                        )
                        for _, other, other_source in conflicts
                    ],
                ]
                findings.append(
                    _make_signal(
                        f"document-amount-{len(findings) + 1}",
                        "document_reconciliation",
                        "Requested loan amounts differ across documents",
                        "The application and a supporting request document list different amounts. "
                        "Confirm the intended request; a discrepancy is not proof of fraud.",
                        "Moderate",
                        [record],
                        evidence,
                        ["Confirm the requested amount with the borrower using the verified contact on file."],
                    )
                )
                findings[-1]["classification"] = "document_review_issue"
                findings[-1]["scoreContribution"] = False

        for path, (page_number, expected, page) in page_totals.items():
            actual_pages = sum(item.path == path for item in packet.pages)
            if expected > actual_pages:
                findings.append(
                    _make_signal(
                        f"document-pages-{len(findings) + 1}",
                        "document_completeness",
                        "A document appears to be missing pages",
                        "The file's own page numbering indicates more pages than were received. "
                        "This is an incomplete-document issue, not evidence of fraud.",
                        "Moderate",
                        [record],
                        [
                            _evidence(
                                f"{path.name} labels this as page {page_number} of {expected}, "
                                f"but the PDF contains {actual_pages} page(s).",
                                _relative_source(page.path, root, page.number),
                                "incomplete_document_pages",
                            )
                        ],
                        ["Request the complete document before relying on its transaction detail."],
                    )
                )
                findings[-1]["classification"] = "document_review_issue"
                findings[-1]["scoreContribution"] = False

        values = financial_values.get("FY2025 revenue", [])
        if len(values) > 1 and max(value for _, value, _ in values) - min(
            value for _, value, _ in values
        ) > 0.01:
            evidence = [
                _evidence(
                    f"FY2025 revenue: ${value:,.2f}.",
                    source,
                    "financial_period_reconciliation",
                )
                for _, value, source in values
            ]
            findings.append(
                _make_signal(
                    f"document-financial-{len(findings) + 1}",
                    "document_reconciliation",
                    "FY2025 revenue differs across financial documents",
                    "Submitted financial documents report different values for the same revenue "
                    "period. Verify source statements and reporting definitions before drawing conclusions.",
                    "Moderate",
                    [record],
                    evidence,
                    ["Reconcile values with the borrower or preparer and retain the source documents."],
                )
            )
            findings[-1]["classification"] = "document_review_issue"
            findings[-1]["scoreContribution"] = False
    for ein, occurrences in current_eins.items():
        borrowers = sorted({borrower for borrower, _, _ in occurrences})
        if len(borrowers) < 2:
            continue
        records = [
            {
                "borrower": borrower,
                "borrowerKey": normalize_supplier(borrower),
                "current": True,
                "dealId": None,
            }
            for borrower in borrowers
        ]
        evidence = [
            _evidence(
                f"EIN {ein} appears in documents for {borrower}.",
                source,
                "cross_application_ein",
            )
            for borrower, _, source in occurrences
        ]
        findings.append(
            _make_signal(
                f"cross-application-ein-{len(findings) + 1}",
                "document_reconciliation",
                "The same EIN appears across different current applicants",
                "A federal EIN appears in documents for multiple applicants. Verify entity "
                "ownership and the source documents; shared control or a data-entry error can be legitimate.",
                "Moderate",
                records,
                evidence,
                ["Verify each applicant's EIN and legal entity using independently sourced records."],
            )
        )
        findings[-1]["classification"] = "document_review_issue"
        findings[-1]["scoreContribution"] = False
    return findings


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
    history_by_industry: dict[str, list[float]] = defaultdict(list)
    history_values: list[float] = []
    for row in historical_behavior:
        value = row["meanRoundDepositPct"]
        history_values.append(value)
        if row.get("industry"):
            history_by_industry[row["industry"]].append(value)
    if len(history_values) < 20:
        return [], {"available": False, "reason": "Insufficient historical reference deals."}

    def percentile(values: list[float], value: float) -> float:
        return sum(item <= value for item in values) / len(values) * 100

    def threshold_for(values: list[float]) -> float:
        ordered = sorted(values)
        index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[index]

    signals: list[dict[str, Any]] = []
    evaluated = 0
    industry_comparisons = 0
    for packet in packets:
        monthly_transactions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for transaction in packet.transactions:
            if transaction["direction"] == "credit":
                monthly_transactions[transaction["date"][:7]].append(transaction)
        usable = {
            month: transactions
            for month, transactions in monthly_transactions.items()
            if transactions
        }
        total = sum(len(transactions) for transactions in usable.values())
        if len(usable) < 3 or total < 10:
            continue
        evaluated += 1
        round_counts = {
            month: sum(transaction["wholeDollar"] for transaction in transactions)
            for month, transactions in usable.items()
        }
        monthly_rates = [
            round_counts[month] / len(transactions)
            for month, transactions in usable.items()
        ]
        round_count = sum(round_counts.values())
        observed = sum(monthly_rates) / len(monthly_rates)

        industry_text = _application_field_value(
            packet,
            r"Industry(?:\s*\(NAICS\))?",
        )
        industry_match = re.search(r"\b(\d{6})\b", industry_text)
        industry_key = industry_match.group(1) if industry_match else ""
        peer_values = history_by_industry.get(industry_key, [])
        use_industry_peers = len(peer_values) >= 5
        comparison_values = peer_values if use_industry_peers else history_values
        threshold = threshold_for(comparison_values)
        observed_percentile = percentile(comparison_values, observed)
        industry_comparisons += int(use_industry_peers)
        if observed <= threshold:
            continue

        channel_counts: dict[str, int] = defaultdict(int)
        evidence = []
        for month, transactions in sorted(usable.items()):
            for transaction in transactions:
                description = transaction["description"].casefold()
                channel = (
                    "wire"
                    if "wire" in description
                    else "ACH"
                    if "ach" in description
                    else "branch/cash"
                    if any(term in description for term in ("branch", "cash"))
                    else "other credit"
                )
                channel_counts[channel] += 1
            extracted_transactions = "; ".join(
                f"{transaction['date']} {transaction['description']} "
                f"${transaction['amount']:,.2f}"
                for transaction in transactions
            )
            sources = sorted({transaction["source"] for transaction in transactions})
            evidence.append(
                _evidence(
                    f"{month}: {len(transactions)} extracted credits, "
                    f"{round_counts[month]} with whole-dollar amounts. "
                    f"Transactions: {extracted_transactions}.",
                    sources[0],
                    "monthly_deposit_transactions",
                )
            )
        channel_summary = ", ".join(
            f"{channel}: {count}" for channel, count in sorted(channel_counts.items())
        )
        if evidence:
            evidence.append(
                _evidence(
                    f"Current deposit channels across these statements: {channel_summary}. "
                    "Historical files contain monthly aggregate deposits, not transaction "
                    "descriptions, so a historical channel-matched comparison is unavailable.",
                    evidence[0]["source"],
                    "deposit_channel_limit",
                )
            )
        peer_scope = (
            f"the same industry (NAICS {industry_key}, {len(comparison_values)} deals)"
            if use_industry_peers
            else f"all available industries ({len(comparison_values)} deals); fewer than "
            f"5 same-industry records were available for NAICS {industry_key or 'unknown'}"
        )
        record = {
            "borrower": packet.name,
            "borrowerKey": normalize_supplier(packet.name),
            "current": True,
            "dealId": None,
        }
        signal = _make_signal(
            f"deposit-behavior-{len(signals) + 1}",
            "deposit_behavior",
            "Round-number deposit behavior outside historical range",
            f"The mean monthly share of whole-dollar deposits exceeds the 95th percentile "
            f"of historical deal-level averages for {peer_scope}. This is a behavioral "
            "anomaly only, not evidence of fraud or repayment trouble. Historical data "
            "does not contain transaction descriptions for channel-matched comparisons.",
            "Moderate",
            [record],
            [
                _evidence(
                    f"{round_count} of {total} extracted credits across {len(usable)} "
                    f"statement months are whole-dollar amounts. The deal-level mean of "
                    f"monthly rates is {observed:.1%}, versus a same-industry (NAICS "
                    f"{industry_key or 'unknown'}) historical median of "
                    f"{sum(comparison_values) / len(comparison_values):.1%} and 95th "
                    f"percentile of {threshold:.1%} ({len(comparison_values)} prior deals). "
                    f"This ranks at the {observed_percentile:.1f}th percentile.",
                    evidence[0]["source"] if evidence else packet.source_name,
                    "deposit_behavior_cohort",
                ),
                *evidence,
            ],
            [
                "Compare deposit entries with invoices and point-of-sale records.",
                "Compare like deposit channels and industry peers; the historical dataset does not contain transaction-level channel descriptions.",
                "Do not make an adverse decision from this behavioral feature alone.",
            ],
        )
        signal["scoreContribution"] = False
        signal["riskDimension"] = "behavioral_anomaly"
        signal["behaviorSeverity"] = (
            "High" if observed_percentile >= 99 else "Moderate"
        )
        signal["depositBehavior"] = {
            "industryCode": industry_key or None,
            "industryPeerCount": len(peer_values),
            "comparisonDealCount": len(comparison_values),
            "comparisonScope": "industry" if use_industry_peers else "all_industries",
            "dealLevelMean": observed,
            "historicalMedian": sum(comparison_values) / len(comparison_values),
            "historical95thPercentile": threshold,
            "observedPercentile": observed_percentile,
            "channelBreakdown": dict(channel_counts),
            "historicalChannelComparisonAvailable": False,
        }
        signals.append(signal)
    return signals, {
        "available": True,
        "historicalDeals": len(history_values),
        "referencePercentile": 95,
        "industrySpecificComparisons": industry_comparisons,
        "currentPacketsEvaluated": evaluated,
        "method": (
            "Current mean monthly whole-dollar deposit share is compared against the "
            "historical 95th percentile of per-deal monthly averages. Use same-NAICS peers "
            "when at least five are available; otherwise disclose the all-industry fallback. "
            "Historical transaction-type descriptions are unavailable."
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


SCORE_FAMILY_LABELS = {
    "supplier_relationship": "Supplier/payment relationships",
    "transaction_relationship": "Cross-business transaction links",
    "financial_similarity": "Matching financial values",
    "document_identifier": "Document identifier patterns",
    "deposit_behavior": "Deposit behavior",
}
SCORE_FAMILY_ORDER = (
    "supplier_relationship",
    "transaction_relationship",
    "financial_similarity",
    "document_identifier",
    "deposit_behavior",
)


def _family_score_details(
    family_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    severity_points = {"Moderate": 3, "Elevated": 8, "High": 13}
    severity = max(
        (signal.get("severity", "Moderate") for signal in family_signals),
        key=lambda value: severity_points.get(value, 3),
    )
    base_points = severity_points.get(severity, 3)
    evidence = [
        item
        for signal in family_signals
        for item in signal.get("evidence", [])
    ]
    source_documents = {
        re.sub(r":(?:page|line)\s+\d+.*$", "", item.get("source", ""), flags=re.I)
        for item in evidence
        if item.get("source")
    }
    evidence_types = {
        item.get("kind", "")
        for item in evidence
        if item.get("kind")
        and "caveat" not in item.get("kind", "").casefold()
        and "template_control" not in item.get("kind", "").casefold()
    }
    borrowers = {
        normalize_supplier(borrower)
        for signal in family_signals
        for borrower in signal.get("borrowers", [])
    }
    source_bonus = min(8, max(0, len(source_documents) - 1) * 2)
    evidence_type_bonus = min(4, max(0, len(evidence_types) - 1) * 2)
    borrower_bonus = 3 if len(borrowers) >= 3 else 1 if len(borrowers) == 2 else 0
    raw_points = base_points + source_bonus + evidence_type_bonus + borrower_bonus
    points = min(25, raw_points)
    urgency = (
        "Low" if points <= 5
        else "Moderate" if points <= 10
        else "Elevated" if points <= 15
        else "High" if points <= 20
        else "Urgent"
    )
    rationale_parts = [
        f"{severity} signal severity +{base_points}",
        f"{len(source_documents)} distinct source documents +{source_bonus}",
        f"{len(evidence_types)} corroborating evidence types +{evidence_type_bonus}",
        f"{len(borrowers)} connected applicants +{borrower_bonus}",
        f"{'capped at 25' if raw_points > 25 else f'total {points}/25'}",
    ]
    return {
        "points": points,
        "urgency": urgency,
        "rationale": "; ".join(rationale_parts),
    }


def _risk_scores(signals: list[dict[str, Any]], packets: list[Packet]) -> dict[str, dict[str, Any]]:
    signals_by_borrower: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for signal in signals:
        if (
            not signal.get("scoreContribution", True)
            or signal.get("riskDimension") == "behavioral_anomaly"
        ):
            continue
        family = signal.get("scoreFamily", signal["family"])
        for borrower in signal["borrowers"]:
            signals_by_borrower[normalize_supplier(borrower)][family].append(signal)

    family_count = 4
    order = {family: index for index, family in enumerate(SCORE_FAMILY_ORDER)}
    scores: dict[str, dict[str, Any]] = {}
    for packet in packets:
        key = normalize_supplier(packet.name)
        family_signals = signals_by_borrower.get(key, {})
        families = set(family_signals)
        if families == {"document_identifier"}:
            families.clear()
        ordered_families = sorted(
            families,
            key=lambda family: (order.get(family, len(order)), family),
        )
        counted_families = ordered_families[:family_count]
        breakdown = []
        for family in counted_families:
            family_score = _family_score_details(family_signals[family])
            breakdown.append({
                "family": family,
                "label": SCORE_FAMILY_LABELS.get(
                    family,
                    family.replace("_", " ").capitalize(),
                ),
                **family_score,
                "signals": [
                    {
                        "id": signal["id"],
                        "title": signal["title"],
                        "evidence": signal["evidence"][:3],
                    }
                    for signal in family_signals[family]
                ],
            })
        scores[key] = {
            "borrower": packet.name,
            "folder": getattr(packet, "folder", None),
            "score": sum(item["points"] for item in breakdown),
            "families": counted_families,
            "additionalFamilies": ordered_families[family_count:],
            "scoreBreakdown": breakdown,
            "method": (
                "Uncalibrated novel-evidence review index: each distinct family receives "
                "1–25 points from signal severity and corroboration across source documents, "
                "evidence types, and connected applicants; capped at four families. "
                "Behavioral anomalies, document discrepancies, known bank-rule triggers, "
                "historical labels, and operational delays do not contribute."
            ),
        }
    return scores


def _investigations(
    signals: list[dict[str, Any]], scores: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    for signal in signals:
        borrowers = sorted({normalize_supplier(name) for name in signal["borrowers"]})
        for key in borrowers[1:]:
            parent[find(key)] = find(borrowers[0])

    component_signals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, signal in enumerate(signals):
        keys = sorted({normalize_supplier(name) for name in signal["borrowers"]})
        root = find(keys[0]) if keys else f"unlinked-{index}"
        component_signals[root].append(signal)

    severity_rank = {"Moderate": 1, "Elevated": 2, "High": 3}
    investigations: list[dict[str, Any]] = []
    for index, group in enumerate(
        sorted(
            component_signals.values(),
            key=lambda items: sorted(
                normalize_supplier(name)
                for signal in items
                for name in signal["borrowers"]
            ),
        ),
        start=1,
    ):
        borrowers = sorted(
            {
                name
                for signal in group
                for name in signal["borrowers"]
            }
        )
        related_scores = [
            scores.get(normalize_supplier(name), {}).get("score", 0)
            for name in borrowers
        ]
        investigation_signals = [
            signal for signal in group
            if signal.get("riskDimension") != "behavioral_anomaly"
        ]
        severity = max(
            (signal["severity"] for signal in investigation_signals),
            key=lambda value: severity_rank.get(value, 0),
            default="Moderate",
        )
        investigation_id = f"investigation-{index:02d}"
        for signal in group:
            signal["investigationId"] = investigation_id
        investigations.append(
            {
                "id": investigation_id,
                "title": (
                    "Connected review cluster"
                    if len(borrowers) > 1
                    else f"Application review: {borrowers[0]}"
                    if borrowers
                    else "Unlinked review"
                ),
                "severity": severity,
                "borrowers": borrowers,
                "signalIds": [signal["id"] for signal in group],
                "patterns": [signal["title"] for signal in group],
                "priorityScore": max(related_scores, default=0),
                "scoreIsProbability": False,
                "severityBasis": "Investigation evidence; behavioral anomaly severity is reported separately.",
            }
        )
    return investigations


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
    transaction_signals: list[dict[str, Any]],
    behavioral_signals: list[dict[str, Any]],
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

    for signal in transaction_signals:
        summary = signal.get("transactionSummary", {})
        reference_ids = set(summary.get("sharedReferences", []))
        destinations = set(summary.get("sharedDestinations", []))
        for borrower in signal["borrowers"]:
            key = normalize_supplier(borrower)
            app_id = app_id_by_key.get(key)
            packet = packet_by_key.get(key)
            if not app_id or not packet:
                continue
            for transaction in packet.transactions:
                reference = transaction.get("reference")
                if reference in reference_ids:
                    node_id = f"transaction-reference:{reference.casefold()}"
                    nodes.setdefault(
                        node_id,
                        _node(node_id, reference, "transaction_reference"),
                    )
                    add_edge(
                        app_id,
                        node_id,
                        "shares_transaction_reference",
                        transaction["source"],
                        signal["id"],
                    )
                destination = transaction.get("destinationLast4")
                if destination in destinations:
                    node_id = f"transaction-destination:{destination}"
                    nodes.setdefault(
                        node_id,
                        _node(
                            node_id,
                            f"Transfer destination ending {destination}",
                            "payment_destination",
                        ),
                    )
                    add_edge(
                        app_id,
                        node_id,
                        "transfers_to_shared_destination",
                        transaction["source"],
                        signal["id"],
                    )

    for signal in behavioral_signals:
        pattern_id = f"behavior-pattern:{signal['id']}"
        nodes[pattern_id] = _node(
            pattern_id, signal["title"], "behavioral_pattern"
        )
        relationship = (
            "whole_dollar_transaction_outlier"
            if signal["family"] == "whole_dollar_transactions"
            else "deposit_behavior_outlier"
        )
        for borrower in signal["borrowers"]:
            app_id = app_id_by_key.get(normalize_supplier(borrower))
            if app_id:
                add_edge(
                    app_id,
                    pattern_id,
                    relationship,
                    signal["evidence"][0]["source"],
                    signal["id"],
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
                else "ml_anomaly"
                if signal["family"] == "ml_anomaly"
                else "document_review"
                if signal["family"] in {"document_reconciliation", "document_completeness"}
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
    transaction_signals, whole_dollar_signals = _transaction_signals(packets)
    document_review_signals = _document_review_signals(packets, data_root)
    financial_signals = _financial_signals(packets, data_root)
    financial_structure_signals = _financial_structure_signals(packets, data_root)
    financial_signals.extend(financial_structure_signals)

    historical_behavior: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, str]] = []
    monthly_path = history_dir / "monthly_deposits.csv"
    if monthly_path.is_file():
        industry_by_deal = {
            row.get("deal_id", ""): row.get("naics") or row.get("industry", "")
            for row in deals
        }
        repayment_by_id = {
            row["deal_id"]: row.get("repayment_status", "")
            for row in deals
            if row.get("deal_id")
        }
        monthly: dict[str, list[float]] = defaultdict(list)
        with monthly_path.open(encoding="utf-8-sig", newline="") as stream:
            monthly_rows = list(csv.DictReader(stream))
            for row in monthly_rows:
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
                "industry": industry_by_deal.get(deal_id, ""),
            }
            for deal_id, values in monthly.items()
            if values
        ]
    historical_model_rows = _historical_ml_features(deals, monthly_rows)
    ml_model = train_historical_anomaly_model(historical_model_rows)
    ml_signals, ml_scores = _ml_anomaly_signals(packets, ml_model, data_root)
    deposit_signals, behavior_baseline = _deposit_behavior_signals(
        packets, historical_behavior, data_root
    )
    supplementary_signals = (
        supplier_name_signals
        + supplier_concentration_signals
        + preparer_signals
        + financial_structure_signals
        + document_review_signals
        + ml_signals
    )
    signals = (
        payment_signals
        + supplier_name_signals
        + supplier_concentration_signals
        + quote_signals
        + transaction_signals
        + financial_signals
        + preparer_signals
        + deposit_signals
        + whole_dollar_signals
        + document_review_signals
        + ml_signals
    )
    scores = _risk_scores(signals, packets)
    for borrower_key, ml_score in ml_scores.items():
        scores[borrower_key]["mlAnomaly"] = ml_score
    investigations = _investigations(signals, scores)
    graph = _graph(
        packets,
        historical_payments,
        current_payments,
        quote_signals,
        financial_signals,
        payment_signals,
        deposit_signals,
        transaction_signals,
        whole_dollar_signals,
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
            "documentReviewFindings": len(document_review_signals),
            "mlAnomalyFindings": len(ml_signals),
            "investigations": len(investigations),
            "linkedApplications": len(
                {name for signal in signals for name in signal["borrowers"]}
            ),
            "graphNodes": len(graph["nodes"]),
            "graphRelationships": len(graph["edges"]),
        },
        "baseline": baseline,
        "mlModel": ml_model.metadata(
            scored_applications=len(ml_scores),
            outlier_applications=len(ml_signals),
        ),
        "scoreMethod": (
            "Uncalibrated review-priority index: each distinct scoring family receives "
            "1–25 points based on the strongest signal severity, distinct source documents, "
            "corroborating evidence types, and connected applicants; capped at four families. "
            "Supplier account reuse, name variation, concentration, and account history count "
            "once; financial-value and expense-structure similarity count once. Behavioral "
            "anomalies, document inconsistencies, and whole-dollar patterns are informational "
            "and add no points. Shared-preparer relationships and other shared-relationship "
            "observations add no points without independent evidence. "
            "This is not a probability of fraud and is not trained on historical fraud labels."
        ),
        "scores": list(scores.values()),
        "signals": signals,
        "investigations": investigations,
        "behavior": _behavior_reference(signals, behavior_baseline),
        "graph": graph,
    }
