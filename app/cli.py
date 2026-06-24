"""Operational CLI helpers."""

from pathlib import Path
import sys

from . import backups, config, db, jobs, seed
from .readiness import summary


USAGE = (
    "usage: python -m app.cli "
    "[backup [destination-dir]|check-production|migrate|seed-demo|"
    "verify-backup <db>|worker [--once|--limit N|--poll SECONDS]]"
)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(USAGE)
        return 2

    command = argv[0]
    if command == "migrate":
        if len(argv) != 1:
            print(USAGE)
            return 2
        db.migrate()
        print("migrations applied")
        return 0

    if command == "seed-demo":
        if len(argv) != 1:
            print(USAGE)
            return 2
        result = seed.seed_demo_workspace()
        print("seeded {slug} pack={pack_id} share={share_url}".format(**result))
        return 0

    if command == "check-production":
        if len(argv) != 1:
            print(USAGE)
            return 2
        report = summary()
        for item in report["checks"]:
            mark = "ok" if item["ok"] else "fail"
            print(f"{mark}\t{item['key']}\t{item['detail']}")
        return 0 if report["ready"] else 1

    if command == "worker":
        return _worker(argv[1:])

    if command == "backup":
        if len(argv) > 2:
            print(USAGE)
            return 2
        destination = Path(argv[1]) if len(argv) == 2 else None
        try:
            result = backups.create_backup(destination)
        except backups.BackupError as exc:
            print(f"backup failed: {exc}", file=sys.stderr)
            return 1
        print(f"backup\t{result.path}")
        _print_verification("restore_check", result.verification)
        return 0

    if command == "verify-backup":
        if len(argv) != 2:
            print(USAGE)
            return 2
        try:
            result = backups.verify_backup(Path(argv[1]))
        except backups.BackupError as exc:
            print(f"verify failed: {exc}", file=sys.stderr)
            return 1
        _print_verification("verify", result)
        return 0

    print(USAGE)
    return 2


def _worker(argv: list[str]) -> int:
    poll = config.JOB_WORKER_POLL_SECONDS
    limit: int | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--once":
            limit = 1
            i += 1
            continue
        if arg == "--limit" and i + 1 < len(argv):
            try:
                limit = int(argv[i + 1])
            except ValueError:
                print(USAGE)
                return 2
            if limit < 1:
                print(USAGE)
                return 2
            i += 2
            continue
        if arg == "--poll" and i + 1 < len(argv):
            try:
                poll = float(argv[i + 1])
            except ValueError:
                print(USAGE)
                return 2
            i += 2
            continue
        print(USAGE)
        return 2

    db.migrate()
    if limit is None:
        print(f"worker\tstarted\tpoll={poll}", flush=True)
        jobs.work(poll_seconds=poll)
        return 0

    processed = jobs.work(poll_seconds=poll, limit=limit)
    print(
        f"worker\tprocessed={processed}\tpending={jobs.pending_count()}"
        f"\tfailed={jobs.failed_count()}"
    )
    return 0


def _print_verification(label: str, result: backups.VerificationResult) -> None:
    print(
        f"{label}\tok\tintegrity={result.integrity}"
        f"\tmigrations={len(result.applied_migrations)}"
        f"\ttables={result.table_count}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
