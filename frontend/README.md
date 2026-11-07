# DealPilot frontend

An interactive React and TypeScript demo for the HackUTD 2026 commercial-lending challenge.

## Run locally

Start the analysis API from the repository root:

```powershell
python -m pip install -r backend/requirements.txt
python backend/server.py
```

In a second terminal, start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Use `npm run build` to type-check and create a production build, and `npm run lint` to run Oxlint.
The Vite development server proxies `/api` requests to the Python service on port `8765`.
The analysis service re-reads source records when files change; the **Recalculate** action forces a refresh.

## Demo behavior

- Borrower, history, assignment, stage, and control examples are based on the synthetic challenge pack.
- `src/data/mockData.ts` contains portfolio and workflow UI data.
- Fraud signals, evidence references, scores, and network relationships come from the Python analysis service.
- The review-priority index is an uncalibrated evidence-family coverage measure, not a probability or confirmed fraud finding.
- Review actions and the simulation clock update the local interface only. They do not send decisions, create holds, or contact bank employees.

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
