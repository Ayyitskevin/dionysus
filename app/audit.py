"""Workspace audit trail helpers."""

import json

from . import db


def actor_id(user) -> int | None:
    return user["id"] if user else None


def log_event(org_id: int, action: str, *, actor_user_id: int | None = None,
              entity_type: str | None = None, entity_id: int | None = None,
              summary: str, details: dict | None = None) -> None:
    payload = json.dumps(details or {}, sort_keys=True)
    db.run("""INSERT INTO audit_events
              (org_id, actor_user_id, action, entity_type, entity_id, summary, details_json)
              VALUES (?,?,?,?,?,?,?)""",
           (org_id, actor_user_id, action, entity_type, entity_id, summary, payload))


def recent_for_org(org_id: int, *, limit: int = 12) -> list[dict]:
    rows = db.all_("""SELECT ae.*, u.name AS actor_name, u.email AS actor_email
                      FROM audit_events ae
                      LEFT JOIN users u ON u.id=ae.actor_user_id
                      WHERE ae.org_id=?
                      ORDER BY ae.created_at DESC, ae.id DESC
                      LIMIT ?""", (org_id, limit))
    events = []
    for row in rows:
        event = dict(row)
        if event["actor_name"] and event["actor_email"]:
            event["actor_label"] = f"{event['actor_name']} ({event['actor_email']})"
        elif event["actor_email"]:
            event["actor_label"] = event["actor_email"]
        else:
            event["actor_label"] = "System"
        events.append(event)
    return events
