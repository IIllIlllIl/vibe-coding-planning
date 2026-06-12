"""Add official PolyBench task categories to an immutable checker snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.internal.evaluate_checker import (  # noqa: E402
    _read_jsonl,
    _write_json,
    _write_jsonl,
)
from src.config import load_config  # noqa: E402
from src.data.instance_loader import InstanceLoader  # noqa: E402

DEFAULT_INPUT = Path(
    "output/SWE-PolyBench/polybench-pct-checker-datasets/"
    "20260609_198_cdf4d414e401/cases.jsonl"
)
DERIVATION_POLICY = "official-polybench-task-category-v1"
FILE_NAMES = {
    "Bug Fix": "bug_fix_cases.jsonl",
    "Feature": "feature_cases.jsonl",
    "Refactoring": "refactoring_cases.jsonl",
    "Security": "security_cases.jsonl",
    "Testing": "testing_cases.jsonl",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def label_task_categories(
    *,
    input_path: Path,
    output_dir: Path,
    loader: InstanceLoader,
) -> dict[str, Any]:
    """Create labeled full data and category subsets without editing input."""
    cases = _read_jsonl(input_path)
    labeled: list[dict[str, Any]] = []
    for case in cases:
        instance_id = str(case["instance_id"])
        metadata = loader.load_instance(instance_id)
        category = metadata.get("task_category")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(
                f"PolyBench task_category missing for {instance_id}"
            )
        labeled.append({**case, "task_category": category.strip()})

    output_dir.mkdir(parents=True, exist_ok=True)
    labeled_path = output_dir / "cases.jsonl"
    _write_jsonl(labeled_path, labeled)

    counts = Counter(case["task_category"] for case in labeled)
    subset_files: dict[str, dict[str, Any]] = {}
    for category in sorted(counts):
        file_name = FILE_NAMES.get(
            category,
            f"{category.lower().replace(' ', '_')}_cases.jsonl",
        )
        subset_path = output_dir / file_name
        subset = [
            case for case in labeled if case["task_category"] == category
        ]
        _write_jsonl(subset_path, subset)
        subset_files[category] = {
            "path": _portable_path(subset_path),
            "sha256": _sha256(subset_path),
            "instances": len(subset),
            "resolved": sum(case["resolved"] is True for case in subset),
            "unresolved": sum(case["resolved"] is False for case in subset),
        }

    manifest = {
        "schema_version": 1,
        "derivation_policy": DERIVATION_POLICY,
        "label_field": "task_category",
        "label_source": "AmazonScience/SWE-PolyBench.task_category",
        "source_cases_path": _portable_path(input_path),
        "source_cases_sha256": _sha256(input_path),
        "labeled_cases_path": _portable_path(labeled_path),
        "labeled_cases_sha256": _sha256(labeled_path),
        "instances": len(labeled),
        "category_counts": dict(sorted(counts.items())),
        "subsets": subset_files,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="configs/polybench_full199_pct.yaml"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Default: <snapshot>/derived/task_category_v1. The source "
            "cases.jsonl is never modified."
        ),
    )
    args = parser.parse_args()
    output_dir = args.output_dir or (
        args.input.parent / "derived" / "task_category_v1"
    )
    config = load_config(args.config)
    loader = InstanceLoader(
        dataset=config.system.dataset,
        dataset_type=config.system.dataset_type,
        language_filter=config.system.language_filter,
    )
    manifest = label_task_categories(
        input_path=args.input,
        output_dir=output_dir,
        loader=loader,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
