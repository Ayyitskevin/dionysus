"""Operational CLI helpers."""

import sys

from . import db
from .readiness import summary


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] not in {"check-production", "migrate"}:
        print("usage: python -m app.cli [check-production|migrate]")
        return 2
    if argv[0] == "migrate":
        db.migrate()
        print("migrations applied")
        return 0
    report = summary()
    for item in report["checks"]:
        mark = "ok" if item["ok"] else "fail"
        print(f"{mark}\t{item['key']}\t{item['detail']}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
