"""Load exact frozen guideline strings with provenance validation."""

from __future__ import annotations

import json
from pathlib import Path

from src.optimization.audit import text_sha256


def load_guidelines(
    bundle: Path,
    labels: tuple[str, ...],
) -> tuple[dict[str, str], dict]:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = {
        str(item["label"]): item
        for item in manifest.get("selected", [])
    }
    result: dict[str, str] = {}
    for label in labels:
        if label not in selected:
            raise ValueError(f"guideline label absent from bundle: {label}")
        item = selected[label]
        path = bundle / str(item["path"])
        text = path.read_text(encoding="utf-8")
        if text_sha256(text) != item.get("guideline_sha256"):
            raise ValueError(f"guideline semantic hash mismatch: {label}")
        result[label] = text
    return result, manifest
