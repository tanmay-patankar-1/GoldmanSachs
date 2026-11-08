from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from dealpilot.auth import (
    ROLES,
    AuthStore,
    AuthenticationError,
    bootstrap_values,
    has_permission,
)
from dealpilot.engine import analyze
from dealpilot.workflow import WorkflowError, WorkflowStore


ROOT = Path(__file__).resolve().parents[1]
HOST = os.environ.get("DEALPILOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEALPILOT_PORT", "8765"))
COOKIE_NAME = "dealpilot_session"
COOKIE_SECURE = os.environ.get(
    "DEALPILOT_COOKIE_SECURE",
    "false" if HOST in {"127.0.0.1", "localhost", "::1"} else "true",
).lower() in {"1", "true", "yes"}
_cache_lock = threading.Lock()
_cached_signature: tuple[tuple[str, int, int], ...] | None = None
_cached_analysis: dict[str, Any] | None = None
_workflow_store = WorkflowStore(ROOT)
_auth_store = AuthStore(
    Path(os.environ.get("DEALPILOT_AUTH_DB", Path(__file__).with_name("auth.sqlite3")))
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    email, password = bootstrap_values()
    _auth_store.bootstrap_admin(email, password)
    yield


app = FastAPI(
    title="Verifin API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class EmployeeCreateRequest(BaseModel):
    email: str = Field(max_length=320)
    employeeId: str = Field(max_length=80)
    name: str = Field(max_length=120)
    role: str
    password: str = Field(max_length=1024)


class EmployeeUpdateRequest(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=1024)


@app.middleware("http")
async def protect_api_requests(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 1_000_000:
                return JSONResponse({"detail": "Request body must be smaller than 1 MB."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length."}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def _signature() -> tuple[tuple[str, int, int], ...]:
    sources = [
        ROOT / "participant" / "participant" / "history" / "deals.csv",
        ROOT / "participant" / "participant" / "history" / "monthly_deposits.csv",
    ]
    shared = ROOT / "participant" / "participant" / "shared_folder"
    if shared.is_dir():
        sources.extend(shared.rglob("*.pdf"))
        sources.extend(shared.rglob("*.csv"))
    return tuple(
        sorted(
            (path.relative_to(ROOT).as_posix(), path.stat().st_mtime_ns, path.stat().st_size)
            for path in sources
            if path.is_file()
        )
    )


def _get_analysis(force: bool = False) -> dict[str, Any]:
    global _cached_analysis, _cached_signature
    with _cache_lock:
        signature = _signature()
        if force or _cached_analysis is None or signature != _cached_signature:
            _cached_analysis = analyze(ROOT)
            _cached_signature = signature
        return _cached_analysis


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def _current_employee(request: Request) -> dict[str, Any]:
    employee = _auth_store.employee_for_token(_token(request))
    if not employee:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return employee


def _require(permission: str):
    def dependency(
        request: Request,
        employee: dict[str, Any] = Depends(_current_employee),
    ) -> dict[str, Any]:
        if not has_permission(employee, permission):
            _auth_store.record(
                employee,
                "access_denied",
                request.url.path,
                f"Required permission: {permission}",
                _ip(request),
            )
            raise HTTPException(status_code=403, detail="You are not authorized to access this resource.")
        return employee
    return dependency


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    try:
        token, employee = _auth_store.authenticate(payload.email, payload.password, _ip(request))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=8 * 60 * 60,
        path="/",
    )
    return {"employee": employee}


@app.get("/api/auth/me")
def current_employee(employee: dict[str, Any] = Depends(_current_employee)) -> dict[str, Any]:
    return {"employee": employee}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    employee = _auth_store.employee_for_token(_token(request))
    _auth_store.logout(_token(request), employee, _ip(request))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=COOKIE_SECURE, samesite="strict")
    return {"status": "ok"}


@app.get("/api/admin/users")
def list_users(employee: dict[str, Any] = Depends(_require("users.manage"))) -> dict[str, Any]:
    return {"roles": ROLES, "users": _auth_store.list_employees()}


@app.post("/api/admin/users", status_code=201)
def create_user(
    payload: EmployeeCreateRequest,
    request: Request,
    employee: dict[str, Any] = Depends(_require("users.manage")),
) -> dict[str, Any]:
    try:
        created = _auth_store.create_employee(
            payload.email, payload.employeeId, payload.name, payload.role, payload.password
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _auth_store.record(employee, "employee_created", created["email"], created["role"], _ip(request))
    return {"user": created}


@app.patch("/api/admin/users/{user_id}")
def update_user(
    user_id: int,
    payload: EmployeeUpdateRequest,
    request: Request,
    employee: dict[str, Any] = Depends(_require("users.manage")),
) -> dict[str, Any]:
    if user_id == employee["id"] and (
        payload.active is False
        or (payload.role is not None and payload.role != employee["role"])
    ):
        raise HTTPException(status_code=400, detail="You cannot disable or change the role of your own account.")
    try:
        updated = _auth_store.update_employee(
            user_id, payload.role, payload.active, payload.password
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    changes = []
    if payload.role is not None:
        changes.append(f"role={payload.role}")
    if payload.active is not None:
        changes.append(f"active={payload.active}")
    if payload.password is not None:
        changes.append("password reset")
    _auth_store.record(
        employee,
        "employee_updated",
        updated["email"],
        ", ".join(changes) or "No changes.",
        _ip(request),
    )
    return {"user": updated}


@app.get("/api/audit")
def audit_events(
    request: Request,
    employee: dict[str, Any] = Depends(_require("audit.read")),
) -> dict[str, Any]:
    _auth_store.record(employee, "audit_log_accessed", "audit", "", _ip(request))
    return {"events": _auth_store.audit_events()}


@app.get("/api/history")
def historical_records(
    request: Request,
    employee: dict[str, Any] = Depends(_require("audit.read")),
) -> dict[str, Any]:
    path = ROOT / "participant" / "participant" / "history" / "deals.csv"
    try:
        with path.open(newline="", encoding="utf-8-sig") as source:
            records = [
                {
                    "dealId": row.get("deal_id", ""),
                    "applicationDate": row.get("application_date", ""),
                    "business": row.get("legal_name", ""),
                    "industry": row.get("industry", ""),
                    "amount": row.get("amount_requested", ""),
                    "product": row.get("product", ""),
                    "status": row.get("final_status", ""),
                    "repaymentStatus": row.get("repayment_status", ""),
                }
                for row in csv.DictReader(source)
            ]
        _auth_store.record(employee, "historical_records_accessed", "historical-records", "", _ip(request))
        return {"records": records}
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        print(f"Historical record request failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Historical records are unavailable.") from exc


@app.get("/api/analysis")
def analysis(
    request: Request,
    employee: dict[str, Any] = Depends(_require("fraud.read")),
) -> dict[str, Any]:
    _auth_store.record(employee, "fraud_analysis_accessed", "fraud-analysis", "", _ip(request))
    try:
        return _get_analysis()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Analysis request failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Analysis service is unavailable.") from exc


@app.post("/api/reload")
def reload_analysis(
    request: Request,
    employee: dict[str, Any] = Depends(_require("fraud.read")),
) -> dict[str, Any]:
    _auth_store.record(employee, "fraud_analysis_refreshed", "fraud-analysis", "", _ip(request))
    try:
        return _get_analysis(force=True)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Analysis refresh failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Analysis service is unavailable.") from exc


@app.get("/api/workflow")
def workflow_snapshot(
    request: Request,
    employee: dict[str, Any] = Depends(_current_employee),
) -> dict[str, Any]:
    if not has_permission(employee, "loans.read") and not has_permission(employee, "fraud.read"):
        _auth_store.record(employee, "access_denied", request.url.path, "Required permission: loans.read or fraud.read", _ip(request))
        raise HTTPException(status_code=403, detail="You are not authorized to access this resource.")
    _auth_store.record(employee, "portfolio_accessed", "portfolio", "", _ip(request))
    try:
        snapshot = _workflow_store.snapshot()
        if employee["role"] == "Fraud Investigator":
            for loan in snapshot["loans"]:
                loan["application"] = {}
                loan["documents"] = []
                loan["requiredDocuments"] = []
                loan["creditMemo"] = ""
                loan["history"] = []
        return snapshot
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Workflow request failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Workflow service is unavailable.") from exc


@app.get("/api/workflow/{deal_id}")
def workflow_loan(
    deal_id: str,
    request: Request,
    employee: dict[str, Any] = Depends(_require("loans.read")),
) -> dict[str, Any]:
    try:
        snapshot = _workflow_store.snapshot()
        loan = next((item for item in snapshot["loans"] if item["id"] == unquote(deal_id)), None)
        if not loan:
            raise HTTPException(status_code=404, detail="Application not found.")
        _auth_store.record(
            employee, "application_accessed", f"application:{loan['id']}", loan["borrower"], _ip(request)
        )
        return {"loan": loan}
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Application request failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Workflow service is unavailable.") from exc


@app.get("/api/workflow/{deal_id}/documents/{filename}")
def download_document(
    deal_id: str,
    filename: str,
    request: Request,
    employee: dict[str, Any] = Depends(_require("documents.read")),
) -> FileResponse:
    if Path(filename).name != filename or filename in {".", ".."}:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        snapshot = _workflow_store.snapshot()
        loan = next((item for item in snapshot["loans"] if item["id"] == unquote(deal_id)), None)
        if not loan:
            raise HTTPException(status_code=404, detail="Application not found.")
        document = next((item for item in loan["documents"] if Path(item["path"]).name == filename), None)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found.")
        shared_root = (ROOT / "participant" / "participant" / "shared_folder").resolve()
        path = (ROOT / document["path"]).resolve()
        if shared_root not in path.parents or not path.is_file() or path.suffix.lower() != ".pdf":
            raise HTTPException(status_code=404, detail="Document not found.")
        _auth_store.record(
            employee,
            "document_downloaded",
            f"application:{loan['id']}/document:{filename}",
            loan["borrower"],
            _ip(request),
        )
        return FileResponse(path, media_type="application/pdf", filename=filename)
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Document download failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Document service is unavailable.") from exc


@app.post("/api/workflow/advance-clock")
def advance_clock(
    payload: dict[str, Any],
    request: Request,
    employee: dict[str, Any] = Depends(_require("users.manage")),
) -> dict[str, Any]:
    try:
        now = _workflow_store.advance_clock(str(payload.get("by", "")))
        _auth_store.record(employee, "simulation_clock_advanced", "workflow", str(payload.get("by", "")), _ip(request))
        return _workflow_store.snapshot() | {"now": now}
    except WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Clock update failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Workflow service is unavailable.") from exc


@app.post("/api/workflow/{deal_id}/actions")
def workflow_action(
    deal_id: str,
    payload: dict[str, Any],
    request: Request,
    employee: dict[str, Any] = Depends(_current_employee),
) -> dict[str, Any]:
    action = str(payload.get("action", ""))
    permitted = {
        "Admin": {"*"},
        "Loan Officer": {
            "request_info", "request_hold", "escalate", "mark_reviewed", "acknowledge_alert",
        },
        "Underwriter": {
            "request_info", "approve", "decline", "escalate", "mark_reviewed",
            "resolve_check", "configure_product", "verify_vendor", "verify_disbursement",
            "release_funds", "acknowledge_alert",
        },
        "Fraud Investigator": {
            "request_hold", "place_hold", "release_hold", "clear_fraud", "mark_reviewed",
            "resolve_check", "acknowledge_alert",
        },
        "Auditor": set(),
    }
    if (
        "*" not in permitted.get(employee["role"], set())
        and action not in permitted.get(employee["role"], set())
    ):
        _auth_store.record(
            employee, "access_denied", f"application:{deal_id}", f"Workflow action: {action}", _ip(request)
        )
        raise HTTPException(status_code=403, detail="You are not authorized to perform this workflow action.")
    trusted_payload = dict(payload)
    trusted_payload["actor"] = employee["employeeId"]
    try:
        result = _workflow_store.act(unquote(deal_id), trusted_payload)
        _auth_store.record(
            employee, "workflow_action", f"application:{deal_id}", action, _ip(request)
        )
        return result
    except WorkflowError as exc:
        _auth_store.record(
            employee, "workflow_action_rejected", f"application:{deal_id}", action, _ip(request)
        )
        raise HTTPException(status_code=409, detail=str(exc), headers={"X-Workflow-Blockers": "|".join(exc.blockers)}) from exc
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Workflow action failed: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Workflow service is unavailable.") from exc


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
