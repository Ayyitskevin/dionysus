"""Print upsell pitch drafts for Plutus hand-off."""

from __future__ import annotations

from typing import Any


def _keywords_from_bundle(bundle: dict[str, Any]) -> list[str]:
    words: list[str] = []
    for item in bundle.get("items") or []:
        photo = item.get("photo") if isinstance(item.get("photo"), dict) else {}
        raw = photo.get("keywords")
        if isinstance(raw, list):
            words.extend(str(w) for w in raw if w)
        elif isinstance(raw, str) and raw.strip():
            words.append(raw.strip())
    return words[:4]


def _theme_intro(gallery_name: str, *, theme: str | None, photo_count: int, bundle_count: int) -> str:
    if theme == "food":
        return (
            f'Your gallery "{gallery_name}" is ready — {photo_count} selects tuned for '
            f"wall art, chef's table, and menu storytelling."
        )
    if theme == "wedding":
        return (
            f'"{gallery_name}" is ready — {photo_count} keeper frames distilled into '
            f"{bundle_count} heirloom print option"
            f"{'' if bundle_count == 1 else 's'}."
        )
    return (
        f'Hi — your gallery "{gallery_name}" is ready, and a few print ideas stood out.'
    )


def build_print_pitch(
    *,
    gallery_name: str,
    bundles: list[dict[str, Any]],
    photo_count: int,
    estimated_total_cents: int,
    gallery_theme: str | None = None,
    argus_run_id: int | None = None,
) -> dict[str, Any]:
    """Deterministic pitch enrichment — same draft posture as generator.py."""
    intro = _theme_intro(
        gallery_name,
        theme=gallery_theme,
        photo_count=photo_count,
        bundle_count=len(bundles),
    )
    enhanced: list[dict[str, str]] = []
    for bundle in bundles:
        title = str(bundle.get("title") or "Bundle")
        base = str(bundle.get("pitch") or "").strip()
        keywords = _keywords_from_bundle(bundle)
        if keywords:
            hint = ", ".join(keywords[:3])
            pitch = f"{base} Keywords that sell the story: {hint}." if base else (
                f"Built around {hint} — a strong client-facing upsell."
            )
        elif base:
            pitch = base
        else:
            pitch = f"A curated {title.lower()} from your strongest selects."
        enhanced.append({"title": title, "pitch": pitch})

    provenance = {"engine": "dionysus-print-pitch"}
    if argus_run_id:
        provenance["argus_run_id"] = argus_run_id
    return {
        "intro": intro,
        "bundles": enhanced,
        "estimated_total_cents": estimated_total_cents,
        "provenance": provenance,
    }