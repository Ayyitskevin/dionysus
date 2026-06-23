"""Operational CLI helpers."""

import sys

from . import db, seed
from .readiness import summary


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    commands = {"check-production", "migrate", "seed-demo"}
    if not argv or argv[0] not in commands:
        print("usage: python -m app.cli [check-production|migrate|seed-demo]")
        return 2
    if argv[0] == "migrate":
        db.migrate()
        print("migrations applied")
        return 0
    if argv[0] == "seed-demo":
        result = seed.seed_demo_workspace()
        print("seeded {slug} pack={pack_id} share={share_url}".format(**result))
        return 0
    report = summary()
    for item in report["checks"]:
        mark = "ok" if item["ok"] else "fail"
        print(f"{mark}\t{item['key']}\t{item['detail']}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
