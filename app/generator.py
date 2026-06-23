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
