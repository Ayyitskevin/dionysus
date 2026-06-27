"""Operational CLI helpers."""

from pathlib import Path
import sys

from . import backups, config, db, jobs, rate_limit, seed
from .readiness import summary


USAGE = (
    "usage: python -m app.cli "
    "[backup [destination-dir]|check-production|migrate|rate-limits "
    "[--window SECONDS|--limit N|--action ACTION]|seed-demo|"
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
        print("seeded {slug} pack={pack_id}".format(**result))
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

    if command == "rate-limits":
        return _rate_limits(argv[1:])

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


def _rate_limits(argv: list[str]) -> int:
    window_seconds = rate_limit.AUTH_WINDOW_SECONDS
    limit = 20
    action = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--window" and i + 1 < len(argv):
            try:
                window_seconds = int(argv[i + 1])
            except ValueError:
                print(USAGE)
                return 2
            i += 2
            continue
        if arg == "--limit" and i + 1 < len(argv):
            try:
                limit = int(argv[i + 1])
            except ValueError:
                print(USAGE)
                return 2
            i += 2
            continue
        if arg == "--action" and i + 1 < len(argv):
            action = argv[i + 1].strip().lower()
            i += 2
            continue
        print(USAGE)
        return 2
    if window_seconds < 1 or limit < 1:
        print(USAGE)
        return 2

    db.migrate()
    rows = rate_limit.recent_summary(
        window_seconds=window_seconds,
        limit=limit,
        action=action,
    )
    action_label = action or "all"
    print(f"rate_limits\twindow={window_seconds}\taction={action_label}\trows={len(rows)}")
    for row in rows:
        print(
            f"{row['action']}\tattempts={row['attempts']}"
            f"\tbucket={row['bucket']}\tfirst={row['first_seen']}"
            f"\tlast={row['last_seen']}"
        )
    return 0


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
