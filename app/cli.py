"""Operational CLI helpers."""

from pathlib import Path
import sys

from . import backups, db, seed
from .readiness import summary


USAGE = (
    "usage: python -m app.cli "
    "[backup [destination-dir]|check-production|migrate|seed-demo|verify-backup <db>]"
)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
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


def _print_verification(label: str, result: backups.VerificationResult) -> None:
    print(
        f"{label}\tok\tintegrity={result.integrity}"
        f"\tmigrations={len(result.applied_migrations)}"
        f"\ttables={result.table_count}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
