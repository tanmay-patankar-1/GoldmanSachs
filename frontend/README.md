# DealPilot frontend

An interactive React and TypeScript demo for the HackUTD 2026 commercial-lending challenge.

## Run locally

Start the FastAPI service from the repository root. On the first run, supply a
bootstrap Admin email and a unique password of at least 12 characters:

```bash
python -m pip install -r backend/requirements.txt
export DEALPILOT_BOOTSTRAP_ADMIN_EMAIL="admin@your-company.example"
printf 'Enter a unique bootstrap password (12+ characters): '
IFS= read -r -s DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD
printf '\n'
export DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD
python backend/server.py
```

The bootstrap variables are only required until the first employee account is
created. Accounts, hashed passwords, sessions, and audit events are stored in
`backend/auth.sqlite3`; the database file is restricted to the current
user. Do not use real borrower data or deploy this local demo without a
production identity provider, HTTPS, and a security review.

In a second terminal, start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Use `npm run build` to type-check and create a production build, and `npm run lint` to run Oxlint.
The Vite development server proxies `/api` requests to the Python service on port `8765`.
The analysis service re-reads source records when files change; the **Recalculate** action forces a refresh.

Run backend tests from the repository root:

```bash
PYTHONPATH=backend python -m unittest discover -s backend/tests
```

## Employee roles and audit

- **Admin:** all workspace features, employee management, and audit records.
- **Loan Officer:** overview, applications, and loan progress; no fraud graph access.
- **Underwriter:** applications, loan progress, and protected document downloads.
- **Fraud Investigator:** fraud analysis and connections, with workflow data redacted
  to omit application financial fields and document contents.
- **Auditor:** read-only historical applications and audit log; direct identifiers are omitted from historical results.

The API enforces these permissions independently of the frontend. Admins create employees, assign roles, reset passwords, and disable accounts in
**Employee access**. Passwords are salted and
PBKDF2-hashed, sessions use an HttpOnly SameSite cookie and expire after eight
hours, and repeated failed sign-ins are throttled. Sign-ins, rejected logins,
portfolio, application, and historical-record access, document downloads, workflow actions, employee
administration, and denied access attempts are recorded in the audit log.
Workflow employee IDs must match the assigned workflow identity for actions
which already require a specific assigned reviewer. The current demo has no
supplier-payment editing feature, so it cannot produce supplier-payment change
events.

## Demo behavior

- Borrower, history, assignment, stage, and control examples are based on the synthetic challenge pack.
- `src/data/mockData.ts` contains offline fallback data; when the API is available, portfolio rows, documents, workflow status, and reviewers come from extracted challenge packets.
- Fraud signals, evidence references, scores, and network relationships come from the Python analysis service.
- The review-priority index is an uncalibrated evidence-family coverage measure, not a probability or confirmed fraud finding.
- Workflow actions and the simulation clock are persisted locally in SQLite and the challenge simulation state file. No external messages are sent.
- Workflow employee IDs are linked to authenticated local accounts and are still demo metadata, not bank identities. Do not use this service for real lending decisions.
- Known-rule checks are first-pass document heuristics. A human rationale is required to resolve an alerted or unevaluable check; this records review, not proof that a document is valid or fraud-free.
- PDF text extraction depends on machine-readable text and the supplied layouts. Unreadable scans and unsupported layouts can leave fields or checks unevaluable.

## Application workflow

The local API scans the shared-folder application PDFs on workflow requests, extracts application facts with source paths, and builds a document checklist from the challenge stage rules. It exposes `GET /api/workflow`, `POST /api/workflow/advance-clock`, and `POST /api/workflow/{id}/actions`.

The workflow follows Intake → Credit → Committee → Product → Committed → Closed. Stage advancement is blocked by required documents, unresolved known-rule checks, missing product/vendor verification, commitment documentation, or closing controls. Human approvals are recorded with an actor ID and rationale where required. The simulation clock creates durable SLA alerts at 100%, 150%, and 200% of the applicable stage SLA, routed to the configured owner/escalation contact.

Actions, alerts, and review history are local demo state. FastAPI authentication and role checks protect the demo, but this is not a production banking identity or authorization system. Uploads are not accepted through an API endpoint; placing a new supported PDF in a shared-folder deal directory causes the next workflow scan to recalculate extracted data and checklist status.

## Cross-deal patterns

The analysis service compares current packets with historical deals and reports source-backed review leads for:

1. Reused supplier payment destinations across borrowers.
2. Supplier-name variations, linked only as candidates after removing legal suffixes.
3. Current supplier accounts that differ from a stable historical destination (at least three prior records, with one account used in at least 70% of them).
4. Consecutive supplier quote identifiers across at least three borrowers.
5. Supplier concentration at or above the historical 95th-percentile deal count, with a minimum of three current borrowers.
6. Exact or near matches in at least five labeled financial values.
7. A matching preparer identity and phone contact across at least three current businesses.
8. Similar expense structures: at least three of four expense-to-revenue ratios within two percentage points across at least three borrowers.
9. Unusually high round-number deposit rates, evaluated only with at least three statement months and ten deposits against a historical 95th-percentile reference.
10. Historical deal exposure connected through supplier candidates and payment destinations.

The review-priority index assigns 25 points per distinct scoring family, capped at four families. Supplier observations are grouped together, and exact financial duplication and expense-structure similarity share one family. Preparer links and supplier-name variations are shown for investigation but do not add points on their own. Common suppliers, accountants, and sequential vendor numbering can be legitimate.

The engine uses the historical CSVs and PDFs under `participant/participant`. It extracts text from PDFs with `pypdf`; image-only scans and unsupported document layouts may not yield evidence. Known fraud labels are reported as a separate baseline and are not used to train or score new-pattern signals. Operational SLA data is not fraud evidence. This is a heuristic review engine, not a trained or validated fraud classifier.
