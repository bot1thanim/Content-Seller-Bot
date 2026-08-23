"""Regression tests for category archive export.

Run with: python3 test_archive_manifest.py
"""

from __future__ import annotations

import json

from archive_manifest import archive_manifest_bytes, build_archive_manifest


def main():
    videos = [
        {
            "entry_id": "aaa111",
            "file_id": "telegram-only-id-must-not-leak",
            "file_unique_id": "unique-a",
            "file_name": "סרטון ראשון.mp4",
            "duration": 26,
            "file_size": 1500,
            "category": "כללי",
            "categories": ["כללי", "ישראלי"],
        },
        {
            "entry_id": "bbb222",
            "file_id": "another-telegram-id",
            "duration": 80,
            "file_size": 2600,
            "category": "קצר",
        },
    ]
    manifest = build_archive_manifest(
        videos,
        {"categories": ["כללי", "ישראלי", "קצר"], "category_order_mode": "manual"},
    )

    assert manifest["library_video_count"] == 2
    assert manifest["default_category"] == "רנדומלי"
    assert manifest["category_order_mode"] == "manual"
    assert "כללי" not in manifest["categories"]
    assert manifest["videos"][0]["categories"] == ["רנדומלי", "ישראלי"]
    assert manifest["videos"][1]["categories"] == ["קצר"]
    assert "aaa111" in manifest["videos"][0]["suggested_archive_filename"]

    encoded = archive_manifest_bytes(videos, {"categories": []})
    rendered = encoded.decode("utf-8")
    assert "telegram-only-id-must-not-leak" not in rendered
    assert "another-telegram-id" not in rendered
    assert json.loads(rendered)["videos"][0]["entry_id"] == "aaa111"
    print("All archive manifest tests passed.")


if __name__ == "__main__":
    main()
