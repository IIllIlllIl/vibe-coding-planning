#!/usr/bin/env python3
"""One-time migration of output/SWE-bench_Verified/ to a batch-scoped layout.

Before:
    output/SWE-bench_Verified/
    ├─ <instance_id>/            (172 dirs, flat)
    ├─ sampled_instances_run1_50_seed42.json
    ├─ sampled_instances.json    (run2)
    ├─ run_summary_2026-05-09.json
    ├─ run_summary_2026-05-11_run2.json
    └─ reflection_analysis_2026-05-11/

After:
    output/SWE-bench_Verified/
    ├─ early_test/<id>/          (2 instances)
    ├─ run1/
    │   ├─ <id>/                 (50 instances)
    │   ├─ sampled_instances.json
    │   └─ run_summary.json
    ├─ run2/
    │   ├─ <id>/                 (120 instances)
    │   ├─ sampled_instances.json
    │   └─ run_summary.json
    └─ reflection_analysis_2026-05-11/   (untouched, cross-batch)

Idempotent: if a target path already exists, the source is skipped with a
SKIP log. Same-filesystem renames are used (Path.rename) — atomic and fast.

Usage:
    python scripts/migrate_to_batches.py --dry-run   # plan only
    python scripts/migrate_to_batches.py             # execute
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("output/SWE-bench_Verified")

# Top-level JSON files to be relocated into their respective batch dirs.
# Source name -> (batch_dir, target_name)
TOP_LEVEL_JSON_MIGRATIONS = {
    "sampled_instances_run1_50_seed42.json": ("run1", "sampled_instances.json"),
    "sampled_instances.json":                ("run2", "sampled_instances.json"),
    "run_summary_2026-05-09.json":           ("run1", "run_summary.json"),
    "run_summary_2026-05-11_run2.json":      ("run2", "run_summary.json"),
}

# Directories that already represent batches or are non-instance artefacts —
# never touch them. (Both "early_test", "run1", "run2" appear here so that
# re-running after partial migration is a no-op.)
BATCH_DIR_NAMES = {"early_test", "run1", "run2"}
CROSS_BATCH_PREFIXES = ("reflection_",)


def load_instance_set(json_path: Path) -> set[str]:
    if not json_path.exists():
        sys.exit(f"ERROR: required manifest missing: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return set(data["instances"])


def classify(name: str, run1: set[str], run2: set[str]) -> str:
    if name in run1:
        return "run1"
    if name in run2:
        return "run2"
    return "early_test"


def safe_rename(src: Path, dst: Path, *, dry_run: bool) -> str:
    """Move src -> dst. Returns 'MOVE' / 'SKIP' / 'ERROR'.

    SKIP if dst already exists (idempotent). Uses Path.rename for same-fs
    atomic move; if cross-fs, falls back to shutil.move.
    """
    if dst.exists():
        return "SKIP"
    if dry_run:
        return "PLAN"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dst)
    except OSError:
        import shutil
        shutil.move(str(src), str(dst))
    return "MOVE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the migration plan without moving anything.")
    args = ap.parse_args()

    if not ROOT.is_dir():
        sys.exit(f"ERROR: {ROOT} does not exist or is not a directory.")

    # Load run1/run2 instance manifests from the CURRENT (pre-migration) locations.
    # After migration these files live inside their respective batch dirs.
    run1_src = ROOT / "sampled_instances_run1_50_seed42.json"
    run2_src = ROOT / "sampled_instances.json"
    run1_set = load_instance_set(run1_src) if run1_src.exists() else set()
    run2_set = load_instance_set(run2_src) if run2_src.exists() else set()

    # If both manifests are already in their batch dirs, accept that too.
    if not run1_set:
        alt = ROOT / "run1" / "sampled_instances.json"
        if alt.exists():
            run1_set = load_instance_set(alt)
    if not run2_set:
        alt = ROOT / "run2" / "sampled_instances.json"
        if alt.exists():
            run2_set = load_instance_set(alt)

    if not run1_set or not run2_set:
        sys.exit(f"ERROR: could not load run1/run2 instance manifests. "
                 f"run1={len(run1_set)} run2={len(run2_set)}")

    print(f"[plan] run1 manifest: {len(run1_set)} instances")
    print(f"[plan] run2 manifest: {len(run2_set)} instances")

    # ---- 1. Plan/execute instance dir moves ----
    counts = {"run1": 0, "run2": 0, "early_test": 0, "skip": 0}
    for child in sorted(ROOT.iterdir()):
        if not child.is_dir():
            continue
        if child.name in BATCH_DIR_NAMES:
            continue
        if child.name.startswith(CROSS_BATCH_PREFIXES):
            continue

        batch = classify(child.name, run1_set, run2_set)
        dst = ROOT / batch / child.name
        action = safe_rename(child, dst, dry_run=args.dry_run)
        if action == "SKIP":
            counts["skip"] += 1
        else:
            counts[batch] += 1
        print(f"  [{action}] {child.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")

    print(f"\n[summary] instance dirs:  "
          f"early_test={counts['early_test']}  "
          f"run1={counts['run1']}  run2={counts['run2']}  "
          f"skip(existing)={counts['skip']}")

    # ---- 2. Plan/execute top-level JSON relocations ----
    json_counts = {"moved": 0, "skip": 0, "missing": 0}
    for src_name, (batch, dst_name) in TOP_LEVEL_JSON_MIGRATIONS.items():
        src = ROOT / src_name
        if not src.exists():
            print(f"  [MISSING] {src_name} (already migrated?)")
            json_counts["missing"] += 1
            continue
        dst = ROOT / batch / dst_name
        action = safe_rename(src, dst, dry_run=args.dry_run)
        if action == "SKIP":
            json_counts["skip"] += 1
        else:
            json_counts["moved"] += 1
        print(f"  [{action}] {src_name} -> {batch}/{dst_name}")

    print(f"\n[summary] top-level JSON:  "
          f"moved={json_counts['moved']}  "
          f"skip(existing)={json_counts['skip']}  "
          f"missing(already-moved)={json_counts['missing']}")

    if args.dry_run:
        print("\nDRY RUN — no changes made. Re-run without --dry-run to execute.")
    else:
        print("\nMigration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
