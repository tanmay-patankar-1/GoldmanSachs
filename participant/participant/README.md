# DealPilot — Team Data Pack (HackUTD 2026)

Everything here is **made up**. No real people, companies, accounts or IDs. Bank: *Trinity Commercial Bank* (fictional).
Emails end in `.example` and phone numbers use the reserved 555-01xx range.

## What's in the box

```
shared_folder/           The bank's shared loan folder: one sub-folder per borrower (13 businesses).
                         PDFs, CSV bank exports, a phone photo of an ID. File names are whatever
                         the borrower used — some are tidy, some are "SCAN_2026…pdf". Read the content.
history/
  deals.csv              One year of past deals (Nov 2025 – Oct 2026), ~257 rows. Old tricks are marked
                         in `fraud_label`. Outcomes + repayment status included.
  monthly_deposits.csv   6 months of bank-deposit summaries before each past deal.
  stage_events.csv       How long every past deal sat in every stage, SLA breaches, alerts.
  known_fraud_checks.md  The bank's existing list of old tricks.
rules/
  stages.json            The pipeline, what each step needs, SLA time limits, human sign-offs, escalation rules.
  known_fraud_checks.md  (copy)
contacts/teams.json      Who does what, backups, who to escalate to, who is out of office.
sim/
  clock.py               The pretend clock. Fast-forward time; files "land" when their time comes.
  arrivals.jsonl         When each file lands in the shared folder (sim time, America/Chicago).
  assignments.json       Relationship manager + credit officer for each deal.
SCHEMA.md                Field-by-field data dictionary.
SUBMISSION_FORMAT.md     The JSON your DealPilot must output for judging.
```

## The sim week

Sim time runs **Mon 2026-11-02 08:00 → Fri 2026-11-06 18:00 CT**. Deals arrive through the week.

```bash
python3 sim/clock.py reset                  # 2026-11-02 08:00
python3 sim/clock.py advance 4h
python3 sim/clock.py inbox --new            # what just landed
python3 sim/clock.py sync live_inbox/       # copy landed files somewhere your app watches
python3 sim/clock.py serve 8765             # or use the HTTP API (GET /now, /inbox · POST /advance?by=6h)
```

Your system should only "see" a file once the clock has passed its `arrive_at`.
Read the real files from `shared_folder/` (that's what `sync` copies).

## Humans

DealPilot must stop and **ask a person** for: credit decisions, committee approval, commitment sign-off,
funding release, and fraud holds (see `rules/stages.json`). During development, play the humans yourself.
During judging, the organizers answer as those people — some answer fast, some are out of office, some
don't answer at all. Read `contacts/teams.json` carefully.

## The weekend drop

Saturday the organizers release **one more drop**: new deals, plus new papers on deals you already have
(and a notice from Collections). The trick in it may look different. Then your system runs on a
**hidden set** with a different trick. *Build for the idea, not the examples.*

## How you'll be scored (summary)

| Score | What earns points |
|---|---|
| **READ** | Correct borrower name, EIN, amount, purpose, product, term, guarantor, stated revenue — and spotting what is missing or doesn't match (no false alarms). |
| **MOVE** | Stages in order, the right human asked before each sign-off step, alerts + escalations on stuck deals, not pushing a deal forward while it waits on a document, hitting "funds needed by" dates. |
| **FRAUD** | Fakes held **before** money goes out (earlier = more points), the pattern spotted in the first few deals, good deals **not** blocked (each false flag costs a lot), and "finding the rest" — linking related deals, including old ones in `history/`. |

Have fun. One deal can look fine. Look across all the deals.
