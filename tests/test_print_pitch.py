from app.print_pitch import build_print_pitch


def test_build_print_pitch_enriches_keywords():
    result = build_print_pitch(
        gallery_name="June Menu",
        photo_count=8,
        estimated_total_cents=42000,
        gallery_theme="food",
        bundles=[{
            "title": "Statement wall piece",
            "pitch": "Lead with your strongest hero.",
            "items": [{
                "label": "Canvas",
                "size": "16x20",
                "photo": {"filename": "hero.jpg", "keywords": ["risotto", "chef", "warm light"]},
            }],
        }],
    )
    assert "June Menu" in result["intro"]
    assert "Keywords that sell the story" in result["bundles"][0]["pitch"]
    assert result["provenance"]["engine"] == "dionysus-print-pitch"