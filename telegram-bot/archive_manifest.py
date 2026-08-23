"""Build privacy-safe archive manifests for the Content-Seller-Bot media library.

The manifest intentionally excludes Telegram `file_id` values.  Those values are
operational identifiers for one bot, not stable archive identifiers.  A manifest
is paired with the user's original video files in a private archive such as
MEGA, allowing category assignments to be restored without re-sorting media.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

ARCHIVE_MANIFEST_SCHEMA_VERSION = 1


def _categories(video: dict[str, Any], default_category: str) -> list[str]:
    values = video.get("categories")
    if not isinstance(values, list):
        values = [video.get("category")] if video.get("category") else []
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name:
            continue
        if name == "כללי":
            name = default_category
        if name not in cleaned:
            cleaned.append(name)
    return cleaned or [default_category]


def _safe_file_component(name: str | None, entry_id: str, position: int) -> str:
    """Build a suggested archive filename; it never changes production data."""
    base = (name or "").strip()
    if not base:
        base = f"video_{position:04d}.mp4"
    base = re.sub(r"[^\w.\-() ]+", "_", base, flags=re.UNICODE).strip(" .")
    return f"CSB_{entry_id}__{base or f'video_{position:04d}.mp4'}"


def build_archive_manifest(videos: list[Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable archive manifest for the current library."""
    default_category = "רנדומלי"
    configured_categories = settings.get("categories", []) if isinstance(settings, dict) else []
    categories: list[str] = []
    for item in configured_categories:
        if isinstance(item, str) and item.strip():
            normalized = default_category if item.strip() == "כללי" else item.strip()
            if normalized not in categories:
                categories.append(normalized)
    if default_category not in categories:
        categories.insert(0, default_category)

    exported: list[dict[str, Any]] = []
    for index, raw_video in enumerate(videos, start=1):
        if not isinstance(raw_video, dict):
            continue
        entry_id = str(raw_video.get("entry_id") or f"legacy-{index}")
        file_name = raw_video.get("file_name")
        exported.append(
            {
                "library_position": index,
                "entry_id": entry_id,
                "suggested_archive_filename": _safe_file_component(file_name, entry_id, index),
                "original_file_name": file_name or None,
                "file_unique_id": raw_video.get("file_unique_id") or None,
                "duration_seconds": int(raw_video.get("duration") or 0),
                "file_size_bytes": int(raw_video.get("file_size") or 0),
                "categories": _categories(raw_video, default_category),
                "file_status": raw_video.get("file_status") or "usable",
            }
        )

    return {
        "schema_version": ARCHIVE_MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "Content-Seller-Bot",
        "default_category": default_category,
        "category_order_mode": settings.get("category_order_mode", "alphabetical") if isinstance(settings, dict) else "alphabetical",
        "categories": categories,
        "library_video_count": len(exported),
        "videos": exported,
        "instructions": {
            "privacy": "Keep this manifest and the original media files in a private archive. Do not publish a public sharing link.",
            "matching": "The recommended archive filename contains the stable entry_id. Preserve it when uploading a file to the archive. If files are kept under different names, retain this manifest so a controlled import can match duration, size and file_unique_id where available.",
            "telegram": "Telegram file_id values are intentionally excluded because they are not a portable media archive identifier.",
        },
    }


def archive_manifest_bytes(videos: list[Any], settings: dict[str, Any]) -> bytes:
    manifest = build_archive_manifest(videos, settings)
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
