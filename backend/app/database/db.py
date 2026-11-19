"""SQLite metadata store + on-disk documents/results.

Layout under STORAGE_DIR (a persistent Docker volume in production):

    app.db                        document metadata (this module)
    uploads/<id>.<ext>            original files (never served without auth)
    results/<id>/model_output.json  original model output + diagnostics (immutable)
    results/<id>/corrected.json     reviewer's corrected values (if any)
    cache/                        extraction cache keyed by file hash + config
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_name       TEXT,
    mime_type         TEXT,
    size_bytes        INTEGER,
    sha256            TEXT,
    pages             INTEGER,
    status            TEXT NOT NULL,
    is_invoice        INTEGER,
    needs_review      INTEGER NOT NULL DEFAULT 0,
    review_status     TEXT NOT NULL DEFAULT 'not_required',
    review_reasons    TEXT NOT NULL DEFAULT '[]',
    error_code        TEXT,
    error_message     TEXT,
    route             TEXT,
    document_type     TEXT,
    vendor            TEXT,
    invoice_number    TEXT,
    invoice_date      TEXT,
    currency          TEXT,
    total             REAL,
    financial_status  TEXT,
    model             TEXT,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    attempts          INTEGER NOT NULL DEFAULT 0,
    cached            INTEGER NOT NULL DEFAULT 0,
    processing_ms     INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    reviewed_at       TEXT,
    review_note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_review ON documents(needs_review, review_status);
"""

REVIEW_STATUSES = ("not_required", "pending", "in_review", "approved")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.uploads_dir = storage_dir / "uploads"
        self.results_dir = storage_dir / "results"
        for directory in (storage_dir, self.uploads_dir, self.results_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.path = storage_dir / "app.db"
        self._write_lock = threading.Lock()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ----------------------------------------------------------------- writes
    def create_document(self, doc_id: str, **fields: Any) -> None:
        now = utcnow()
        fields.update(id=doc_id, created_at=now, updated_at=now)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._write_lock, self.connect() as conn:
            conn.execute(f"INSERT INTO documents ({cols}) VALUES ({marks})", tuple(fields.values()))

    def update_document(self, doc_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._write_lock, self.connect() as conn:
            conn.execute(f"UPDATE documents SET {assignments} WHERE id = ?", (*fields.values(), doc_id))

    # ------------------------------------------------------------------ reads
    def get(self, doc_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return _row(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        review: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if kind == "invoice":
            where.append("status = 'success' AND is_invoice = 1")
        elif kind == "non_invoice":
            where.append("status = 'success' AND is_invoice = 0")
        if review == "pending":
            where.append("needs_review = 1 AND review_status IN ('pending', 'in_review')")
        elif review in REVIEW_STATUSES:
            where.append("review_status = ?")
            params.append(review)
        if query:
            where.append("(original_filename LIKE ? OR vendor LIKE ? OR invoice_number LIKE ?)")
            like = f"%{query}%"
            params += [like, like, like]
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM documents {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM documents {clause} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [_row(r) for r in rows], total

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            counts = {
                "total_documents": q("SELECT COUNT(*) FROM documents"),
                "processing": q("SELECT COUNT(*) FROM documents WHERE status = 'processing'"),
                "invoices": q("SELECT COUNT(*) FROM documents WHERE status = 'success' AND is_invoice = 1"),
                "non_invoices": q("SELECT COUNT(*) FROM documents WHERE status = 'success' AND is_invoice = 0"),
                "failed": q("SELECT COUNT(*) FROM documents WHERE status = 'failed'"),
                "needs_review": q(
                    "SELECT COUNT(*) FROM documents WHERE needs_review = 1 AND review_status IN ('pending','in_review')"
                ),
                "flagged_total": q("SELECT COUNT(*) FROM documents WHERE needs_review = 1"),
                "approved": q("SELECT COUNT(*) FROM documents WHERE review_status = 'approved'"),
                "cached_results": q("SELECT COUNT(*) FROM documents WHERE cached = 1"),
            }
            billed = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd),0), COALESCE(AVG(cost_usd),0), COALESCE(SUM(total_tokens),0),"
                " COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0)"
                " FROM documents WHERE cached = 0 AND attempts > 0"
            ).fetchone()
            timing = conn.execute(
                "SELECT AVG(processing_ms), MAX(processing_ms) FROM documents WHERE status = 'success' AND cached = 0"
            ).fetchone()
            routes = conn.execute(
                "SELECT COALESCE(route,'none') AS route, COUNT(*) AS n FROM documents WHERE status='success' GROUP BY route"
            ).fetchall()
            errors = conn.execute(
                "SELECT error_code, COUNT(*) AS n FROM documents WHERE status='failed' GROUP BY error_code ORDER BY n DESC"
            ).fetchall()
            reason_rows = conn.execute("SELECT review_reasons FROM documents WHERE needs_review = 1").fetchall()
            latencies = [
                r[0]
                for r in conn.execute(
                    "SELECT processing_ms FROM documents WHERE status='success' AND cached = 0 AND processing_ms IS NOT NULL"
                ).fetchall()
            ]
        reason_counts: dict[str, int] = {}
        for row in reason_rows:
            for reason in json.loads(row[0] or "[]"):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        latencies.sort()

        def pct(p: float) -> int | None:
            if not latencies:
                return None
            return latencies[min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))]

        return {
            **counts,
            "billed_documents": billed[0],
            "total_cost_usd": round(billed[1], 6),
            "avg_cost_usd": round(billed[2], 6),
            "total_tokens": billed[3],
            "input_tokens": billed[4],
            "output_tokens": billed[5],
            "avg_processing_ms": round(timing[0]) if timing[0] is not None else None,
            "max_processing_ms": timing[1],
            "p50_processing_ms": pct(0.5),
            "p90_processing_ms": pct(0.9),
            "routes": {r["route"]: r["n"] for r in routes},
            "error_codes": {r["error_code"] or "UNKNOWN": r["n"] for r in errors},
            "review_reasons": dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])),
        }

    # ------------------------------------------------------------------ files
    def upload_path(self, stored_name: str) -> Path:
        path = (self.uploads_dir / stored_name).resolve()
        if self.uploads_dir.resolve() not in path.parents:
            raise ValueError("invalid stored file name")
        return path

    def result_dir(self, doc_id: str) -> Path:
        path = self.results_dir / doc_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, doc_id: str, name: str, payload: dict[str, Any]) -> None:
        target = self.result_dir(doc_id) / name
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(target)

    def read_json(self, doc_id: str, name: str) -> dict[str, Any] | None:
        path = self.results_dir / doc_id / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["review_reasons"] = json.loads(data.get("review_reasons") or "[]")
    for key in ("needs_review", "cached"):
        data[key] = bool(data.get(key))
    if data.get("is_invoice") is not None:
        data["is_invoice"] = bool(data["is_invoice"])
    return data
