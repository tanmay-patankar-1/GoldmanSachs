from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from pypdf import PdfReader

from dealpilot.engine import Page, _packet_name, _relative_source, normalize_supplier


CENTRAL = timezone(timedelta(hours=-6))
DEFAULT_SIM_TIME = "2026-11-02T08:00-06:00"
STAGES = ("Intake", "Credit", "Committee", "Product", "Committed", "Closed")
DOC_PATTERNS: dict[str, tuple[str, ...]] = {
    "signed_application": ("application", "loan app"),
    "guarantor_id": ("photo id", "driver license", "driver's license", "identity document"),
    "formation_certificate": ("certificate of formation", "articles of organization"),
    "ein_letter": ("ein letter", "irs ein", "cp 575", "147c"),
    "bank_statement": ("statement", "bank statement"),
    "financial_statements": ("financial statements", "profit & loss", "balance sheet"),
    "tax_return": ("tax return", "form 1120", "form 1065", "form 1120-s"),
    "tax_extension": ("form 7004", "extension"),
    "equipment_quote": ("quote", "quotation", "invoice"),
    "commitment_letter": ("commitment letter",),
    "credit_memo": ("credit memo",),
}
LABELS = {
    "legalName": "Legal business name",
    "ein": "Federal EIN",
    "businessAddress": "Business address",
    "revenueRaw": "Annual revenue (most recent year)",
    "amountRaw": "Amount requested",
    "product": "Product",
    "purpose": "Use of proceeds",
    "termRaw": "Requested term",
    "guarantor": "Name",
    "guarantorDob": "Guarantor date of birth",
    "industry": "Industry (NAICS)",
    "businessEstablished": "Date business established",
    "businessContact": "Business phone / email",
    "fundsNeededBy": "Funds needed by",
}


class WorkflowError(ValueError):
    def __init__(self, message: str, blockers: list[str] | None = None):
        super().__init__(message)
        self.blockers = blockers or []


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CENTRAL)


def _format_time(value: datetime) -> str:
    return value.astimezone(CENTRAL).isoformat(timespec="minutes")


def _amount(value: str | None) -> float | None:
    if not value or re.search(r"\b(?:approx(?:imately)?|about|around)\b|[\d,]\s*[-–]\s*[$\d]", value, re.I):
        return None
    match = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)\s*([kmb])?\b", value, re.I)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").casefold()
    return amount * {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]


def _extract_application(pages: list[tuple[Path, int, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    application_pages = [
        item for item in pages
        if _document_category(item[0], item[2]) == "signed_application"
    ]
    for path, number, text in application_pages:
        lines = [line.strip() for line in text.splitlines()]
        for field, label in LABELS.items():
            if field in result:
                continue
            for index, line in enumerate(lines):
                if re.sub(r"\s+", " ", line).casefold() == label.casefold():
                    candidate = next(
                        (part for part in lines[index + 1 :] if part and part != "\ufffd"),
                        "",
                    )
                    if candidate:
                        result[field] = {
                            "value": candidate,
                            "source": f"{path.as_posix()}:page {number}",
                        }
                    break
    for key in ("amountRaw", "revenueRaw", "termRaw"):
        if key in result:
            result[key]["numericValue"] = _amount(result[key]["value"])
    return result


def _credit_memo(packet: dict[str, Any]) -> str:
    app = packet["application"]

    def value(key: str) -> str:
        return app.get(key, {}).get("value", "Not extracted")

    checks = "; ".join(
        f"{check['code']}: {check['status']}"
        for check in packet["checks"]
    )
    missing = ", ".join(
        item["label"] for item in packet["requirements"]
        if item["status"] != "received"
    ) or "None identified"
    return (
        f"Borrower: {value('legalName')}. Request: {value('amountRaw')} for "
        f"{value('purpose')} ({value('product')}, {value('termRaw')}). "
        f"Guarantor: {value('guarantor')}. Extracted annual revenue: "
        f"{value('revenueRaw')}. Known-control check status: {checks}. "
        f"Outstanding documentation: {missing}. Human credit judgment and rationale "
        "must be entered by the assigned credit officer before committee approval."
    )


def _document_category(path: Path, text: str) -> str | None:
    filename = path.name.casefold()
    if "application" in filename or "loan app" in filename:
        return "signed_application"
    content = f"{path.name} {text[:1800]}".casefold()
    if any(token in content for token in ("driver license", "driver's license", "photo id", "state identification")):
        return "guarantor_id"
    if any(token in content for token in ("certificate of formation", "articles of organization")):
        return "formation_certificate"
    if any(token in content for token in ("ein assignment", "ein confirmation", "irs ein", "cp 575", "147c")):
        return "ein_letter"
    if "commitment letter" in content:
        return "commitment_letter"
    if "credit memo" in content:
        return "credit_memo"
    if "quote" in path.name.casefold() or re.search(r"\b(?:quote|quotation)\b", content):
        return "equipment_quote"
    if "invoice" in path.name.casefold() or re.search(r"\binvoice\b", content):
        return "equipment_quote"
    if re.search(r"\bstatement\b", content) and re.search(r"statement period", content):
        return "bank_statement"
    if "financial statements" in content or "profit & loss" in content:
        return "financial_statements"
    if re.search(r"\bform\s*7004\b|\bextension\b", content):
        return "tax_extension"
    if re.search(r"\b(?:form\s*(?:1120|1065)|tax return|income tax return)\b", content):
        return "tax_return"
    return None


def _load_documents(folder: Path, root: Path) -> tuple[list[dict[str, Any]], list[tuple[Path, int, str]]]:
    documents: list[dict[str, Any]] = []
    pages: list[tuple[Path, int, str]] = []
    for path in sorted(folder.rglob("*.pdf")):
        try:
            reader = PdfReader(str(path))
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                pages.append((path, page_number, text))
                category = _document_category(path, text)
                if category and not any(doc["path"] == _relative_source(path, root) for doc in documents):
                    documents.append({
                        "name": path.name,
                        "category": category,
                        "path": _relative_source(path, root),
                        "pages": len(reader.pages),
                        "legible": bool(text.strip()),
                    })
        except Exception as exc:
            raise RuntimeError(f"Unable to read application document {path}: {exc}") from exc
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
                legible = any(
                    any(value.strip() for value in row)
                    for row in reader
                )
                documents.append({
                    "name": path.name,
                    "category": "bank_statement",
                    "path": _relative_source(path, root),
                    "pages": 1,
                    "legible": legible,
                })
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise RuntimeError(f"Unable to read transaction export {path}: {exc}") from exc
    return documents, pages


def _required_documents(
    app: dict[str, Any],
    documents: list[dict[str, Any]],
    pages: list[tuple[Path, int, str]],
    clock_now: datetime,
) -> list[dict[str, Any]]:
    legible_categories = {
        document["category"] for document in documents if document["legible"]
    }
    requirements = [
        ("signed_application", "Signed loan application"),
        ("guarantor_id", "Government photo ID for each guarantor"),
        ("formation_certificate", "Certificate of formation"),
        ("ein_letter", "IRS EIN letter"),
        ("financial_statements", "Financial statements (P&L and balance sheet)"),
    ]
    try:
        established = datetime.fromisoformat(app.get("businessEstablished", {}).get("value", "")).date()
        younger_than_two_years = (clock_now.date() - established).days < 365 * 2
    except ValueError:
        younger_than_two_years = False
    requirements.append(
        ("tax_extension" if younger_than_two_years else "tax_return",
         "IRS extension (Form 7004)" if younger_than_two_years else "Most recent business tax return")
    )
    statement_months: set[str] = set()
    for _, _, text in pages:
        period = re.search(
            r"Statement period\s*\n\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
            text,
            re.I,
        )
        if period:
            try:
                start = datetime.fromisoformat(period.group(1)).date()
                end = datetime.fromisoformat(period.group(2)).date()
            except ValueError:
                continue
            if start.day == 1 and (end + timedelta(days=1)).day == 1:
                statement_months.add(end.strftime("%Y-%m"))
    month_names = {
        month.casefold(): index
        for index, month in enumerate(
            ("January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"),
            start=1,
        )
    }
    for document in documents:
        match = re.search(
            r"(?:^|[^A-Za-z])(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"[_-](20\d{2})\b",
            document["name"],
            re.I,
        )
        if match and document["legible"]:
            statement_months.add(f"{match.group(2)}-{month_names[match.group(1).casefold()]:02d}")
    expected_statement_months: set[str] = set()
    month_cursor = clock_now.date().replace(day=1)
    for _ in range(6):
        previous_month = month_cursor - timedelta(days=1)
        expected_statement_months.add(previous_month.strftime("%Y-%m"))
        month_cursor = previous_month.replace(day=1)
    recent_statement_months = statement_months & expected_statement_months
    requirements.append(
        ("bank_statement", f"Bank statements · {len(recent_statement_months)} of 6 recent full months present")
    )
    is_equipment = "equipment" in app.get("product", {}).get("value", "").casefold()
    if is_equipment:
        requirements.append(("equipment_quote", "Equipment vendor quote"))
    result: list[dict[str, Any]] = []
    for category, label in requirements:
        present = category in legible_categories
        if category == "bank_statement":
            present = len(recent_statement_months) == 6
        doc = next(
            (
                item for item in documents
                if item["category"] == category and item["legible"]
            ),
            None,
        )
        result.append({
            "key": category,
            "label": label,
            "status": "received" if present else "missing",
            "source": doc["path"] if doc else None,
            "monthCount": len(recent_statement_months) if category == "bank_statement" else None,
        })
    return result


def _known_fraud_checks(
    app: dict[str, Any],
    pages: list[tuple[Path, int, str]],
    historical_eins: dict[str, set[str]],
    root: Path,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ein = re.sub(r"\D", "", app.get("ein", {}).get("value", ""))
    legal_name = app.get("legalName", {}).get("value", "")
    dup_names = historical_eins.get(ein, set()) if ein else set()
    dup_names = {name for name in dup_names if normalize_supplier(name) != normalize_supplier(legal_name)}
    checks.append({
        "code": "DUP_EIN",
        "status": "flagged" if dup_names else "clear" if ein else "not_evaluable",
        "evidence": f"Historical EIN appears under: {', '.join(sorted(dup_names))}" if dup_names else "No different legal name with this EIN was found in the historical deal file." if ein else "Application EIN could not be extracted.",
    })
    address = app.get("businessAddress", {}).get("value", "")
    shell = bool(re.search(r"\b(?:PMB|private mailbox|mailbox store|UPS Store|PO Box|P\.O\. Box)\b", address, re.I))
    checks.append({
        "code": "SHELL_ADDRESS",
        "status": "flagged" if shell else "clear" if address else "not_evaluable",
        "evidence": f"Business address contains a mailbox indicator: {address}" if shell else "No mailbox indicator was found in the extracted business address." if address else "Business address could not be extracted.",
    })
    app_owner = app.get("guarantor", {}).get("value", "")
    app_dob = app.get("guarantorDob", {}).get("value", "")
    id_pages = [(path, number, text) for path, number, text in pages if _document_category(path, text) == "guarantor_id"]
    name_mismatch = bool(id_pages and app_owner and not any(
        normalize_supplier(app_owner) in normalize_supplier(text)
        for _, _, text in id_pages
    ))
    id_dobs = {
        match.group(1)
        for _, _, text in id_pages
        if (match := re.search(r"(?:date of birth|dob)\s*:?\s*(\d{4}-\d{2}-\d{2})", text, re.I))
    }
    dob_mismatch = bool(app_dob and id_dobs and app_dob not in id_dobs)
    mismatch = name_mismatch or dob_mismatch
    identity_comparable = bool(id_pages and app_owner)
    checks.append({
        "code": "ID_MISMATCH",
        "status": "flagged" if mismatch else "clear" if identity_comparable else "not_evaluable",
        "evidence": (
            "Extracted guarantor name was not found on the uploaded ID."
            if name_mismatch else
            "Application and ID birth dates differ."
            if dob_mismatch else
            "Guarantor name matched the uploaded ID; date of birth was not available on both documents for comparison."
            if identity_comparable and (not app_dob or not id_dobs) else
            "Guarantor name and available birth date matched."
            if identity_comparable else
            "Application guarantor name and legible ID are both required to evaluate this check."
        ),
    })
    revenue = app.get("revenueRaw", {}).get("numericValue")
    monthly_deposits: dict[str, float] = defaultdict(float)
    for _, _, text in pages:
        period = re.search(r"Statement period\s*\n\s*(\d{4}-\d{2})-\d{2}", text, re.I)
        deposits = re.search(r"Total deposits\s*&\s*credits\s*\n\s*\$?([\d,]+(?:\.\d{2})?)", text, re.I)
        if period and deposits:
            monthly_deposits[period.group(1)] += float(deposits.group(1).replace(",", ""))
    annualized = (
        sum(monthly_deposits.values()) / len(monthly_deposits) * 12
        if monthly_deposits else None
    )
    revenue_flagged = bool(revenue and annualized and revenue > annualized * 1.5)
    revenue_ratio = revenue / annualized if revenue is not None and annualized else None
    checks.append({
        "code": "REV_INFLATION",
        "status": "flagged" if revenue_flagged else "clear" if revenue is not None and annualized else "not_evaluable",
        "evidence": (
            f"Stated revenue ${revenue:,.0f}; annualized statement deposits "
            f"${annualized:,.0f}; ratio {revenue_ratio:.2f}x against the 1.50x threshold."
            if revenue is not None and annualized and revenue_ratio is not None else
            "Need numeric stated revenue and at least one extractable statement-deposit total."
        ),
    })
    statement_pages = [(path, number, text) for path, number, text in pages if _document_category(path, text) == "bank_statement"]
    tamper_evidence: list[str] = []
    statements: list[tuple[str, float, float, float, float, str]] = []
    for path, number, text in statement_pages:
        def money_after(label: str) -> float | None:
            match = re.search(label + r"\s*\n\s*\$?([\d,]+(?:\.\d{2})?)", text, re.I)
            return float(match.group(1).replace(",", "")) if match else None
        opening = money_after("Opening balance")
        deposits = money_after(r"Total deposits\s*&\s*credits")
        withdrawals = money_after(r"Total withdrawals\s*&\s*debits")
        closing = money_after("Closing balance")
        period = re.search(r"Statement period\s*\n\s*(\d{4}-\d{2})", text, re.I)
        if (
            opening is not None
            and deposits is not None
            and withdrawals is not None
            and closing is not None
            and period
        ):
            statements.append((period.group(1), opening, deposits, withdrawals, closing, _relative_source(path, root, number)))
            calculated_closing = opening + deposits - withdrawals
            discrepancy = abs(calculated_closing - closing)
            if discrepancy > 0.02:
                tamper_evidence.append(
                    f"{period.group(1)} reported closing balance ${closing:,.2f} differs "
                    f"from the calculated ${calculated_closing:,.2f} by "
                    f"${discrepancy:,.2f} ({_relative_source(path, root, number)})."
                )
    statements.sort()
    for previous, current in zip(statements, statements[1:]):
        if abs(previous[4] - current[1]) > 0.02:
            tamper_evidence.append(
                f"{current[0]} opening balance ${current[1]:,.2f} does not match "
                f"the prior reported closing balance ${previous[4]:,.2f} "
                f"({current[5]})."
            )
    checks.append({
        "code": "STMT_TAMPER",
        "status": "flagged" if tamper_evidence else "clear" if statements else "not_evaluable",
        "evidence": " ".join(tamper_evidence) if tamper_evidence else f"Summary balances reconcile on {len(statements)} extracted statements; transaction-level totals are not independently evaluated." if statements else "No statement summary balances could be extracted.",
    })
    return checks


class WorkflowStore:
    def __init__(
        self,
        data_root: Path,
        database_path: Path | None = None,
        clock_path: Path | None = None,
    ):
        self.root = data_root.resolve()
        self.participant = self.root / "participant" / "participant"
        self.shared = self.participant / "shared_folder"
        self.history = self.participant / "history"
        self.rules = json.loads((self.participant / "rules" / "stages.json").read_text(encoding="utf-8"))
        self.assignments = json.loads((self.participant / "sim" / "assignments.json").read_text(encoding="utf-8"))
        self.teams = json.loads((self.participant / "contacts" / "teams.json").read_text(encoding="utf-8"))
        self.people = {person["id"]: person for person in self.teams["people"]}
        self.database_path = database_path or Path(__file__).with_name("workflow.sqlite3")
        self.clock_path = clock_path or self.participant / "sim" / ".clock_state.json"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_deals (
                    id TEXT PRIMARY KEY,
                    folder TEXT NOT NULL UNIQUE,
                    borrower TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    stage_entered_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fraud_hold TEXT NOT NULL DEFAULT 'none',
                    fraud_review TEXT NOT NULL DEFAULT 'pending',
                    product_configured INTEGER NOT NULL DEFAULT 0,
                    vendor_verified INTEGER NOT NULL DEFAULT 0,
                    disbursement_verified INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deal_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deal_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(deal_id, stage, level)
                );
                CREATE TABLE IF NOT EXISTS workflow_check_resolutions (
                    deal_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    note TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(deal_id, code)
                );
                """
            )

    def now(self) -> datetime:
        if not self.clock_path.exists():
            return _parse_time(DEFAULT_SIM_TIME)
        state = json.loads(self.clock_path.read_text(encoding="utf-8"))
        return _parse_time(state["now"])

    def advance_clock(self, amount: str) -> str:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd])", amount.strip())
        if not match:
            raise WorkflowError("Clock increment must look like 30m, 6h, or 2d.")
        value = float(match.group(1))
        unit = match.group(2)
        delta = timedelta(minutes=value) if unit == "m" else timedelta(hours=value) if unit == "h" else timedelta(days=value)
        now = self.now() + delta
        self.clock_path.parent.mkdir(parents=True, exist_ok=True)
        state = json.loads(self.clock_path.read_text(encoding="utf-8")) if self.clock_path.exists() else {"seen_until": None}
        state["now"] = _format_time(now)
        self.clock_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.snapshot()
        return _format_time(now)

    def _packet_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.shared.is_dir():
            return rows
        for folder in sorted(path for path in self.shared.iterdir() if path.is_dir()):
            documents, pages = _load_documents(folder, self.root)
            if not pages:
                continue
            app = _extract_application(pages)
            borrower = app.get("legalName", {}).get("value") or _packet_name(
                folder.name,
                [Page(path, number, text) for path, number, text in pages],
            )
            rows.append({
                "id": "wf-" + re.sub(r"[^a-z0-9]+", "-", folder.name.casefold()).strip("-"),
                "folder": folder.name,
                "borrower": borrower,
                "application": app,
                "documents": documents,
                "pages": pages,
            })
        return rows

    def _historical_eins(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        path = self.history / "deals.csv"
        if not path.is_file():
            return result
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                ein = re.sub(r"\D", "", row.get("ein", ""))
                if ein and row.get("legal_name"):
                    result[ein].add(row["legal_name"])
        return result

    def _historical_sla(self) -> dict[str, Any]:
        path = self.history / "stage_events.csv"
        counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"events": 0, "breaches": 0}
        )
        total_events = 0
        total_breaches = 0
        if not path.is_file():
            return {
                "available": False,
                "source": _relative_source(path, self.root),
                "events": 0,
                "breaches": 0,
                "breachRate": None,
                "byStage": [],
            }
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                stage = row.get("stage", "")
                if stage not in STAGES:
                    continue
                breached = row.get("breached", "").casefold() in {"true", "1", "yes"}
                counts[stage]["events"] += 1
                counts[stage]["breaches"] += int(breached)
                total_events += 1
                total_breaches += int(breached)
        return {
            "available": True,
            "source": _relative_source(path, self.root),
            "events": total_events,
            "breaches": total_breaches,
            "breachRate": round(total_breaches / total_events, 4) if total_events else 0,
            "byStage": [
                {
                    "stage": stage,
                    **counts[stage],
                    "breachRate": round(counts[stage]["breaches"] / counts[stage]["events"], 4)
                    if counts[stage]["events"] else 0,
                }
                for stage in STAGES
                if counts[stage]["events"]
            ],
        }

    def _assignment(self, folder: str, stage: str, amount: float | None) -> tuple[str, str]:
        assignment = self.assignments.get(folder, {})
        if stage == "Intake":
            return ("olivia.chen", "Intake")
        if stage == "Credit":
            person = assignment.get("credit_officer", "aisha.grant")
            return self._active_person(person), "Credit"
        if stage == "Committee":
            person = "priscilla.nguyen" if (amount or 0) < 250000 else "harold.benson"
            return person, "Committee"
        if stage == "Product":
            return "kevin.alvarez", "Product"
        if stage == "Committed":
            return assignment.get("relationship_manager", "brian.foster"), "Relationship Management"
        if stage == "Closed":
            return "tasha.reed", "Closing"
        return "lauren.kim", "Lending Operations"

    def _active_person(self, person: str) -> str:
        record = self.people.get(person, {})
        note = record.get("notes", "")
        out = re.search(r"OUT OF OFFICE (\d{4}-\d\d-\d\d \d\d:\d\d) through (\d{4}-\d\d-\d\d \d\d:\d\d)", note)
        if out:
            now = self.now()
            start = _parse_time(out.group(1).replace(" ", "T") + "-06:00")
            end = _parse_time(out.group(2).replace(" ", "T") + "-06:00")
            if start <= now <= end:
                return record.get("backup") or person
        return person

    def _seed_and_refresh(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        now = _format_time(self.now())
        rows = self._packet_rows()
        historical_eins = self._historical_eins()
        for packet in rows:
            connection.execute(
                """INSERT OR IGNORE INTO workflow_deals
                   (id, folder, borrower, stage, stage_entered_at, status, updated_at)
                   VALUES (?, ?, ?, 'Intake', ?, 'in_progress', ?)""",
                (packet["id"], packet["folder"], packet["borrower"], now, now),
            )
            connection.execute(
                "UPDATE workflow_deals SET borrower=?, updated_at=? WHERE id=?",
                (packet["borrower"], now, packet["id"]),
            )
            packet["requirements"] = _required_documents(
                packet["application"], packet["documents"], packet["pages"], self.now()
            )
            packet["checks"] = _known_fraud_checks(
                packet["application"], packet["pages"], historical_eins, self.root
            )
            resolutions = {
                row["code"]: row
                for row in connection.execute(
                    "SELECT code, signature, note, actor FROM workflow_check_resolutions WHERE deal_id=?",
                    (packet["id"],),
                )
            }
            for check in packet["checks"]:
                resolution = resolutions.get(check["code"])
                signature = hashlib.sha256(
                    f"{check['status']}\0{check['evidence']}".encode("utf-8")
                ).hexdigest()
                if resolution and resolution["signature"] == signature:
                    check["status"] = "reviewed"
                    check["evidence"] += (
                        f" Human review recorded by {resolution['actor']}: "
                        f"{resolution['note']}"
                    )
            packet["creditMemo"] = _credit_memo(packet)
            packet["application"] = {
                key: value for key, value in packet["application"].items()
            }
        return rows

    def _blockers(
        self,
        stage: str,
        packet: dict[str, Any],
        deal: sqlite3.Row,
    ) -> list[str]:
        blockers: list[str] = []
        if stage == "Intake":
            intake_requirements = {
                "signed_application",
                "guarantor_id",
                "formation_certificate",
                "ein_letter",
            }
            blockers.extend(
                item["label"] for item in packet["requirements"]
                if item["key"] in intake_requirements
                if item["status"] != "received"
            )
        if stage == "Credit":
            credit_requirements = {
                "signed_application",
                "guarantor_id",
                "formation_certificate",
                "ein_letter",
                "bank_statement",
                "financial_statements",
                "tax_return",
                "tax_extension",
            }
            blockers.extend(
                item["label"] for item in packet["requirements"]
                if item["key"] in credit_requirements
                if item["status"] != "received"
            )
        if stage == "Credit":
            blockers.extend(
                f"Run or resolve known-fraud check {check['code']}"
                for check in packet["checks"]
                if check["status"] in ("flagged", "not_evaluable")
            )
        if stage == "Committee":
            if not packet.get("creditMemo"):
                blockers.append("System credit memo could not be generated.")
        if stage == "Product":
            if not deal["product_configured"]:
                blockers.append("Product and pricing must be confirmed.")
            if "equipment" in packet["application"].get("product", {}).get("value", "").casefold():
                if not any(doc["category"] == "equipment_quote" for doc in packet["documents"]):
                    blockers.append("Equipment vendor quote is missing.")
                if not deal["vendor_verified"]:
                    blockers.append("Vendor identity and remittance account need human verification.")
        if stage == "Committed":
            if not any(
                document["category"] == "commitment_letter" for document in packet["documents"]
            ):
                blockers.append("Signed commitment letter is missing.")
            if deal["fraud_hold"] != "none":
                blockers.append("Fraud hold must be resolved before closing.")
            if deal["fraud_review"] != "cleared":
                blockers.append("Fraud Operations must clear the final review before closing.")
        if stage == "Closed":
            if deal["fraud_hold"] != "none":
                blockers.append("Fraud hold is active or awaiting a Fraud Operations decision.")
            if deal["fraud_review"] != "cleared":
                blockers.append("Fraud Operations must clear the final fraud review.")
            if not deal["disbursement_verified"]:
                blockers.append("Disbursement account has not been verified.")
        return blockers

    def _maybe_alert(
        self,
        connection: sqlite3.Connection,
        deal: sqlite3.Row,
        packet: dict[str, Any],
        amount: float | None,
    ) -> None:
        if deal["status"] == "closed":
            return
        stage_rules = self.rules["stages"].get(deal["stage"], {})
        sla = stage_rules.get("sla_hours", 0)
        if deal["stage"] == "Committee" and (amount or 0) < 250000:
            sla = stage_rules.get("sla_hours_delegated", sla)
        if not sla:
            return
        elapsed = max(0.0, (self.now() - _parse_time(deal["stage_entered_at"])).total_seconds() / 3600)
        owner_id, _ = self._assignment(deal["folder"], deal["stage"], amount)
        assignment = self.assignments.get(deal["folder"], {})
        relationship_manager = self.people.get(
            assignment.get("relationship_manager", ""), {}
        ).get("name", "the assigned relationship manager")
        blockers = self._blockers(deal["stage"], packet, deal)
        if self.rules["stages"].get(deal["stage"], {}).get("human_gate"):
            blockers.append("authorized human decision")
        waiting = ", ".join(blockers) or deal["status"].replace("_", " ")
        unblock = "; ".join(blockers) or "complete the assigned stage review"
        levels = (
            (200, 3, "lauren.kim"),
            (150, 2, self.people.get(owner_id, {}).get("escalates_to") or "lauren.kim"),
            (100, 1, owner_id),
        )
        for threshold, level, target in levels:
            if elapsed < sla * threshold / 100:
                continue
            person = self.people.get(target, {"name": target})
            alert = (
                f"{deal['borrower']} · {deal['folder']} · RM {relationship_manager} "
                f"is in {deal['stage']} for {elapsed:.1f}h (SLA {sla}h). "
                f"Waiting for {waiting}. Unblock by: {unblock}."
            )
            connection.execute(
                """INSERT OR IGNORE INTO workflow_alerts
                   (deal_id, stage, level, message, owner, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (deal["id"], deal["stage"], level, alert, person.get("name", target), _format_time(self.now())),
            )

    def snapshot(self) -> dict[str, Any]:
        with self._connection() as connection:
            packets = self._seed_and_refresh(connection)
            packet_by_id = {packet["id"]: packet for packet in packets}
            deals = connection.execute("SELECT * FROM workflow_deals ORDER BY borrower").fetchall()
            output: list[dict[str, Any]] = []
            for deal in deals:
                packet = packet_by_id.get(deal["id"])
                if packet is None:
                    continue
                amount = packet["application"].get("amountRaw", {}).get("numericValue")
                self._maybe_alert(connection, deal, packet, amount)
                owner, team = self._assignment(deal["folder"], deal["stage"], amount)
                blockers = self._blockers(deal["stage"], packet, deal)
                events = connection.execute(
                    "SELECT event_type, detail, actor, created_at FROM workflow_events WHERE deal_id=? ORDER BY id DESC LIMIT 50",
                    (deal["id"],),
                ).fetchall()
                output.append({
                    "id": deal["id"],
                    "folder": deal["folder"],
                    "borrower": deal["borrower"],
                    "stage": deal["stage"],
                    "stageEnteredAt": deal["stage_entered_at"],
                    "status": deal["status"],
                    "owner": owner,
                    "ownerName": self.people.get(owner, {}).get("name", owner),
                    "team": team,
                    "relationshipManager": self.people.get(
                        self.assignments.get(deal["folder"], {}).get("relationship_manager", ""),
                        {},
                    ).get("name", ""),
                    "creditOfficer": self.people.get(
                        self.assignments.get(deal["folder"], {}).get("credit_officer", ""),
                        {},
                    ).get("name", ""),
                    "slaHours": self.rules["stages"].get(deal["stage"], {}).get("sla_hours"),
                    "hoursInStage": round(max(0.0, (self.now() - _parse_time(deal["stage_entered_at"])).total_seconds() / 3600), 1),
                    "blockers": blockers,
                    "fraudHold": deal["fraud_hold"],
                    "fraudReview": deal["fraud_review"],
                    "requiredDocuments": packet["requirements"],
                    "documents": packet["documents"],
                    "checks": packet["checks"],
                    "application": packet["application"],
                    "creditMemo": packet["creditMemo"],
                    "flags": [check["code"] for check in packet["checks"] if check["status"] == "flagged"],
                    "history": [dict(event) for event in events],
                    "productConfigured": bool(deal["product_configured"]),
                    "vendorVerified": bool(deal["vendor_verified"]),
                    "disbursementVerified": bool(deal["disbursement_verified"]),
                    "requiredActor": owner,
                })
            alerts = []
            for row in connection.execute(
                    "SELECT id, deal_id AS dealId, stage, level, message, owner, created_at AS createdAt, acknowledged FROM workflow_alerts ORDER BY id DESC"
            ):
                alert = dict(row)
                alert["ownerId"] = next(
                    (
                        person_id for person_id, person in self.people.items()
                        if person.get("name") == alert["owner"]
                    ),
                    "",
                )
                alert["deal_id"] = alert["dealId"]
                alerts.append(alert)
            pending = [
                {
                    "dealId": item["id"],
                    "borrower": item["borrower"],
                    "stage": item["stage"],
                    "owner": item["owner"],
                    "ownerName": item["ownerName"],
                    "status": item["status"],
                }
                for item in output
                if self.rules["stages"].get(item["stage"], {}).get("human_gate")
                and not item["blockers"]
                and item["status"] in ("in_progress", "awaiting_decision")
            ]
            return {
                "now": _format_time(self.now()),
                "pipeline": list(STAGES),
                "loans": output,
                "alerts": alerts,
                "pendingDecisions": pending,
                "historicalSla": self._historical_sla(),
            }

    def _record(
        self, connection: sqlite3.Connection, deal_id: str, event: str,
        detail: str, actor: str,
    ) -> None:
        connection.execute(
            "INSERT INTO workflow_events (deal_id, event_type, detail, actor, created_at) VALUES (?, ?, ?, ?, ?)",
            (deal_id, event, detail, actor, _format_time(self.now())),
        )

    def _advance_stage(
        self,
        connection: sqlite3.Connection,
        deal: sqlite3.Row,
        packet: dict[str, Any],
        actor: str,
    ) -> str:
        blockers = self._blockers(deal["stage"], packet, deal)
        if blockers:
            raise WorkflowError("This application cannot advance yet.", blockers)
        index = STAGES.index(deal["stage"])
        if index >= len(STAGES) - 1:
            raise WorkflowError("Application is already closed.")
        next_stage = STAGES[index + 1]
        now = _format_time(self.now())
        connection.execute(
            "UPDATE workflow_deals SET stage=?, stage_entered_at=?, status='in_progress', updated_at=? WHERE id=?",
            (next_stage, now, now, deal["id"]),
        )
        self._record(connection, deal["id"], "stage_advanced", f"Moved from {deal['stage']} to {next_stage}.", actor)
        return next_stage

    def act(self, deal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", ""))
        note = str(payload.get("note", "")).strip()
        actor = str(payload.get("actor", "")).strip()
        if not actor:
            raise WorkflowError("A reviewer identity is required.")
        with self._connection() as connection:
            packets = self._seed_and_refresh(connection)
            packet = next((row for row in packets if row["id"] == deal_id), None)
            if not packet:
                raise WorkflowError(f"Application {deal_id} was not found.")
            deal = connection.execute("SELECT * FROM workflow_deals WHERE id=?", (deal_id,)).fetchone()
            if not deal:
                raise WorkflowError(f"Workflow state for {deal_id} was not found.")
            if deal["status"] == "closed" and action != "acknowledge_alert":
                raise WorkflowError("Funds have already been released for this application.")
            amount = packet["application"].get("amountRaw", {}).get("numericValue")
            owner, _ = self._assignment(deal["folder"], deal["stage"], amount)
            fraud_ops = {"victor.hale", "monica.salas"}

            if action == "advance":
                gate = self.rules["stages"].get(deal["stage"], {}).get("human_gate")
                if gate:
                    raise WorkflowError(f"{deal['stage']} requires an authorized human decision before it can advance.")
                if actor != owner:
                    raise WorkflowError(f"Only the assigned stage owner ({self.people.get(owner, {}).get('name', owner)}) can advance this stage.")
                self._advance_stage(connection, deal, packet, actor)
            elif action == "request_info":
                if actor != owner:
                    raise WorkflowError(f"Only the assigned stage owner ({self.people.get(owner, {}).get('name', owner)}) can request information.")
                if not note:
                    raise WorkflowError("Specify the information needed from the borrower.")
                connection.execute("UPDATE workflow_deals SET status='awaiting_information', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "information_requested", note, actor)
            elif action in {"approve", "decline"}:
                gate = self.rules["stages"].get(deal["stage"], {}).get("human_gate")
                if not gate:
                    raise WorkflowError(f"{deal['stage']} has no approval gate.")
                if actor != owner:
                    raise WorkflowError(f"This decision must be recorded by {self.people.get(owner, {}).get('name', owner)}.")
                if action == "decline":
                    if not note:
                        raise WorkflowError("A reason is required to decline an application.")
                    connection.execute("UPDATE workflow_deals SET status='declined', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                    self._record(connection, deal_id, "declined", note, actor)
                else:
                    if not note:
                        raise WorkflowError("A rationale is required to approve an application.")
                    self._record(connection, deal_id, "approved", note, actor)
                    self._advance_stage(connection, deal, packet, actor)
            elif action == "request_hold":
                if actor != owner and actor not in fraud_ops:
                    raise WorkflowError("Only the assigned stage owner or Fraud Operations can request a hold.")
                if not note:
                    raise WorkflowError("Supporting evidence and rationale are required to request a fraud hold.")
                connection.execute("UPDATE workflow_deals SET fraud_hold='requested', status='hold_requested', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "fraud_hold_requested", note, actor)
            elif action == "escalate":
                if actor != owner:
                    raise WorkflowError("Only the assigned stage owner can escalate this application.")
                if not note:
                    raise WorkflowError("A reason is required to escalate this application.")
                escalation_owner = self.people.get(owner, {}).get("escalates_to") or "lauren.kim"
                target = self.people.get(escalation_owner, {}).get("name", escalation_owner)
                connection.execute("UPDATE workflow_deals SET status='escalated', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "escalated", f"Escalated to {target}: {note}", actor)
            elif action == "mark_reviewed":
                if actor != owner and actor not in fraud_ops:
                    raise WorkflowError("Only the assigned stage owner or Fraud Operations can record a review.")
                self._record(connection, deal_id, "review_recorded", note or "Review acknowledged; no fraud finding recorded.", actor)
            elif action == "resolve_check":
                if deal["stage"] != "Credit" or actor != owner:
                    raise WorkflowError("Only the assigned credit officer can resolve a known-rule check at the Credit stage.")
                code = str(payload.get("checkCode", "")).strip()
                check = next((item for item in packet["checks"] if item["code"] == code), None)
                if not check or check["status"] not in ("flagged", "not_evaluable"):
                    raise WorkflowError("Select a flagged or unevaluable check for human review.")
                if not note:
                    raise WorkflowError("A rationale is required to record a human check resolution.")
                signature = hashlib.sha256(
                    f"{check['status']}\0{check['evidence']}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """INSERT INTO workflow_check_resolutions
                       (deal_id, code, signature, note, actor, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(deal_id, code) DO UPDATE SET
                           signature=excluded.signature, note=excluded.note,
                           actor=excluded.actor, created_at=excluded.created_at""",
                    (deal_id, code, signature, note, actor, _format_time(self.now())),
                )
                self._record(connection, deal_id, "known_check_reviewed", f"{code}: {note}", actor)
            elif action in {"place_hold", "release_hold", "clear_fraud"}:
                if actor not in fraud_ops:
                    raise WorkflowError("Only Fraud Operations can place, release, or clear a fraud hold.")
                if action == "place_hold":
                    if deal["fraud_hold"] != "requested":
                        raise WorkflowError("A fraud hold must be requested before it can be placed.")
                    if not note:
                        raise WorkflowError("A reason is required to place a fraud hold.")
                    connection.execute("UPDATE workflow_deals SET fraud_hold='placed', status='fraud_hold', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                    self._record(connection, deal_id, "fraud_hold_placed", note, actor)
                elif action == "release_hold":
                    if deal["fraud_hold"] != "placed":
                        raise WorkflowError("There is no placed fraud hold to release.")
                    if not note:
                        raise WorkflowError("A reason is required to release a fraud hold.")
                    connection.execute("UPDATE workflow_deals SET fraud_hold='none', fraud_review='cleared', status='in_progress', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                    self._record(connection, deal_id, "fraud_hold_released", note, actor)
                else:
                    if deal["fraud_hold"] == "placed":
                        raise WorkflowError("Release the placed fraud hold before clearing review.")
                    if not note:
                        raise WorkflowError("A review explanation is required.")
                    connection.execute("UPDATE workflow_deals SET fraud_hold='none', fraud_review='cleared', status='in_progress', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                    self._record(connection, deal_id, "fraud_review_cleared", note, actor)
            elif action == "configure_product":
                if deal["stage"] != "Product" or actor != owner:
                    raise WorkflowError("Only the assigned Product owner can configure product terms at the Product stage.")
                if not payload.get("product") or not payload.get("pricing"):
                    raise WorkflowError("Product and pricing terms are required.")
                connection.execute("UPDATE workflow_deals SET product_configured=1, updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "product_configured", f"{payload['product']} · {payload['pricing']}", actor)
            elif action == "verify_vendor":
                if deal["stage"] != "Product" or actor != owner:
                    raise WorkflowError("Only the assigned Product owner can verify the vendor.")
                if not note:
                    raise WorkflowError("Record how the vendor and remittance account were independently verified.")
                connection.execute("UPDATE workflow_deals SET vendor_verified=1, updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "vendor_verified", note, actor)
            elif action == "verify_disbursement":
                if deal["stage"] != "Closed" or actor != owner:
                    raise WorkflowError("Only the assigned Closing officer can verify the disbursement account.")
                if not note:
                    raise WorkflowError("Record how the disbursement account was verified.")
                connection.execute("UPDATE workflow_deals SET disbursement_verified=1, updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "disbursement_verified", note, actor)
            elif action == "release_funds":
                if deal["stage"] != "Closed" or actor != owner:
                    raise WorkflowError("Only the assigned Closing & Funding Officer can release funds.")
                if not note:
                    raise WorkflowError("A funding-release rationale is required.")
                blockers = self._blockers("Closed", packet, deal)
                if blockers:
                    raise WorkflowError("Funds cannot be released.", blockers)
                connection.execute("UPDATE workflow_deals SET status='closed', updated_at=? WHERE id=?", (_format_time(self.now()), deal_id))
                self._record(connection, deal_id, "funds_released", note, actor)
            elif action == "acknowledge_alert":
                alert_id = int(payload.get("alertId", 0))
                alert_row = connection.execute(
                    "SELECT owner FROM workflow_alerts WHERE id=? AND deal_id=?",
                    (alert_id, deal_id),
                ).fetchone()
                if not alert_row:
                    raise WorkflowError("The SLA alert was not found for this application.")
                alert_owner = next(
                    (
                        person_id for person_id, person in self.people.items()
                        if person.get("name") == alert_row["owner"]
                    ),
                    "",
                )
                if actor not in {alert_owner, owner, *fraud_ops}:
                    raise WorkflowError("Only the alert recipient, stage owner, or Fraud Operations can acknowledge this alert.")
                connection.execute("UPDATE workflow_alerts SET acknowledged=1 WHERE id=? AND deal_id=?", (alert_id, deal_id))
                self._record(connection, deal_id, "alert_acknowledged", note or "SLA alert acknowledged.", actor)
            else:
                raise WorkflowError(f"Unsupported workflow action: {action}")
        return self.snapshot()
