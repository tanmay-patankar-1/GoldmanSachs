# DealPilot

DealPilot is an interactive frontend with local cross-deal analysis and loan-workflow services for the HackUTD 2026 commercial-lending challenge.
The synthetic challenge data is in [`participant/participant`](./participant/participant),
the React/TypeScript application is in [`frontend`](./frontend), and the source-backed
fraud analysis and workflow services are in [`backend`](./backend).

## Run locally

```powershell
python -m pip install -r backend/requirements.txt
python backend/server.py
```

In a second terminal, run `cd frontend; npm install; npm run dev`. The frontend proxies
analysis and workflow requests to the service on `127.0.0.1:8765`. See
[`frontend/README.md`](./frontend/README.md) for validation commands and limitations.
