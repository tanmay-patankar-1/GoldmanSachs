from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dealpilot.auth import AuthStore, AuthenticationError, has_permission


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.temporary_directory.name) / "auth.sqlite3")
        self.admin = self.store.create_employee(
            "admin@example.test",
            "admin",
            "Admin User",
            "Admin",
            "Admin-Password-For-Test-Only",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_password_authentication_creates_and_revokes_session(self) -> None:
        token, employee = self.store.authenticate(
            "ADMIN@example.test", "Admin-Password-For-Test-Only", "127.0.0.1"
        )
        self.assertEqual(employee["id"], self.admin["id"])
        self.assertEqual(self.store.employee_for_token(token)["email"], "admin@example.test")
        self.store.logout(token, employee, "127.0.0.1")
        self.assertIsNone(self.store.employee_for_token(token))

    def test_failed_password_is_audited(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.store.authenticate("admin@example.test", "wrong password", "127.0.0.1")
        events = self.store.audit_events()
        self.assertEqual(events[0]["event"], "login_failed")
        self.assertEqual(events[0]["actor"], "admin@example.test")

    def test_login_throttles_repeated_invalid_attempts(self) -> None:
        for _ in range(6):
            with self.assertRaises(AuthenticationError):
                self.store.authenticate("admin@example.test", "incorrect", "127.0.0.1")
        self.assertEqual(
            sum(event["event"] == "login_failed" for event in self.store.audit_events()),
            6,
        )

    def test_employee_roles_limit_sensitive_capabilities(self) -> None:
        officer = self.store.create_employee(
            "officer@example.test", "officer", "Loan Officer", "Loan Officer",
            "Officer-Password-For-Test",
        )
        investigator = self.store.create_employee(
            "investigator@example.test", "investigator", "Investigator", "Fraud Investigator",
            "Investigator-Password-For-Test",
        )
        auditor = self.store.create_employee(
            "auditor@example.test", "auditor", "Auditor", "Auditor",
            "Auditor-Password-For-Test",
        )
        self.assertTrue(has_permission(officer, "loans.read"))
        self.assertFalse(has_permission(officer, "fraud.read"))
        self.assertTrue(has_permission(investigator, "fraud.read"))
        self.assertFalse(has_permission(investigator, "documents.read"))
        self.assertTrue(has_permission(auditor, "audit.read"))
        self.assertFalse(has_permission(auditor, "loans.read"))

    def test_disabling_employee_revokes_existing_sessions(self) -> None:
        officer = self.store.create_employee(
            "officer@example.test", "officer", "Loan Officer", "Loan Officer",
            "Officer-Password-For-Test",
        )
        token, employee = self.store.authenticate(
            "officer@example.test", "Officer-Password-For-Test", "127.0.0.1"
        )
        self.store.update_employee(officer["id"], None, False, None)
        self.assertIsNone(self.store.employee_for_token(token))

    def test_role_change_revokes_existing_sessions(self) -> None:
        officer = self.store.create_employee(
            "officer@example.test", "officer", "Loan Officer", "Loan Officer",
            "Officer-Password-For-Test",
        )
        token, employee = self.store.authenticate(
            officer["email"], "Officer-Password-For-Test", "127.0.0.1"
        )
        self.store.update_employee(employee["id"], "Auditor", None, None)
        self.assertIsNone(self.store.employee_for_token(token))

    def test_last_active_admin_cannot_be_demoted_or_disabled(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "At least one active Admin"):
            self.store.update_employee(self.admin["id"], "Auditor", None, None)
        with self.assertRaisesRegex(AuthenticationError, "At least one active Admin"):
            self.store.update_employee(self.admin["id"], None, False, None)

    def test_short_employee_password_is_rejected(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "at least 12"):
            self.store.create_employee(
                "short@example.test", "short", "Short Password", "Loan Officer", "tiny"
            )


if __name__ == "__main__":
    unittest.main()
