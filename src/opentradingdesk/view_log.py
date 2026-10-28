"""Append-only, hash-chained view log.

Every view a provider submits (or no-show) is written here BEFORE the
outcome is known, each row hashed together with the previous row's hash.
Publishing the latest hash (print it, post it, whatever) lets anyone
recompute the chain and confirm nothing was edited or backdated after the
fact — including us, the people running the database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .schemas import NoShow, View

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS view_log (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,   -- 'view' | 'no_show'
    payload         TEXT NOT NULL,   -- canonical JSON of the View/NoShow
    session_minute  INTEGER NOT NULL, -- synthetic-market minute the view was requested at
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL
);
"""


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _row_hash(prev_hash: str, kind: str, payload_json: str, session_minute: int) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(kind.encode())
    h.update(payload_json.encode())
    h.update(str(session_minute).encode())
    return h.hexdigest()


class ViewLog:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def latest_hash(self) -> str:
        row = self._conn.execute(
            "SELECT hash FROM view_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else GENESIS_HASH

    def log_view(self, view: View, session_minute: int) -> str:
        return self._append("view", view.__dict__, session_minute)

    def log_no_show(self, no_show: NoShow, session_minute: int) -> str:
        return self._append("no_show", no_show.__dict__, session_minute)

    def _append(self, kind: str, payload: dict, session_minute: int) -> str:
        prev_hash = self.latest_hash()
        payload_json = _canonical(payload)
        row_hash = _row_hash(prev_hash, kind, payload_json, session_minute)
        self._conn.execute(
            "INSERT INTO view_log (kind, payload, session_minute, prev_hash, hash) VALUES (?, ?, ?, ?, ?)",
            (kind, payload_json, session_minute, prev_hash, row_hash),
        )
        self._conn.commit()
        return row_hash

    def all_entries(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM view_log ORDER BY seq ASC").fetchall()
        return [dict(r) for r in rows]

    def entries_for_ticker(self, ticker: str) -> list[dict]:
        out = []
        for row in self.all_entries():
            payload = json.loads(row["payload"])
            if payload.get("ticker") == ticker:
                out.append({**row, "payload": payload})
        return out

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute every hash from scratch and confirm the chain is intact.

        Returns (ok, message). This is the "independent recomputation" check
        the proposal asks for — anyone with read access to the DB (or just
        the published latest hash) can run this without trusting us.
        """
        prev_hash = GENESIS_HASH
        for row in self.all_entries():
            expected = _row_hash(prev_hash, row["kind"], row["payload"], row["session_minute"])
            if expected != row["hash"]:
                return False, f"chain broken at seq={row['seq']}: expected {expected}, found {row['hash']}"
            if row["prev_hash"] != prev_hash:
                return False, f"chain broken at seq={row['seq']}: prev_hash mismatch"
            prev_hash = row["hash"]
        return True, f"chain verified, {len(self.all_entries())} entries, latest_hash={prev_hash}"

    def close(self) -> None:
        self._conn.close()
