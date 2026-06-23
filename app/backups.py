"""SQLite backup and restore-verification helpers."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import tempfile

from . import config, db


class BackupError(RuntimeError):
    """Raised when a backup or verification step cannot complete."""


@dataclass(frozen=True)
class VerificationResult:
    path: Path
    integrity: str
    applied_migrations: tuple[str, ...]
    missing_migrations: tuple[str, ...]
    table_count: int


@dataclass(frozen=True)
class BackupResult:
    path: Path
    verification: VerificationResult


def default_backup_dir() -> Path:
    return config.DATA_DIR / "backups"


def create_backup(destination_dir: str | Path | None = None) -> BackupResult:
    """Create a private SQLite snapshot and verify it through a temp restore."""
    source_path = config.DB_PATH
    if not source_path.exists():
        raise BackupError(f"database does not exist: {source_path}")

    destination = Path(destination_dir) if destination_dir else default_backup_dir()
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)

    backup_path = _next_backup_path(destination)
    tmp_path = destination / f".{backup_path.name}.tmp"
    if tmp_path.exists():
        raise BackupError(f"temporary backup path already exists: {tmp_path}")

    try:
        _sqlite_backup(source_path, tmp_path)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, backup_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    verification = verify_backup(backup_path)
    return BackupResult(path=backup_path, verification=verification)


def verify_backup(path: str | Path) -> VerificationResult:
    """Restore a backup into a temp DB and inspect that restored copy."""
    backup_path = Path(path)
    if not backup_path.exists():
        raise BackupError(f"backup does not exist: {backup_path}")

    with tempfile.TemporaryDirectory(prefix="dionysus-restore-check-") as tmp_dir:
        restore_path = Path(tmp_dir) / "dionysus.db"
        _sqlite_backup(backup_path, restore_path)
        return _inspect_database(restore_path, reported_path=backup_path)


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=30)
    try:
        source.execute("PRAGMA busy_timeout=30000")
        destination = sqlite3.connect(destination_path, timeout=30)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
    finally:
        source.close()


def _inspect_database(path: Path, *, reported_path: Path) -> VerificationResult:
    con = sqlite3.connect(path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError(f"integrity_check failed: {integrity}")

        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "schema_migrations" not in tables:
            raise BackupError("schema_migrations table missing")

        applied = tuple(
            row[0]
            for row in con.execute("SELECT name FROM schema_migrations ORDER BY name")
        )
        expected = tuple(path.name for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")))
        missing = tuple(name for name in expected if name not in applied)
        if missing:
            raise BackupError("missing migrations: " + ", ".join(missing))

        return VerificationResult(
            path=reported_path,
            integrity=integrity,
            applied_migrations=applied,
            missing_migrations=missing,
            table_count=len(tables),
        )
    finally:
        con.close()


def _next_backup_path(destination: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    for index in range(100):
        suffix = "" if index == 0 else f"-{index:02d}"
        candidate = destination / f"dionysus-{stamp}{suffix}.db"
        if not candidate.exists():
            return candidate
    raise BackupError(f"could not find a free backup filename in {destination}")
