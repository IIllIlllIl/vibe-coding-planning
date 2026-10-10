"""Repartition an existing verified snapshot without altering scientific content.

Reuse the historical repository/duplicate components. Existing exposed components
remain training-only; split the remaining components by label-blind seeded hashes.
Test is a separate snapshot, never staged as part of the GEPA dataset directory.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from scripts.tools.freeze_swe_chat_behavioral_formal_split import _content_sha256
from src.optimization.hpc.task_batch import atomic_json


def partition(assignments, exposed_ids, *, seed=42, test_target=24):
    by_id = {a["case_id"]: a for a in assignments}
    if len(by_id) != len(assignments) or not set(exposed_ids) <= set(by_id):
        raise ValueError("Invalid assignment IDs or exposure IDs")
    groups = {}
    for assignment in assignments:
        group = assignment.get("dedup_group")
        if not group:
            raise ValueError("Missing frozen repository component")
        groups.setdefault(group, []).append(assignment["case_id"])
    protected = {by_id[i]["dedup_group"] for i in exposed_ids}
    remaining = sorted(set(groups) - protected,
                       key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    # Subset-sum over component sizes, not labels or Plan outcomes. First seeded
    # solution wins ties. This keeps whole components and avoids large-group bias.
    choices = {0: ()}
    for group in remaining:
        for size, subset in list(choices.items()):
            choices.setdefault(size + len(groups[group]), (*subset, group))
    total_remaining = sum(len(groups[g]) for g in remaining)
    candidates = [n for n in choices if 0 < n < total_remaining]
    if not candidates:
        raise ValueError("Insufficient components for nonempty validation and test")
    selected_size = min(candidates, key=lambda n: (abs(n - test_target), n))
    test_groups = set(choices[selected_size])
    return [dict(a, split=("train" if a["dedup_group"] in protected else
                          "test" if a["dedup_group"] in test_groups else "validation"))
            for a in sorted(assignments, key=lambda a: a["case_id"])]


def freeze(*, source_dir, source_authority, source_split, output_dir,
           manifest_path, seed=42, test_target=24):
    if output_dir.exists() or manifest_path.exists():
        raise FileExistsError("Refuse to overwrite a frozen split or snapshot")
    authority = json.loads(source_authority.read_text())
    split = json.loads(source_split.read_text())
    if split["content_sha256"] != _content_sha256(split):
        raise ValueError("Source split content hash mismatch")
    if authority["source_split_content_sha256"] != split["content_sha256"]:
        raise ValueError("Snapshot and split provenance mismatch")
    for name, digest in authority["file_sha256"].items():
        if hashlib.sha256((source_dir / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Source snapshot hash mismatch: {name}")
    rows = [json.loads(line) for name in ("train.jsonl", "validation.jsonl")
            for line in (source_dir / name).read_text().splitlines() if line.strip()]
    by_id = {row["instance_id"]: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != {a["case_id"] for a in split["assignments"]}:
        raise ValueError("Source snapshot does not cover frozen universe exactly")
    assignments = partition(split["assignments"], split["development_exposed_case_ids"],
                            seed=seed, test_target=test_target)
    grouped = {s: [] for s in ("train", "validation", "test")}
    repositories = {s: set() for s in grouped}
    for assignment in assignments:
        row = by_id[assignment["case_id"]]
        if row["audit_provenance"]["dedup_group"] != assignment["dedup_group"]:
            raise ValueError("Snapshot component differs from frozen authority")
        # Only membership metadata changes; Plan, context, supervision and
        # reflection evidence remain byte-equivalent after JSON parsing.
        updated = dict(row, split=assignment["split"])
        grouped[assignment["split"]].append(updated)
        repositories[assignment["split"]].add(row["checker_input"]["repository_proxy"]["repo"])
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        if repositories[left] & repositories[right]:
            raise ValueError("Cross-split repository overlap")
    output_dir.mkdir(parents=True)
    gepa = output_dir / "gepa-snapshot"
    test = output_dir / "pc-only-test"
    gepa.mkdir()
    test.mkdir()
    for name, destination in (("train", gepa), ("validation", gepa), ("test", test)):
        (destination / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    for row in grouped[name]))
    common = {"schema_version": 1, "complete": True, "provisional": False,
              "task_semantics": rows[0]["task_semantics"],
              "source_snapshot_sha256": authority["file_sha256"],
              "source_split_content_sha256": split["content_sha256"],
              "historically_untouched": False}
    atomic_json(gepa / "manifest.json", dict(common, train_instances=len(grouped["train"]),
        validation_instances=len(grouped["validation"]), test_included=False))
    atomic_json(test / "manifest.json", dict(common, test_instances=len(grouped["test"]),
        purpose="downstream_pc_only", excluded_from_current_gepa=True))
    manifest = dict(common, split_id="swe-chat-hybrid-threeway-v1-20261010", seed=seed,
        target_ratios=[0.64, 0.18, 0.18], assignments=assignments,
        policy={"repository_and_duplicate_components_atomic": True,
                "development_components_train_only": True,
                "assignment_label_independent": True,
                "test_never_enters_current_gepa": True},
        development_exposed_case_ids=split["development_exposed_case_ids"],
        historical_exposure={"previous_gepa_train_cases": 84,
                             "previous_gepa_validation_cases": 47,
                             "previous_run": "repository-holdout-media-projected-accuracy-b8-8it-v2-20260830",
                             "claim": "held_out_from_this_run_not_historically_untouched"},
        counts={s: dict(Counter(row["supervision"]["decision"] for row in grouped[s]),
                        cases=len(grouped[s]), repositories=len(repositories[s])) for s in grouped},
        snapshot_file_sha256={str(p.relative_to(output_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for folder in (gepa, test) for p in sorted(folder.iterdir())})
    manifest["content_sha256"] = _content_sha256(manifest)
    atomic_json(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "source-authority", "source-split", "output-dir", "manifest-path"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-target", type=int, default=24)
    print(json.dumps(freeze(**vars(parser.parse_args()))["counts"], indent=2))


if __name__ == "__main__":
    main()
