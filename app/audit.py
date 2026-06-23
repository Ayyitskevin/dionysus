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


def _actor_label(event: dict) -> str:
    if event["actor_name"] and event["actor_email"]:
        return f"{event['actor_name']} ({event['actor_email']})"
    if event["actor_email"]:
        return event["actor_email"]
    return "System"


def _event(row) -> dict:
    event = dict(row)
    event["actor_label"] = _actor_label(event)
    try:
        event["details"] = json.loads(event["details_json"] or "{}")
    except json.JSONDecodeError:
        event["details"] = {}
    return event


def for_org(org_id: int, *, action: str = "", actor: str = "",
            date_from: str = "", date_to: str = "", limit: int = 25) -> list[dict]:
    clauses = ["ae.org_id=?"]
    params: list = [org_id]
    if action:
        clauses.append("ae.action=?")
        params.append(action)
    if actor == "system":
        clauses.append("ae.actor_user_id IS NULL")
    elif actor:
        try:
            actor_id = int(actor)
        except ValueError:
            actor_id = 0
        clauses.append("ae.actor_user_id=?")
        params.append(actor_id)
    if date_from:
        clauses.append("date(ae.created_at) >= date(?)")
        params.append(date_from)
    if date_to:
        clauses.append("date(ae.created_at) <= date(?)")
        params.append(date_to)
    params.append(limit)
    rows = db.all_(f"""SELECT ae.*, u.name AS actor_name, u.email AS actor_email
                       FROM audit_events ae
                       LEFT JOIN users u ON u.id=ae.actor_user_id
                       WHERE {' AND '.join(clauses)}
                       ORDER BY ae.created_at DESC, ae.id DESC
                       LIMIT ?""", tuple(params))
    return [_event(row) for row in rows]


def recent_for_org(org_id: int, *, limit: int = 12) -> list[dict]:
    return for_org(org_id, limit=limit)


def actions_for_org(org_id: int) -> list[str]:
    rows = db.all_("""SELECT DISTINCT action FROM audit_events
                      WHERE org_id=?
                      ORDER BY action""", (org_id,))
    return [row["action"] for row in rows]


def actors_for_org(org_id: int) -> list[dict]:
    rows = db.all_("""SELECT DISTINCT ae.actor_user_id, u.name, u.email
                      FROM audit_events ae
                      LEFT JOIN users u ON u.id=ae.actor_user_id
                      WHERE ae.org_id=?
                      ORDER BY CASE WHEN ae.actor_user_id IS NULL THEN 1 ELSE 0 END,
                               u.email""", (org_id,))
    actors = []
    for row in rows:
        if row["actor_user_id"] is None:
            actors.append({"value": "system", "label": "System"})
        else:
            label = row["email"]
            if row["name"] and row["email"]:
                label = f"{row['name']} ({row['email']})"
            actors.append({"value": str(row["actor_user_id"]), "label": label})
    return actors


def get_for_org(org_id: int, event_id: int) -> dict | None:
    row = db.one("""SELECT ae.*, u.name AS actor_name, u.email AS actor_email
                    FROM audit_events ae
                    LEFT JOIN users u ON u.id=ae.actor_user_id
                    WHERE ae.org_id=? AND ae.id=?""", (org_id, event_id))
    return _event(row) if row else None
