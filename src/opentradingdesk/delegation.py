"""Owner-signed, revocable delegation tokens.

The proposal calls for JWT-style delegations. This is a minimal
HMAC-signed token with the same properties (signed limits + expiry,
checked against a revocation list on every order) without pulling in a
JWT library — see README "Known limitations" for the honest version of
that tradeoff.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path

from .schemas import Delegation

SCHEMA = """
CREATE TABLE IF NOT EXISTS revocations (
    token_id TEXT PRIMARY KEY,
    revoked_at REAL NOT NULL
);
"""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class DelegationError(Exception):
    pass


class DelegationAuthority:
    """The owner's signing authority. In production this key lives with the
    owner, never with the PM agent — the PM only ever holds the token."""

    def __init__(self, db_path: str | Path, secret: bytes | None = None):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._secret = secret or secrets.token_bytes(32)

    def issue(
        self,
        owner: str,
        max_position_pct: float,
        long_only: bool,
        flat_by_minute: int,
        daily_loss_stop_pct: float,
        ttl_minutes: int,
    ) -> str:
        token_id = secrets.token_hex(8)
        payload = {
            "owner": owner,
            "max_position_pct": max_position_pct,
            "long_only": long_only,
            "flat_by_minute": flat_by_minute,
            "daily_loss_stop_pct": daily_loss_stop_pct,
            "expires_at": time.time() + ttl_minutes * 60,
            "token_id": token_id,
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        sig = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        return f"{_b64(payload_bytes)}.{_b64(sig)}"

    def revoke(self, token: str) -> None:
        delegation = self._decode(token)
        self._conn.execute(
            "INSERT OR REPLACE INTO revocations (token_id, revoked_at) VALUES (?, ?)",
            (delegation.token_id, time.time()),
        )
        self._conn.commit()

    def verify(self, token: str) -> Delegation:
        """Decode + check signature + expiry + revocation list. Raises
        DelegationError with a machine-readable reason on any failure."""
        delegation = self._decode(token)
        row = self._conn.execute(
            "SELECT 1 FROM revocations WHERE token_id = ?", (delegation.token_id,)
        ).fetchone()
        if row:
            raise DelegationError("DELEGATION_REVOKED")
        if time.time() > float(delegation.expires_at):
            raise DelegationError("DELEGATION_EXPIRED")
        return delegation

    def _decode(self, token: str) -> Delegation:
        try:
            payload_b64, sig_b64 = token.split(".")
            payload_bytes = _unb64(payload_b64)
            sig = _unb64(sig_b64)
        except Exception as e:
            raise DelegationError("DELEGATION_MALFORMED") from e

        expected_sig = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            raise DelegationError("DELEGATION_BAD_SIGNATURE")

        data = json.loads(payload_bytes)
        return Delegation(
            owner=data["owner"],
            max_position_pct=data["max_position_pct"],
            long_only=data["long_only"],
            flat_by_minute=data["flat_by_minute"],
            daily_loss_stop_pct=data["daily_loss_stop_pct"],
            expires_at=data["expires_at"],
            token_id=data["token_id"],
        )

    def close(self) -> None:
        self._conn.close()
