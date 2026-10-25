"""Freeze outcome-blind repository holdouts and separate RPG execution sources.

Reuse the historical duplicate grouping, shared seeded partition and immutable
RPG loader/writer. No model, container, history builder or scheduler is invoked.
"""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.tools.freeze_swe_chat_behavioral_formal_split import (
    _Groups, _normalize, _shingles, _jaccard, _content_sha256,
    NEAR_DUPLICATE_THRESHOLD,
)
from src.data.grouped_partition import partition
from src.swe_rpg_pce.dataset import load_swe_rpg_pce_cases, prepare_subset
from src.swe_verified_pce.dataset import file_sha256
from src.optimization.safe_pce_preparation import _write_once


def assignments_for(cases):
    """Only decision-time issue text, repository and base identity choose groups."""
    cases = sorted(cases, key=lambda case: case.instance_id)
    groups = _Groups({case.repo for case in cases})
    signals = {case.instance_id: (_normalize(case.issue_description), _shingles(case.issue_description))
               for case in cases}
    duplicate_links = []
    for index, left in enumerate(cases):
        for right in cases[index + 1:]:
            if left.repo == right.repo:
                continue
            ltext, lshingles = signals[left.instance_id]
            rtext, rshingles = signals[right.instance_id]
            reason = ("same_base_commit" if left.base_commit == right.base_commit else
                      "exact_issue" if ltext == rtext else
                      "near_duplicate_issue" if _jaccard(lshingles, rshingles) >= NEAR_DUPLICATE_THRESHOLD else None)
            if reason:
                groups.union(left.repo, right.repo)
                duplicate_links.append({"left": left.instance_id, "right": right.instance_id, "reason": reason})
    members = {}
    for repo in sorted(groups.parent):
        members.setdefault(groups.find(repo), []).append(repo)
    assignments = []
    for case in sorted(cases, key=lambda c: c.instance_id):
        component = hashlib.sha256("\n".join(members[groups.find(case.repo)]).encode()).hexdigest()[:16]
        assignments.append({"case_id": case.instance_id, "repo": case.repo,
            "dedup_group": "repository-component:" + component,
            "row_sha256": case.row_sha256,
            "normalized_issue_sha256": hashlib.sha256(signals[case.instance_id][0].encode()).hexdigest()})
    return assignments, duplicate_links


def freeze(*, snapshot, image_manifest, exposure_selection, output_dir, manifest_dir,
           seed=42, train_fraction=0.6, test_fraction=0.2):
    output_dir, manifest_dir = Path(output_dir), Path(manifest_dir)
    if output_dir.exists() or manifest_dir.exists():
        raise FileExistsError("Refuse to overwrite a frozen split or source")
    if not 0 < train_fraction < 1 or not 0 < test_fraction < 1 - train_fraction:
        raise ValueError("train/validation/test fractions must all be positive")
    cases, source, _ = load_swe_rpg_pce_cases(snapshot, image_manifest)
    exposed = json.loads(Path(exposure_selection).read_text())["selected_instance_ids"]
    assignments, links = assignments_for(cases)
    targets = {"train": round(len(cases) * train_fraction), "test": round(len(cases) * test_fraction)}
    assignments = partition(assignments, exposed, seed=seed,
                            train_target=targets["train"], test_target=targets["test"])
    selected = {split: [a["case_id"] for a in assignments if a["split"] == split]
                for split in ("train", "validation", "test")}
    if any(not ids for ids in selected.values()):
        raise ValueError("Every split must contain at least one case")
    gepa_ids = sorted(selected["train"] + selected["validation"])
    prepare_subset(snapshot=snapshot, image_manifest=image_manifest,
                   instance_ids=gepa_ids, destination=output_dir / "gepa-source")
    prepare_subset(snapshot=snapshot, image_manifest=image_manifest,
                   instance_ids=selected["test"], destination=output_dir / "downstream-test-source")
    counts = {split: {"cases": len(ids),
                     "repositories": sorted({a["repo"] for a in assignments if a["split"] == split})}
              for split, ids in selected.items()}
    manifest = {"schema_version": 1, "complete": True, "provisional": False,
        "split_id": manifest_dir.name, "dataset": source["dataset"], "revision": source["revision"],
        "source_manifest_sha256": file_sha256(Path(snapshot) / "manifest.json"),
        "source_instances_sha256": source["instances_sha256"],
        "image_manifest_sha256": file_sha256(Path(image_manifest)), "seed": seed,
        "target_ratios": [train_fraction, 1-train_fraction-test_fraction, test_fraction],
        "actual_ratios": {s: len(ids)/len(cases) for s, ids in selected.items()},
        "assignments": assignments, "counts": counts, "cross_repository_duplicate_links": links,
        "development_exposed_case_ids": sorted(exposed),
        "exposure_selection_sha256": file_sha256(Path(exposure_selection)),
        "policy": {"repository_and_duplicate_components_atomic": True,
            "development_components_train_only": True, "assignment_outcome_independent": True,
            "all_frozen_python_cases_retained": True, "downstream_rows_and_images_not_in_gepa_package": True,
            "intermediate_reference_files_excluded": True, "no_claim_of_absence_from_model_pretraining": True},
        "source_subset_file_sha256": {str(p.relative_to(output_dir)): file_sha256(p)
            for p in sorted(output_dir.rglob("*")) if p.is_file()}}
    manifest["content_sha256"] = _content_sha256(manifest)
    files = {"manifest.json": manifest,
             "split-selection.json": {s: selected[s] for s in ("train", "validation")},
             "gepa-selection.json": {"schema_version": 1, "selected_instance_ids": gepa_ids},
             "downstream-test-selection.json": {"schema_version": 1, "selected_instance_ids": selected["test"]}}
    for name, content in files.items():
        _write_once(manifest_dir / name, (json.dumps(content, sort_keys=True, indent=2) + "\n").encode())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "image-manifest", "exposure-selection", "output-dir", "manifest-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    print(json.dumps(freeze(**vars(parser.parse_args()))["counts"], indent=2))


if __name__ == "__main__":
    main()
