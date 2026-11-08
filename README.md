# DealPilot

DealPilot is an interactive frontend with local cross-deal analysis and loan-workflow services for the HackUTD 2026 commercial-lending challenge.
The synthetic challenge data is in [`participant/participant`](./participant/participant),
the React/TypeScript application is in [`frontend`](./frontend), and the source-backed
fraud analysis and workflow services are in [`backend`](./backend).

## Run locally

The first backend start requires a one-time bootstrap Admin email and password.
Choose a unique password of at least 12 characters; credentials are read from
environment variables and are not stored in the repository.

```bash
python -m pip install -r backend/requirements.txt
export DEALPILOT_BOOTSTRAP_ADMIN_EMAIL="admin@your-company.example"
printf 'Enter a unique bootstrap password (12+ characters): '
IFS= read -r -s DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD
printf '\n'
export DEALPILOT_BOOTSTRAP_ADMIN_PASSWORD
python backend/server.py
```

On later starts, the employee account remains in the local auth database and the
bootstrap variables are only needed if no employee has been created yet. In a
second terminal, run `cd frontend && npm install && npm run dev`. The frontend proxies
API requests to the service on `127.0.0.1:8765`. See
[`frontend/README.md`](./frontend/README.md) for validation commands and limitations.

## Fraud analysis model

Fraud review combines explicit bank-control checks and cross-deal relationship analysis
with an unsupervised scikit-learn Isolation Forest. Each analysis run trains the model
from historical deals that have no existing bank-rule fraud label; those labels are
excluded from the model. Features include loan and revenue amounts, business age, loan
terms, industry and product categories, and summarized deposit behavior. Current
applications are ranked against cross-fitted historical anomaly scores.

An anomaly percentile is a review-priority lead, not a fraud probability or a validated
accuracy measure. The model does not replace transaction-link and graph analysis, and
its results require human review. The historical `none` label means no listed bank-rule
label, not independently confirmed fraud-free. Before operational use, validate against
representative, independently adjudicated outcomes and monitor false positives.

The separate uncalibrated investigation-priority index assigns each supported scoring
family 1–25 points based on the strongest signal severity, distinct source documents,
corroborating evidence types, and connected applicants. The total is capped at four
families (100 points). The interface shows each contribution's urgency band and rationale;
behavioral anomalies and document inconsistencies do not add points. This review index is
not a fraud probability or a validated measure of loss.

The Fraud Checks view lists every current application and keeps novel pattern leads,
the five known bank-rule results, document completeness/inconsistencies, and workflow
blockers in separate columns. Scores are matched to workflow records by source folder as
well as borrower name. Missing results and unevaluable checks are labeled explicitly;
a zero index is not presented as a fraud-free determination. This screen does not
calculate credit capacity, repayment risk, or a lending decision.

Whole-dollar deposit behavior is compared with historical deal-level averages for the
same NAICS industry when at least five peers are available, otherwise the all-industry
fallback is disclosed. Historical records contain monthly aggregates rather than
transaction descriptions, so the system cannot compare deposit channels with historical
peers.
