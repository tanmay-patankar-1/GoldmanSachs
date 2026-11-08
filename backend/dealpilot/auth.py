from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROLES = ("Admin", "Loan Officer", "Underwriter", "Fraud Investigator", "Auditor")
PASSWORD_ITERATIONS = 310_000
SESSION_HOURS = 8
ROLE_PERMISSIONS = {
    "Admin": {"overview", "loans.read", "documents.read", "workflow.write", "fraud.read", "audit.read", "users.manage"},
    "Loan Officer": {"overview", "loans.read", "workflow.write"},
    "Underwriter": {"overview", "loans.read", "documents.read", "workflow.write"},
    "Fraud Investigator": {"overview", "fraud.read", "fraud.write"},
    "Auditor": {"audit.read"},
}


class AuthenticationError(ValueError):
    pass


def _password_hash(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return salt, derived


class AuthStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    employee_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id),
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    attempt_key TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS login_attempts_key_time
                    ON login_attempts(attempt_key, attempted_at);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER,
                    actor_email TEXT,
                    event TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    ip_address TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
        self.database_path.chmod(0o600)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def bootstrap_admin(self, email: str | None, password: str | None) -> None:
        with self._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            if count:
                return
        if not email or not password:
            raise RuntimeError(
                "No employee accounts exist. Set DEALPILOT_BOOTSTRAP_ADMIN_EMAIL "
                "and DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD to create the first Admin."
            )
        self.create_employee(
            email=email,
            employee_id=email.partition("@")[0],
            name=email.partition("@")[0].replace(".", " ").title(),
            role="Admin",
            password=password,
            bootstrap=True,
        )

    def create_employee(
        self,
        email: str,
        employee_id: str,
        name: str,
        role: str,
        password: str,
        bootstrap: bool = False,
    ) -> dict[str, Any]:
        email = email.strip().lower()
        employee_id = employee_id.strip().lower()
        name = name.strip()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise AuthenticationError("Enter a valid company email address.")
        if not employee_id or len(employee_id) > 80:
            raise AuthenticationError("Enter a valid employee ID.")
        if not name or len(name) > 120:
            raise AuthenticationError("Enter the employee's name.")
        if role not in ROLES:
            raise AuthenticationError("Select a valid employee role.")
        if not bootstrap and len(password) < 12:
            raise AuthenticationError("Passwords must be at least 12 characters.")
        if len(password) > 1024:
            raise AuthenticationError("Password is too long.")
        salt, password_hash = _password_hash(password)
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """INSERT INTO employees
                    (email, employee_id, name, role, salt, password_hash)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (email, employee_id, name, role, salt, password_hash),
                )
                employee = connection.execute(
                    "SELECT * FROM employees WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError("That email or employee ID is already in use.") from exc
        return self._public_employee(employee)

    def authenticate(
        self, email: str, password: str, ip_address: str
    ) -> tuple[str, dict[str, Any]]:
        email_key = email.strip().lower()
        attempt_key = hashlib.sha256(f"{ip_address}|{email_key}".encode()).hexdigest()
        now = int(time.time())
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM login_attempts WHERE attempted_at < ?", (now - 900,)
            )
            attempts = connection.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE attempt_key=? AND attempted_at>=?",
                (attempt_key, now - 900),
            ).fetchone()[0]
            employee = connection.execute(
                "SELECT * FROM employees WHERE email=? AND active=1", (email_key,)
            ).fetchone()
            salt = employee["salt"] if employee else bytes(16)
            actual_hash = employee["password_hash"] if employee else bytes(32)
            _, candidate = _password_hash(password, salt)
            valid = hmac.compare_digest(candidate, actual_hash)
            if attempts >= 5 or not employee or not valid:
                connection.execute(
                    "INSERT INTO login_attempts (attempt_key, attempted_at) VALUES (?, ?)",
                    (attempt_key, now),
                )
                self._audit(
                    connection, None, email_key, "login_failed", "session", "", ip_address
                )
                employee = None
            else:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                connection.execute(
                    "INSERT INTO sessions (token_hash, employee_id, expires_at) VALUES (?, ?, ?)",
                    (token_hash, employee["id"], now + SESSION_HOURS * 3600),
                )
                connection.execute(
                    "DELETE FROM login_attempts WHERE attempt_key=?", (attempt_key,)
                )
                self._audit(
                    connection,
                    employee["id"],
                    employee["email"],
                    "login_succeeded",
                    "session",
                    "",
                    ip_address,
                )
                public_employee = self._public_employee(employee)
        if not employee:
            raise AuthenticationError("Invalid email or password.")
        return token, public_employee

    def employee_for_token(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._connection() as connection:
            row = connection.execute(
                """SELECT e.* FROM sessions s JOIN employees e ON e.id=s.employee_id
                WHERE s.token_hash=? AND s.expires_at>? AND e.active=1""",
                (token_hash, int(time.time())),
            ).fetchone()
            return self._public_employee(row) if row else None

    def logout(self, token: str | None, employee: dict[str, Any] | None, ip: str) -> None:
        with self._connection() as connection:
            if token:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                )
            if employee:
                self._audit(
                    connection, employee["id"], employee["email"], "logout", "session", "", ip
                )

    def record(
        self,
        employee: dict[str, Any] | None,
        event: str,
        resource: str,
        details: str,
        ip: str,
    ) -> None:
        with self._connection() as connection:
            self._audit(
                connection,
                employee["id"] if employee else None,
                employee["email"] if employee else None,
                event,
                resource,
                details,
                ip,
            )

    def list_employees(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [
                self._public_employee(row)
                for row in connection.execute(
                    "SELECT * FROM employees ORDER BY name COLLATE NOCASE"
                )
            ]

    def update_employee(
        self, employee_id: int, role: str | None, active: bool | None, password: str | None
    ) -> dict[str, Any]:
        if role is not None and role not in ROLES:
            raise AuthenticationError("Select a valid employee role.")
        if password is not None and not 12 <= len(password) <= 1024:
            raise AuthenticationError("Passwords must be at least 12 characters.")
        with self._connection() as connection:
            employee = connection.execute(
                "SELECT * FROM employees WHERE id=?", (employee_id,)
            ).fetchone()
            if not employee:
                raise AuthenticationError("Employee not found.")
            next_role = role or employee["role"]
            next_active = employee["active"] if active is None else int(active)
            if employee["role"] == "Admin" and employee["active"] and (
                next_role != "Admin" or not next_active
            ):
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM employees WHERE role='Admin' AND active=1 AND id!=?",
                    (employee_id,),
                ).fetchone()[0]
                if not remaining:
                    raise AuthenticationError("At least one active Admin must remain.")
            fields = ["role=?", "active=?", "updated_at=datetime('now')"]
            values: list[Any] = [next_role, next_active]
            if password is not None:
                salt, password_hash = _password_hash(password)
                fields.extend(["salt=?", "password_hash=?"])
                values.extend([salt, password_hash])
            if password is not None or next_role != employee["role"] or not next_active:
                connection.execute(
                    "DELETE FROM sessions WHERE employee_id=?", (employee_id,)
                )
            values.append(employee_id)
            connection.execute(
                f"UPDATE employees SET {', '.join(fields)} WHERE id=?", values
            )
            updated = connection.execute(
                "SELECT * FROM employees WHERE id=?", (employee_id,)
            ).fetchone()
            if not updated["active"]:
                connection.execute("DELETE FROM sessions WHERE employee_id=?", (employee_id,))
            return self._public_employee(updated)

    def audit_events(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT a.id, a.actor_email AS actor, a.event, a.resource, a.details,
                a.ip_address AS ipAddress, a.created_at AS createdAt
                FROM audit_events a ORDER BY a.id DESC LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in rows]

    @staticmethod
    def _public_employee(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "email": row["email"],
            "employeeId": row["employee_id"],
            "name": row["name"],
            "role": row["role"],
            "active": bool(row["active"]),
        }

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        employee_id: int | None,
        email: str | None,
        event: str,
        resource: str,
        details: str,
        ip: str,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events
            (employee_id, actor_email, event, resource, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (employee_id, email, event, resource, details[:1000], ip[:100]),
        )


def has_permission(employee: dict[str, Any], permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(employee["role"], set())


def bootstrap_values() -> tuple[str | None, str | None]:
    email = os.environ.get("DEALPILOT_BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD")
    if password is not None and len(password) < 12:
        raise RuntimeError("DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.")
    return email, password
