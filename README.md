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

The separate, uncalibrated review-priority index weights evidence strength (35%),
independent corroboration (25%), cross-deal coordination (20%), historical deviation
(10%), and source traceability (10%). Each distinct scoring family contributes 1–25
points, capped at four families (100 points). Correlated supplier findings and financial
similarities are grouped to reduce double counting. Cluster assessments expose the
dimension scores and unresolved legitimate explanations; evidence confidence describes
source traceability and document diversity, not the truth of a claim. Behavioral anomalies,
document inconsistencies, known bank-rule triggers, and whole-dollar patterns remain
separate from novel-evidence points. Neither the index nor an anomaly percentile is a
fraud probability or a validated measure of loss.

Cross-deal analysis includes repeated transaction timing/sequence observations when
multiple businesses share a normalized counterparty over several dates, plus a graph
summary when multiple meaningful evidence families connect a cluster. Timing observations
do not add points on their own, and common counterparties are suppressed as non-rare
patterns. The graph summary organizes existing evidence; it does not claim statistical
graph rarity because historical entity-link data is incomplete.

The available historical files do not provide transaction-level records, verified
beneficial-ownership links, independent supplier verification, or confirmed fraud-free
outcomes for all unlabeled deals. Therefore, the engine does not train the Isolation Forest
on transaction-sequence or graph features, and document/source reliability is limited to
traceability available in extracted files. Supplier validation, document authenticity,
complete fund-flow tracing, and ownership resolution require independently verified
records and human review.

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
