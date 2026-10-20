"""Submission tracking: what was done, when, and when to redo it."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from .paths import state_path

STATUSES = ("done", "pending", "skipped", "failed")


@dataclass
class Record:
    status: str
    at: str  # ISO date
    expires: str | None = None
    note: str = ""

    @property
    def expires_date(self) -> date | None:
        return date.fromisoformat(self.expires) if self.expires else None

    def is_expired(self, today: date | None = None) -> bool:
        today = today or date.today()
        e = self.expires_date
        return bool(e and e <= today)

    def expires_within(self, days: int, today: date | None = None) -> bool:
        today = today or date.today()
        e = self.expires_date
        return bool(e and today < e <= today + timedelta(days=days))


class State:
    def __init__(self, path=None):
        self.path = path or state_path()
        self.records: dict[str, Record] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text() or "{}")
            self.records = {k: Record(**v) for k, v in raw.items()}

    def save(self) -> None:
        self.path.write_text(json.dumps({k: asdict(v) for k, v in self.records.items()}, indent=2, sort_keys=True))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, key: str) -> Record | None:
        return self.records.get(key)

    def mark(self, key: str, status: str, renew_days: int | None, note: str = "", when: date | None = None) -> Record:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        when = when or date.today()
        expires = (when + timedelta(days=renew_days)).isoformat() if (status == "done" and renew_days) else None
        rec = Record(status=status, at=when.isoformat(), expires=expires, note=note)
        self.records[key] = rec
        self.save()
        return rec

    def needs_action(self, key: str, today: date | None = None) -> bool:
        rec = self.get(key)
        if rec is None:
            return True
        if rec.status in ("pending", "failed"):
            return True
        if rec.status == "skipped":
            return False
        return rec.is_expired(today)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
