from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from dealpilot.engine import analyze
from dealpilot.workflow import WorkflowError, WorkflowStore


ROOT = Path(__file__).resolve().parents[1]
HOST = os.environ.get("DEALPILOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEALPILOT_PORT", "8765"))
_cache_lock = threading.Lock()
_cached_signature: tuple[tuple[str, int, int], ...] | None = None
_cached_analysis: dict[str, Any] | None = None
_workflow_store = WorkflowStore(ROOT)


def _signature() -> tuple[tuple[str, int, int], ...]:
    sources = [
        ROOT / "participant" / "participant" / "history" / "deals.csv",
        ROOT / "participant" / "participant" / "history" / "monthly_deposits.csv",
    ]
    shared = ROOT / "participant" / "participant" / "shared_folder"
    if shared.is_dir():
        sources.extend(shared.rglob("*.pdf"))
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


class Handler(BaseHTTPRequestHandler):
    server_version = "DealPilotAnalysis/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path != "/api/analysis":
            if self.path == "/api/workflow":
                try:
                    self._send_json(200, _workflow_store.snapshot())
                except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                    print(f"Workflow request failed: {exc}", file=sys.stderr)
                    self._send_json(503, {"error": str(exc)})
                return
            self._send_json(404, {"error": "Not found"})
            return
        try:
            self._send_json(200, _get_analysis())
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Analysis request failed: {exc}", file=sys.stderr)
            self._send_json(503, {"error": str(exc)})

    def do_POST(self) -> None:
        if self.path == "/api/workflow/advance-clock":
            try:
                body = self._read_json()
                now = _workflow_store.advance_clock(str(body.get("by", "")))
                self._send_json(200, _workflow_store.snapshot() | {"now": now})
            except WorkflowError as exc:
                self._send_json(400, {"error": str(exc)})
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                print(f"Clock update failed: {exc}", file=sys.stderr)
                self._send_json(503, {"error": str(exc)})
            return
        if self.path.startswith("/api/workflow/") and self.path.endswith("/actions"):
            try:
                deal_id = unquote(self.path[len("/api/workflow/") : -len("/actions")].strip("/"))
                result = _workflow_store.act(deal_id, self._read_json())
                self._send_json(200, result)
            except WorkflowError as exc:
                self._send_json(409, {"error": str(exc), "blockers": exc.blockers})
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                print(f"Workflow action failed: {exc}", file=sys.stderr)
                self._send_json(503, {"error": str(exc)})
            return
        if self.path != "/api/reload":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            self._send_json(200, _get_analysis(force=True))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Analysis refresh failed: {exc}", file=sys.stderr)
            self._send_json(503, {"error": str(exc)})

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1_000_000:
                raise WorkflowError("Request body must be smaller than 1 MB.")
            if not length:
                return {}
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WorkflowError("Request body must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise WorkflowError("Request body must be a JSON object.")
        return value

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"DealPilot analysis API listening at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DealPilot analysis API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
