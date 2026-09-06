"""Load fixed-revision Pro rows and audited SIF identities."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from src.swe_bench_pro_pce.models import SWEBenchProPCECase
from src.swe_verified_pce.dataset import file_sha256
from src.swe_verified_pce.models import FrozenImage


DATASET = "ScaleAI/SWE-bench_Pro"


def _list(value: Any, *, field: str, instance_id: str) -> tuple[str, ...]:
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"{instance_id}: {field} is not a parseable list") from exc
    else:
        parsed = None
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{instance_id}: {field} is not a string list")
    return tuple(parsed)


def canonical_image_ref(row: dict[str, Any]) -> str:
    tag = str(row.get("dockerhub_tag", "")).strip()
    if not tag:
        raise ValueError("Pro row lacks dockerhub_tag")
    return f"jefzda/sweap-images:{tag}"


def load_swe_bench_pro_pce_cases(
    snapshot: Path,
    image_manifest_path: Path,
) -> tuple[list[SWEBenchProPCECase], dict[str, Any], dict[str, Any]]:
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("Pro PCE requires a complete source snapshot")
    if manifest.get("dataset") != DATASET or not manifest.get("revision"):
        raise ValueError("unexpected or unfrozen Pro source dataset")
    rows_path = snapshot / str(manifest.get("instances_file", "instances.jsonl"))
    if file_sha256(rows_path) != manifest.get("instances_sha256"):
        raise ValueError("Pro source rows differ from their frozen hash")
    wrappers = [json.loads(line) for line in rows_path.read_text().splitlines() if line]
    if len(wrappers) != int(manifest.get("instances", -1)):
        raise ValueError("Pro source instance count mismatch")

    images = json.loads(image_manifest_path.read_text(encoding="utf-8"))
    if images.get("source_manifest_sha256") != file_sha256(manifest_path):
        raise ValueError("Pro image manifest belongs to another source snapshot")
    if images.get("selection_manifest_sha256") != manifest.get(
        "selection_manifest_sha256"
    ):
        raise ValueError("Pro image manifest belongs to another selection")
    if images.get("agent_history_policy") != "future_history_inaccessible_v1":
        raise ValueError(
            "Pro Plan/Code future-history containment has not been verified"
        )
    implementation = Path(__file__).with_name("repository.py")
    if images.get("agent_history_implementation_sha256") != file_sha256(
        implementation
    ):
        raise ValueError("Pro history containment implementation identity differs")
    records = images.get("records")
    if not isinstance(records, dict):
        raise ValueError("Pro image manifest has no records mapping")

    cases = []
    for wrapper in wrappers:
        row = wrapper.get("source_row")
        if not isinstance(row, dict):
            raise ValueError("Pro instance lacks source_row")
        instance_id = str(row["instance_id"])
        row_hash = hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if wrapper.get("row_sha256") != row_hash:
            raise ValueError(f"{instance_id}: source row hash mismatch")
        image_ref = canonical_image_ref(row)
        image = records.get(image_ref)
        if not isinstance(image, dict):
            raise ValueError(f"{instance_id}: audited image record is missing")
        if image.get("instance_id") != instance_id or image.get("status") != "audited":
            raise ValueError(f"{instance_id}: image identity/status differs")
        if image.get("base_commit_verified") is not True:
            raise ValueError(f"{instance_id}: image lacks a verified base commit")
        if not image.get("sif_sha256") or not image.get("sif_path"):
            raise ValueError(f"{instance_id}: image lacks frozen SIF identity")
        assets = wrapper.get("evaluator_assets")
        if not isinstance(assets, dict):
            raise ValueError(f"{instance_id}: evaluator assets are missing")
        for name in ("run_script", "parser", "base_dockerfile", "instance_dockerfile"):
            relative = assets.get(name)
            expected_hash = assets.get(f"{name}_sha256")
            path = snapshot / str(relative)
            if not path.is_file() or file_sha256(path) != expected_hash:
                raise ValueError(f"{instance_id}: evaluator asset differs: {name}")
        cases.append(
            SWEBenchProPCECase(
                instance_id=instance_id,
                row_sha256=row_hash,
                issue_description=str(wrapper["issue_description"]),
                repo=str(row["repo"]),
                base_commit=str(row["base_commit"]),
                dockerhub_tag=str(row["dockerhub_tag"]),
                fail_to_pass=_list(row["fail_to_pass"], field="fail_to_pass", instance_id=instance_id),
                pass_to_pass=_list(row["pass_to_pass"], field="pass_to_pass", instance_id=instance_id),
                before_repo_set_cmd=str(row["before_repo_set_cmd"]),
                selected_test_files_to_run=_list(
                    row["selected_test_files_to_run"],
                    field="selected_test_files_to_run",
                    instance_id=instance_id,
                ),
                gold_patch=str(row.get("patch", "")),
                test_patch=str(row.get("test_patch", "")),
                image=FrozenImage(
                    requested_ref=image_ref,
                    sif_path=str(image["sif_path"]),
                    sif_sha256=str(image["sif_sha256"]),
                    sif_bytes=int(image["sif_bytes"]),
                    provenance_strength=str(image.get("provenance_strength", "retrospective")),
                    oci_digest=str(image["oci_digest"]) if image.get("oci_digest") else None,
                ),
                evaluator_assets={str(k): str(v) for k, v in assets.items()},
                source_row=dict(row),
            )
        )
    if len({case.instance_id for case in cases}) != len(cases):
        raise ValueError("Pro source instance IDs are not unique")
    return cases, manifest, images
