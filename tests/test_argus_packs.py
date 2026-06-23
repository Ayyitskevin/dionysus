"""Argus-enriched content pack generation (mock HTTP only)."""

from app import argus, generator


def test_summarize_export_extracts_keywords_and_heroes():
    data = {
        "run": {"id": 9},
        "photos": [
            {
                "shot_type": "hero_dish",
                "keywords": ["plating", "spring"],
                "culling": {"hero_potential": 0.9},
            },
            {
                "shot_type": "interior",
                "keywords": ["ambience"],
                "culling": {"hero_potential": 0.4},
            },
        ],
    }
    ctx = argus.summarize_export(data, run_id=9)
    assert ctx["photo_count"] == 2
    assert "plating" in ctx["top_keywords"]
    assert len(ctx["hero_frames"]) == 1


def test_build_pack_uses_argus_context():
    org = {"id": 1, "name": "Blue Plate", "audience": "restaurant", "brand_voice": "warm"}
    campaign = {"title": "June pack", "goal": "fill tables"}
    recipe = {
        "slug": "monthly-retainer",
        "name": "Monthly Retainer Pack",
        "channels": '["instagram"]',
        "deliverable_note": "captions and shot priorities",
    }
    ctx = {
        "run_id": 12,
        "top_keywords": ["plating", "cocktail"],
        "hero_frames": ["hero dish — plating"],
    }
    pack = generator.build_pack(org, campaign, recipe, argus_context=ctx)
    assert pack["provenance"]["engine"] == "dionysus-argus-enriched"
    assert pack["provenance"]["argus_run_id"] == 12
    assert any("Hero select" in line for line in pack["shot_list"])
    assert "plating" in pack["captions"][1]