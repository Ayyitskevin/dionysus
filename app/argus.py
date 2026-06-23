"""Read-only Argus run export client for content-pack enrichment."""

from __future__ import annotations

import logging
from collections import Counter

import httpx

from . import config

log = logging.getLogger("dionysus.argus")


class ArgusError(Exception):
    pass


def is_enabled() -> bool:
    return bool(config.ARGUS_URL and config.ARGUS_API_TOKEN)


def fetch_run_context(run_id: int) -> dict | None:
    """Summarize one Argus run into pack-building hints. Returns None when disarmed."""
    if not is_enabled() or run_id <= 0:
        return None
    base = config.ARGUS_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {config.ARGUS_API_TOKEN}"}
    try:
        with httpx.Client(timeout=config.ARGUS_TIMEOUT) as client:
            resp = client.get(f"{base}/runs/{run_id}/export", headers=headers)
    except httpx.RequestError as exc:
        raise ArgusError(f"Argus unreachable: {exc}") from exc
    if resp.status_code == 401:
        raise ArgusError("Argus rejected the bearer token")
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise ArgusError(f"Argus returned HTTP {resp.status_code}")
    data = resp.json()
    return summarize_export(data, run_id=run_id)


def summarize_export(data: dict, *, run_id: int) -> dict:
    photos = data.get("photos") or []
    keywords: Counter[str] = Counter()
    shot_types: Counter[str] = Counter()
    heroes: list[str] = []

    for photo in photos:
        shot = (photo.get("shot_type") or "other").replace("_", " ")
        shot_types[shot] += 1
        for kw in photo.get("keywords") or []:
            word = str(kw).strip().lower()
            if word:
                keywords[word] += 1
        hero = float((photo.get("culling") or {}).get("hero_potential") or 0)
        if hero >= 0.75:
            label = shot
            if photo.get("keywords"):
                label = f"{shot} — {photo['keywords'][0]}"
            heroes.append(label)

    top_keywords = [w for w, _ in keywords.most_common(6)]
    top_shots = [s for s, _ in shot_types.most_common(4)]
    return {
        "run_id": run_id,
        "photo_count": len(photos),
        "top_keywords": top_keywords,
        "top_shot_types": top_shots,
        "hero_frames": heroes[:5],
    }