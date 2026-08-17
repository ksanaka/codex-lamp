"""Concurrency-safe, per-session state persistence for Codex Lamp."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from typing import Iterator

from .config import Settings, ensure_layout


@dataclass(frozen=True)
class SessionRecord:
    """The latest lamp state observed for one Codex session."""

    session_id: str
    state: str
    event_time_ns: int
    updated_at: float


class StateStore:
    """Atomically aggregate active Codex session states."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.home = ensure_layout(settings.home)
        self.sessions_dir = self.home / "sessions"
        self.lock_path = self.home / "state.lock"
        self.effective_path = self.home / "effective_state.json"

    def update(
        self, session_id: str, state: str, event_time_ns: int, *, now: float | None = None
    ) -> None:
        """Store a session's newer event and refresh the aggregate state."""
        self._validate_update(session_id, state, event_time_ns)
        observed_at = self._now(now)
        record_path = self._record_path(session_id)

        with self._locked():
            existing = self._read_record(record_path) if record_path.exists() else None
            if existing is None or event_time_ns >= existing.event_time_ns:
                self._atomic_write(
                    record_path,
                    SessionRecord(
                        session_id=session_id,
                        state=state,
                        event_time_ns=event_time_ns,
                        updated_at=observed_at,
                    ),
                )
            self._effective_locked(observed_at)

    def remove(self, session_id: str, *, now: float | None = None) -> None:
        """Remove a session and refresh the aggregate state."""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")

        observed_at = self._now(now)
        with self._locked():
            self._record_path(session_id).unlink(missing_ok=True)
            self._effective_locked(observed_at)

    def prune(self, *, now: float | None = None) -> int:
        """Remove expired session records and return the number removed."""
        observed_at = self._now(now)
        with self._locked():
            removed, _ = self._active_records_locked(observed_at)
            self._effective_locked(observed_at)
            return removed

    def effective(self, *, now: float | None = None) -> str:
        """Return and atomically persist the aggregate state for active sessions."""
        observed_at = self._now(now)
        with self._locked():
            return self._effective_locked(observed_at)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _effective_locked(self, now: float) -> str:
        _, records = self._active_records_locked(now)
        active_states = {record.state for record in records}
        state = next((candidate for candidate in self.settings.priority if candidate in active_states), "off")
        self._atomic_write(self.effective_path, {"state": state, "updated_at": now})
        return state

    def _active_records_locked(self, now: float) -> tuple[int, list[SessionRecord]]:
        removed = 0
        records: list[SessionRecord] = []
        for record_path in self.sessions_dir.glob("*.json"):
            record = self._read_record(record_path)
            if now - record.updated_at >= self.settings.stale_seconds:
                record_path.unlink()
                removed += 1
            else:
                records.append(record)
        return removed, records

    def _record_path(self, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.sessions_dir / f"{digest}.json"

    @staticmethod
    def _read_record(path: Path) -> SessionRecord:
        with path.open(encoding="utf-8") as record_file:
            values = json.load(record_file)
        return SessionRecord(**values)

    @staticmethod
    def _atomic_write(path: Path, value: SessionRecord | dict[str, str | float]) -> None:
        values = asdict(value) if isinstance(value, SessionRecord) else value
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False
        ) as temporary_file:
            json.dump(values, temporary_file, sort_keys=True)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)

    def _validate_update(self, session_id: str, state: str, event_time_ns: int) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if state not in self.settings.priority:
            raise ValueError(f"invalid state: {state!r}")
        if not isinstance(event_time_ns, int) or isinstance(event_time_ns, bool):
            raise ValueError("event_time_ns must be an integer")

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else now
