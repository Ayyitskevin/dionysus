"""Deterministic AI-pack drafting.

This is the local product engine. It produces useful drafts now and leaves a clean
replacement point for Odysseus/OpenAI later: generated text is stored as draft
output with provenance, never treated as approved human work.
"""

import json

from . import db


def _menu_lines(org_id: int) -> list[str]:
    rows = db.all_("SELECT name, category, notes FROM menu_items WHERE org_id=? ORDER BY id",
                   (org_id,))
    return [f"{r['name']} ({r['category'] or 'menu'})" for r in rows] or [
        "seasonal hero dish", "signature drink", "interior ambience"]


def _headline(title: str) -> str:
    clean = title.strip()
    return clean if clean.lower().endswith(("pack", "kit")) else f"{clean} content pack"


def build_pack(org, campaign, recipe, *, argus_context: dict | None = None) -> dict:
    menu = _menu_lines(org["id"])
    audience = org["audience"]
    voice = org["brand_voice"] or "warm, specific, restaurant-native"
    offer = campaign["goal"] or "turn this shoot into reusable marketing"
    title = campaign["title"]
    first = menu[0]
    second = menu[1] if len(menu) > 1 else menu[0]
    argus = argus_context or {}
    hero_frames = argus.get("hero_frames") or []
    top_keywords = argus.get("top_keywords") or []
    keyword_hint = ", ".join(top_keywords[:3]) if top_keywords else ""

    if audience == "photographer":
        shot_list = [
            f"Opening frame: {first} with negative space for client copy",
            f"Hands/process detail that proves craft for {title}",
            f"Owner or chef portrait with environment context",
            f"Vertical reel opener built around {second}",
            "Wide room or storefront frame for press and website use",
        ]
        if hero_frames:
            shot_list = [f"Argus keeper: {frame}" for frame in hero_frames] + shot_list[:2]
        captions = [
            f"Delivered for {org['name']}: a {recipe['name'].lower()} built around {first}.",
            f"Use this set to pitch the next monthly content day: {offer}.",
            f"Behind the image: shape the light, protect the texture, sell the appetite.",
        ]
        if keyword_hint:
            captions[0] = (
                f"Delivered for {org['name']}: {recipe['name'].lower()} anchored on "
                f"{keyword_hint}."
            )
        upsells = [
            "Offer a monthly refresh plan with 12 social-ready exports.",
            "Bundle usage-language and delivery-app crops as a paid add-on.",
            "Send a one-page campaign brief before the shoot to reduce client revisions.",
        ]
    else:
        shot_list = [
            f"Hero plate: {first}, tight crop plus overhead variation",
            f"Menu support: {second} with ingredient cue",
            "Dining-room frame that shows the experience, not just the food",
            "Team/process frame for trust and hospitality",
            "Vertical phone-first clip for the week's leading post",
        ]
        if hero_frames:
            shot_list = [f"Hero select: {frame}" for frame in hero_frames] + shot_list[:2]
        captions = [
            f"{first} is the kind of table moment people plan around.",
            f"New this week: {second}. Built for regulars, easy to share.",
            f"From kitchen to table, this is the story behind {org['name']}.",
        ]
        if keyword_hint:
            captions[1] = f"On the menu now: {second} — {keyword_hint}."
        upsells = [
            "Book the next seasonal menu refresh before the current pack goes stale.",
            "Ask for DoorDash/Uber Eats hero crops from the same selects.",
            "License a paid-ad version if the post becomes a campaign.",
        ]

    channels = json.loads(recipe["channels"])
    provenance = {"engine": "dionysus-local-draft", "recipe": recipe["slug"]}
    if argus.get("run_id"):
        provenance["argus_run_id"] = argus["run_id"]
        provenance["engine"] = "dionysus-argus-enriched"
    return {
        "headline": _headline(title),
        "strategy": f"{recipe['name']} for {org['name']}: {offer}. Voice: {voice}.",
        "shot_list": shot_list,
        "captions": captions,
        "exports": [f"{channel}: {recipe['deliverable_note']}" for channel in channels],
        "upsells": upsells,
        "argus": argus or None,
        "provenance": provenance,
    }


def _text_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _shorten(value: str, limit: int = 118) -> str:
    clean = " ".join(value.split())
    first_sentence = clean.split(".", 1)[0].strip()
    if first_sentence:
        clean = first_sentence
    if len(clean) > limit:
        clean = f"{clean[:limit - 3].rstrip()}..."
    return clean


def regenerate_with_feedback(org, source_pack, feedback: str) -> dict:
    """Create a new draft pack body from an existing pack plus owner feedback.

    This remains deterministic until a hosted model is wired in; the route treats
    the output as a draft and keeps the source pack untouched.
    """
    feedback = " ".join(feedback.split())
    lower = feedback.lower()
    source = json.loads(source_pack["body_json"])
    shot_list = _text_list(source.get("shot_list"))
    captions = _text_list(source.get("captions"))
    exports = _text_list(source.get("exports"))
    upsells = _text_list(source.get("upsells"))
    strategy = str(source.get("strategy") or "").strip()
    if not captions:
        captions = [f"{org['name']} has a clear campaign angle ready to refine."]
    if not shot_list:
        shot_list = ["Hero frame with clear subject, light, and client-facing purpose"]

    premium = any(word in lower for word in (
        "premium", "upscale", "elevated", "elegant", "luxury", "polished"))
    delivery = any(word in lower for word in (
        "delivery", "doordash", "uber eats", "ubereats", "ordering", "takeout"))
    short = any(word in lower for word in (
        "short", "shorter", "concise", "tight", "tighter"))
    social = any(word in lower for word in (
        "social", "instagram", "reel", "reels", "tiktok"))
    reservation = any(word in lower for word in (
        "reservation", "reserve", "book", "tables", "table"))

    strategy_notes = [strategy] if strategy else []
    if premium:
        strategy_notes.append(
            "Elevated direction: make the language premium, chef-led, and specific.")
        captions = [f"Elevated angle: {caption}" for caption in captions]
        upsells.insert(
            0,
            "Offer a premium usage bundle for web, paid social, and reservation campaigns.",
        )
    if delivery:
        strategy_notes.append(
            "Delivery direction: prioritize ordering-platform clarity and item-level conversion.")
        exports = [
            "delivery_apps: concise hero copy, modifier notes, and ordering CTA",
        ] + [item for item in exports if not item.lower().startswith("delivery_apps:")]
        captions.append(
            "Delivery angle: lead with the dish benefit, then send guests straight to order.")
        shot_list.append(
            "Delivery crop: tight item frame with clean negative space for app menus")
    if social:
        strategy_notes.append("Social direction: make the first frame and caption hook faster.")
        exports = [
            "reels: vertical opener, hook caption, and save/share CTA",
        ] + [item for item in exports if not item.lower().startswith("reels:")]
        shot_list.append("Vertical reel opener with motion, hands, or sauce pull")
    if reservation:
        strategy_notes.append("Reservation direction: move guests toward booking a table.")
        captions.append("Reserve this week's table around the dish people will remember.")
        exports.append("reservation_cta: booking prompt for email, social, and website")
    if not any((premium, delivery, short, social, reservation)):
        captions[0] = f"{captions[0]} Direction: {feedback}."
        exports.insert(0, f"client_feedback: {feedback}")

    if short:
        captions = [_shorten(caption) for caption in captions[:3]]
        strategy_notes.append("Concise direction: keep captions direct and skimmable.")

    provenance = dict(source.get("provenance") or {})
    source_engine = provenance.get("engine")
    provenance.update({
        "engine": "dionysus-feedback-regenerate",
        "source_engine": source_engine,
        "source_pack_id": source_pack["id"],
        "feedback": feedback,
    })
    title = f"{source_pack['title']} feedback draft"
    return {
        "headline": title,
        "strategy": " ".join(strategy_notes + [f"Feedback direction: {feedback}."]),
        "shot_list": _unique(shot_list),
        "captions": _unique(captions),
        "exports": _unique(exports),
        "upsells": _unique(upsells),
        "argus": source.get("argus"),
        "provenance": provenance,
    }
