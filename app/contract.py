"""Mise Worker-Contract draft envelope.

Maps Dionysus's internal draft bodies onto the canonical structured-output
contract every Mise content worker must speak:

    {"drafts": [{"kind", "title", "body", "alt_text"}],
     "model": "...", "latency_ms": 0, "cost_usd": 0.0}

Local deterministic drafting reports ``model="dionysus-local-draft"`` and
``cost_usd=0.0``. When a local/hosted model is wired in (see the planned
``app/model_client.py``) it fills in the real model name, measured latency, and
cost; the envelope shape does not change.

Every output remains a reversible draft a human accepts in Mise — this module
only describes drafts, it never publishes them.
"""

from __future__ import annotations

LOCAL_MODEL = "dionysus-local-draft"

# The draft kinds Mise understands. Keep in sync with the Worker Contract.
DRAFT_KINDS = (
    "caption",
    "gallery_description",
    "campaign_pack",
    "email",
    "social",
)

_DRAFT_KEYS = frozenset({"kind", "title", "body", "alt_text"})


def draft(kind: str, body, *, title=None, alt_text=None) -> dict:
    """Build one validated draft. Empty ``title``/``alt_text`` are dropped."""
    if kind not in DRAFT_KINDS:
        raise ValueError(f"unknown draft kind: {kind!r}")
    text = "" if body is None else str(body).strip()
    if not text:
        raise ValueError("draft body is required")
    item: dict = {"kind": kind, "body": text}
    if title is not None and str(title).strip():
        item["title"] = str(title).strip()
    if alt_text is not None and str(alt_text).strip():
        item["alt_text"] = str(alt_text).strip()
    return item


def envelope(drafts, *, model: str = LOCAL_MODEL, latency_ms: int = 0,
             cost_usd: float = 0.0) -> dict:
    """Wrap drafts in the contract envelope, validating before returning."""
    env = {
        "drafts": list(drafts),
        "model": model,
        "latency_ms": max(int(latency_ms), 0),
        "cost_usd": round(float(cost_usd), 6),
    }
    validate_envelope(env)
    return env


def _lines(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def drafts_from_pack(body: dict, *, title: str | None = None) -> list[dict]:
    """Map a generator/regenerator pack body onto contract drafts.

    Produces one ``campaign_pack`` draft (strategy plus the assembled shot
    list / exports / upsells) and one ``caption`` draft per generated caption,
    so Mise can accept or edit each independently.
    """
    body = body or {}
    headline = str(body.get("headline") or title or "Campaign pack").strip()
    sections: list[str] = []
    strategy = str(body.get("strategy") or "").strip()
    if strategy:
        sections.append(strategy)
    for label, key in (("Shot list", "shot_list"), ("Exports", "exports"),
                       ("Upsells", "upsells")):
        items = _lines(body.get(key))
        if items:
            sections.append(f"{label}:\n" + "\n".join(f"- {item}" for item in items))
    pack_body = "\n\n".join(sections) or headline
    drafts = [draft("campaign_pack", pack_body, title=headline)]
    for caption in _lines(body.get("captions")):
        drafts.append(draft("caption", caption))
    return drafts


def drafts_from_print_pitch(pitch: dict) -> list[dict]:
    """Map a print-pitch body onto contract drafts (intro + bundle blurbs)."""
    pitch = pitch or {}
    drafts: list[dict] = []
    intro = str(pitch.get("intro") or "").strip()
    if intro:
        drafts.append(draft("email", intro, title="Gallery print pitch"))
    for bundle in pitch.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        text = str(bundle.get("pitch") or "").strip()
        if not text:
            continue
        bundle_title = str(bundle.get("title") or "").strip() or None
        drafts.append(draft("email", text, title=bundle_title))
    return drafts


def validate_draft(item) -> None:
    if not isinstance(item, dict):
        raise ValueError("draft must be an object")
    if item.get("kind") not in DRAFT_KINDS:
        raise ValueError(f"draft.kind must be one of {DRAFT_KINDS}")
    if not isinstance(item.get("body"), str) or not item["body"].strip():
        raise ValueError("draft.body must be a non-empty string")
    for opt in ("title", "alt_text"):
        if opt in item and not isinstance(item[opt], str):
            raise ValueError(f"draft.{opt} must be a string when present")
    extra = set(item) - _DRAFT_KEYS
    if extra:
        raise ValueError(f"draft has unexpected keys: {sorted(extra)}")


def validate_envelope(env) -> None:
    """Raise ``ValueError`` if ``env`` is not a contract-valid envelope."""
    if not isinstance(env, dict):
        raise ValueError("envelope must be an object")
    for key in ("drafts", "model", "latency_ms", "cost_usd"):
        if key not in env:
            raise ValueError(f"envelope missing {key!r}")
    if not isinstance(env["model"], str) or not env["model"]:
        raise ValueError("model must be a non-empty string")
    if not isinstance(env["latency_ms"], int) or isinstance(env["latency_ms"], bool) \
            or env["latency_ms"] < 0:
        raise ValueError("latency_ms must be a non-negative integer")
    if isinstance(env["cost_usd"], bool) or not isinstance(env["cost_usd"], (int, float)) \
            or env["cost_usd"] < 0:
        raise ValueError("cost_usd must be a non-negative number")
    drafts = env["drafts"]
    if not isinstance(drafts, list) or not drafts:
        raise ValueError("drafts must be a non-empty list")
    for item in drafts:
        validate_draft(item)
