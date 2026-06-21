#!/usr/bin/env python3
"""Pre-pull Apptainer SIF images required by a GEPA configuration.

This avoids 5-10 minute on-the-fly Docker-to-SIF conversions inside the GEPA
main loop. Run it on an HPC login or compute node before submitting a GEPA job.

Examples:
    python scripts/tools/prepare_apptainer_sifs.py \
        --config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml

    # Override SIF cache directory without editing the config:
    python scripts/tools/prepare_apptainer_sifs.py \
        --config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
        --sif-cache-dir /scratch/users/twang/vibe-sif-cache
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.environment.apptainer_env import (
    ApptainerSifCache,
    _image_to_sif_name,
)
from src.environment.docker_env import DockerCapacityWindow
from src.evaluator.swe_evaluator import derive_image_name
from src.optimization.config import load_optimization_config
from src.optimization.dataset import load_snapshot


REFLECTION_IMAGE = "python:3.12-slim"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-pull Apptainer SIF images for a GEPA run",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the GEPA YAML config",
    )
    parser.add_argument(
        "--sif-cache-dir",
        help="Override the SIF cache directory from the config",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per SIF pull in seconds (default: 1800)",
    )
    return parser.parse_args()


def _collect_images(config) -> list[str]:
    """Return all Docker image references needed by the GEPA config."""
    images = [REFLECTION_IMAGE]
    train, validation = load_snapshot(config.dataset_snapshot)
    seen: set[str] = set()
    for case in (*train, *validation):
        instance_info = case.checker_payload()["repository"]
        image = derive_image_name(instance_info)
        if image not in seen:
            images.append(image)
            seen.add(image)
    return images


def main() -> int:
    args = _parse_args()
    config = load_optimization_config(args.config)

    if config.container.runtime != "apptainer":
        print(
            f"ERROR: config container.runtime is {config.container.runtime!r}; "
            "this tool is only needed for apptainer runs",
            file=sys.stderr,
        )
        return 2

    sif_cache_dir = Path(
        args.sif_cache_dir or config.container.sif_cache_dir
    )
    sif_cache_dir.mkdir(parents=True, exist_ok=True)

    window = DockerCapacityWindow(
        max_concurrent=1,
        max_cached_images=1,
        min_free_gb=1,
    )
    cache = ApptainerSifCache(sif_cache_dir, window)

    images = _collect_images(config)
    print(f"Preparing {len(images)} SIF image(s) in {sif_cache_dir}")

    pulled = 0
    cached = 0
    failed = 0
    for image in images:
        sif = cache.sif_path(image)
        if sif.exists():
            size_mb = sif.stat().st_size / (1024 * 1024)
            print(f"  CACHED {_image_to_sif_name(image)} ({size_mb:.1f} MiB)")
            cached += 1
            continue
        print(f"  PULLING {image} -> {sif.name}")
        try:
            cache.ensure(image, timeout=args.timeout)
            size_mb = sif.stat().st_size / (1024 * 1024)
            print(f"  OK {_image_to_sif_name(image)} ({size_mb:.1f} MiB)")
            pulled += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {image}: {exc}", file=sys.stderr)
            failed += 1

    print(
        f"Summary: {cached} cached, {pulled} pulled, {failed} failed "
        f"out of {len(images)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
