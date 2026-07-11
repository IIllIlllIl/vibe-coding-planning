#!/usr/bin/env python3
"""Pre-pull Apptainer SIF images required by a GEPA configuration.

This avoids 5-10 minute on-the-fly Docker-to-SIF conversions inside the GEPA
main loop. Run it on an HPC login or compute node before submitting a GEPA job.

Examples:
    python scripts/tools/prepare_apptainer_sifs.py \
        --config configs/archive/offline_gepa/gepa_verified_rules_reflection_smoke_apptainer.yaml

    # Override SIF cache directory without editing the config:
    python scripts/tools/prepare_apptainer_sifs.py \
        --config configs/archive/offline_gepa/gepa_verified_rules_reflection_smoke_apptainer.yaml \
        --sif-cache-dir /scratch/users/${USER}/vibe-sif-cache
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.environment.apptainer_env import (  # noqa: E402
    ApptainerSifCache,
    _image_to_sif_name,
)
from src.environment.docker_env import DockerCapacityWindow  # noqa: E402
from src.evaluator.swe_evaluator import derive_image_name  # noqa: E402
from src.optimization.config import load_optimization_config  # noqa: E402
from src.optimization.dataset import load_snapshot  # noqa: E402


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
        default=0,
        help="Timeout per SIF pull in seconds; 0 disables the per-pull timeout (default: 0)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Attempts per missing SIF image before marking it failed (default: 3)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=int,
        default=60,
        help="Seconds to wait between failed pull attempts (default: 60)",
    )
    parser.add_argument(
        "--failed-output",
        help=(
            "Write failed image refs to this file "
            "(default: <sif-cache-dir>/preheat_failed_images.txt)"
        ),
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


def _pull_with_retries(
    cache: ApptainerSifCache,
    image: str,
    *,
    timeout: int | None,
    max_attempts: int,
    retry_backoff: int,
) -> tuple[bool, str | None]:
    """Try to pull one missing image, returning success and final error text."""
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            cache.ensure(image, timeout=timeout)
            return True, None
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            print(
                f"  RETRYABLE_FAILURE attempt={attempt}/{max_attempts} "
                f"image={image}: {last_error}",
                file=sys.stderr,
                flush=True,
            )
            if attempt < max_attempts and retry_backoff > 0:
                time.sleep(retry_backoff)
    return False, last_error


def main() -> int:
    args = _parse_args()
    config = load_optimization_config(args.config, require_api_keys=False)

    if config.container.runtime != "apptainer":
        print(
            f"ERROR: config container.runtime is {config.container.runtime!r}; "
            "this tool is only needed for apptainer runs",
            file=sys.stderr,
        )
        return 2
    if args.max_attempts < 1:
        print("ERROR: --max-attempts must be >= 1", file=sys.stderr)
        return 2
    if args.timeout < 0:
        print("ERROR: --timeout must be >= 0", file=sys.stderr)
        return 2
    if args.retry_backoff < 0:
        print("ERROR: --retry-backoff must be >= 0", file=sys.stderr)
        return 2

    sif_cache_dir = Path(
        args.sif_cache_dir or config.container.sif_cache_dir
    )
    sif_cache_dir.mkdir(parents=True, exist_ok=True)
    failed_output = Path(
        args.failed_output
        or (sif_cache_dir / "preheat_failed_images.txt")
    )

    window = DockerCapacityWindow(
        max_concurrent=1,
        max_cached_images=1,
        min_free_gb=1,
    )
    cache = ApptainerSifCache(sif_cache_dir, window)

    images = _collect_images(config)
    print(f"Preparing {len(images)} SIF image(s) in {sif_cache_dir}")

    pull_timeout = None if args.timeout == 0 else args.timeout
    pulled = 0
    cached = 0
    failures: list[tuple[str, str | None]] = []
    for image in images:
        sif = cache.sif_path(image)
        if sif.exists():
            size_mb = sif.stat().st_size / (1024 * 1024)
            print(f"  CACHED {_image_to_sif_name(image)} ({size_mb:.1f} MiB)")
            cached += 1
            continue
        print(f"  PULLING {image} -> {sif.name}")
        ok, error = _pull_with_retries(
            cache,
            image,
            timeout=pull_timeout,
            max_attempts=args.max_attempts,
            retry_backoff=args.retry_backoff,
        )
        if ok:
            size_mb = sif.stat().st_size / (1024 * 1024)
            print(f"  OK {_image_to_sif_name(image)} ({size_mb:.1f} MiB)")
            pulled += 1
        else:
            print(f"  FAILED {image}: {error}", file=sys.stderr)
            failures.append((image, error))

    if failures:
        failed_output.parent.mkdir(parents=True, exist_ok=True)
        failed_output.write_text(
            "".join(f"{image}\t{error or ''}\n" for image, error in failures),
            encoding="utf-8",
        )
        print(f"Failed image list written to {failed_output}", file=sys.stderr)
    else:
        failed_output.unlink(missing_ok=True)

    print(
        f"Summary: {cached} cached, {pulled} pulled, {len(failures)} failed "
        f"out of {len(images)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
