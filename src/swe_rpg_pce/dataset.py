"""Frozen RPG source loading and a decision-time-only issue projection."""
import hashlib
import json
import re
from pathlib import Path

from src.swe_rpg_pce.models import SWERPGPCECase
from src.swe_verified_pce.models import FrozenImage
from src.swe_verified_pce.dataset import file_sha256
from src.pce_dataset import frozen_source_file as source_file

DATASET = "Xin-Zhou-smu/SWE-RPG-Bench"
EVALUATOR_FILE = "evaluation/evaluate_docker.py"


def runtime_image_ref(image, source_ref):
    """Keep original OCI/task authority separate from an audited local derivative.

    Only the cache lookup changes. Ordinary execution, history isolation and
    official evaluation use the same shared implementations and non-root UID.
    """
    runtime = image.get("runtime_ref", source_ref)
    derivative = image.get("permission_derivative")
    if runtime == source_ref and derivative is None:
        return source_ref
    if (not isinstance(derivative, dict) or derivative.get("policy") != "permission_only_readability_v1"
            or derivative.get("source_ref") != source_ref
            or runtime != f"vibe-local/rpg-readable:{image['instance_id'].lower()}-v1"
            or not re.fullmatch(r"[0-9a-f]{64}", derivative.get("source_sif_sha256", ""))
            or derivative.get("source_sif_sha256") == image.get("sif_sha256")
            or not re.fullmatch(r"[0-9a-f]{64}", derivative.get("build_report_sha256", ""))):
        raise ValueError("RPG derivative lacks its permission-only source/build identity")
    audit = derivative.get("permission_audit", {})
    if (audit.get("passed") is not True or audit.get("source_sif_sha256") != derivative["source_sif_sha256"]
            or not all(re.fullmatch(r"[0-9a-f]{64}", audit.get(k, "")) for k in
                       ("contents_sha256", "original_metadata_sha256", "derived_metadata_sha256"))
            or not audit.get("changes")):
        raise ValueError("RPG derivative permission audit is incomplete")
    from src.environment.sif_permissions import validate_roots, allowed
    roots = audit.get("allowlisted_paths", [])
    validate_roots(roots)
    for change in audit["changes"]:
        before, after = int(change["before"], 8), int(change["after"], 8)
        if (not allowed(change["path"], roots) or before == after or before & ~after
                or (before ^ after) & ~0o555):
            raise ValueError("RPG derivative changes more than allowlisted readability")
    from src.environment.apptainer_env import _image_to_sif_name
    if Path(image["sif_path"]).name != _image_to_sif_name(runtime):
        raise ValueError("RPG derivative cache path differs from runtime identity")
    return runtime


def issue_text(row):
    """Copy issue title/body only; PR/discussion and reference artifacts stay private."""
    issues = row.get("issues")
    if not isinstance(issues, list) or not issues:
        raise ValueError("RPG task requires original issue descriptions")
    parts = []
    for issue in issues:
        title, body = issue.get("issue_title"), issue.get("issue_body")
        if not isinstance(title, str) or not isinstance(body, str) or not (title.strip() or body.strip()):
            raise ValueError("RPG issue lacks a title/body")
        parts.append(title + "\n\n" + body)
    return "\n\n---\n\n".join(parts)


def load_swe_rpg_pce_cases(snapshot, image_manifest_path):
    snapshot, image_manifest_path = Path(snapshot), Path(image_manifest_path)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if (manifest.get("dataset") != DATASET or manifest.get("schema_version") != 1
            or manifest.get("complete") is not True or manifest.get("provisional")
            or not manifest.get("revision")):
        raise ValueError("RPG requires a complete fixed-revision source snapshot")
    rows_path = source_file(snapshot, manifest["instances_file"], manifest["instances_sha256"])
    for name, digest in manifest["evaluator_files"].items():
        source_file(snapshot, name, digest)
    if EVALUATOR_FILE not in manifest["evaluator_files"]:
        raise ValueError("RPG snapshot lacks its official evaluator")
    if file_sha256(image_manifest_path) != manifest["image_manifest_sha256"]:
        raise ValueError("RPG image manifest differs from frozen source binding")
    images = json.loads(image_manifest_path.read_text())
    if images.get("dataset") != DATASET or images.get("revision") != manifest["revision"]:
        raise ValueError("RPG source and image revisions differ")
    rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line.strip()]
    if len(rows) != manifest["instances"] or len({row["instance_id"] for row in rows}) != len(rows):
        raise ValueError("RPG source count/identity mismatch")
    return _cases(rows, manifest, images), manifest, images


def _cases(rows, manifest, images):
    cases = []
    for row in rows:
        if row["pl"] != "python":
            continue
        identifier = row["instance_id"]
        if (not re.fullmatch(r"[0-9a-f]{40}", row["base_commit"])
                or not re.fullmatch(r"[a-z0-9._/-]+@sha256:[0-9a-f]{64}", row["docker_image"])):
            raise ValueError(f"RPG base/image identity is not pinned: {identifier}")
        image = images["records"].get(row["docker_image"])
        if not isinstance(image, dict) or not image.get("accepted") or not image.get("base_commit_verified"):
            raise ValueError(f"RPG image was not accepted: {identifier}")
        if (image.get("instance_id") != identifier or image.get("expected_base_commit") != row["base_commit"]
                or image.get("head_commit") != row["base_commit"]
                or image.get("repository_path") != row["working_dir"]
                or row["working_dir"] not in {"/testbed", "/testbed2"}):
            raise ValueError(f"RPG image/base/workdir binding mismatch: {identifier}")
        if (image.get("oci_digest") != row["docker_image"].split("@", 1)[1]
                or not re.fullmatch(r"[0-9a-f]{64}", image.get("sif_sha256", ""))
                or int(image.get("sif_bytes", 0)) < 1):
            raise ValueError(f"RPG SIF identity is invalid: {identifier}")
        for name in ("rebuild_cmds", "test_cmds", "print_cmds", "FAIL_TO_PASS", "PASS_TO_PASS"):
            if not isinstance(row.get(name), list) or any(not isinstance(s, str) for s in row[name]):
                raise ValueError(f"RPG {name} must be a string array: {identifier}")
        for name in ("patch", "test_patch", "log_parser"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise ValueError(f"RPG evaluator field is empty: {name}")
        cases.append(SWERPGPCECase(instance_id=identifier,
            row_sha256=hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            issue_description=issue_text(row), repo=row["repo"], base_commit=row["base_commit"],
            version="", difficulty="", environment_setup_commit="", test_patch=row["test_patch"],
            fail_to_pass=tuple(row["FAIL_TO_PASS"]), pass_to_pass=tuple(row["PASS_TO_PASS"]),
            gold_patch=row["patch"], source_row=row,
            image=FrozenImage(requested_ref=runtime_image_ref(image, row["docker_image"]), sif_path=image["sif_path"],
                sif_sha256=image["sif_sha256"], sif_bytes=int(image["sif_bytes"]),
                provenance_strength=image["provenance_strength"], oci_digest=image["oci_digest"])))
    if len(cases) != manifest["python_instances"]:
        raise ValueError("RPG Python selection count mismatch")
    return cases


def prepare_snapshot(*, source_root, source_manifest, image_manifest, destination):
    """Mechanical packaging over the already Git-verified acquisition manifest.

    No download or inference. Original row bytes remain unchanged; intermediate
    reference plans are not copied into the execution/learning snapshot.
    """
    source_root, destination = Path(source_root), Path(destination)
    authority = json.loads(Path(source_manifest).read_text())
    images = json.loads(Path(image_manifest).read_text())
    if authority.get("dataset") != DATASET or images.get("dataset") != DATASET or authority["revision"] != images["revision"]:
        raise ValueError("RPG preparation source/image authority differs")
    hashes = {entry["path"]: entry["sha256"] for entry in authority["files"]}
    references = {"instances.jsonl": "data/benchmark.jsonl", EVALUATOR_FILE: EVALUATOR_FILE}
    contents = {target: source_file(source_root, name, hashes[name]).read_bytes()
                for target, name in references.items()}
    rows = [json.loads(line) for line in contents["instances.jsonl"].decode().splitlines() if line.strip()]
    manifest = {"schema_version": 1, "dataset": DATASET, "revision": authority["revision"],
        "complete": True, "provisional": False, "instances_file": "instances.jsonl",
        "instances": len(rows), "python_instances": sum(row["pl"] == "python" for row in rows),
        "instances_sha256": hashlib.sha256(contents["instances.jsonl"]).hexdigest(),
        "source_authority_sha256": file_sha256(Path(source_manifest)),
        "image_manifest_sha256": file_sha256(Path(image_manifest)),
        "evaluator_files": {EVALUATOR_FILE: hashes[EVALUATOR_FILE]},
        "agent_task_projection": "original_issue_title_body_only_v1"}
    _cases(rows, manifest, images)
    contents["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    contents["selection.json"] = (json.dumps({"schema_version": 1,
        "purpose": "all_frozen_python_tasks_without_outcome_screening",
        "selected_instance_ids": sorted(row["instance_id"] for row in rows if row["pl"] == "python"),
        "source_manifest_sha256": hashlib.sha256(contents["manifest.json"]).hexdigest()},
        sort_keys=True, indent=2) + "\n").encode()
    # Reuse the immutable PCE preparation writer, not another promotion loop.
    from src.optimization.safe_pce_preparation import _write_once
    for name, content in contents.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_once(path, content)
    load_swe_rpg_pce_cases(destination, Path(image_manifest))
    return manifest


def prepare_subset(*, snapshot, image_manifest, instance_ids, destination, runtime_image_manifest=None):
    """Freeze exact selected source lines; keep downstream rows out of GEPA.

    This is declared dataset selection, not Plan/patch/Agent-output repair.
    The original whole-source authority is retained through provenance hashes.
    """
    snapshot, image_manifest, destination = map(Path, (snapshot, image_manifest, destination))
    if destination.resolve().is_relative_to(snapshot.resolve()):
        raise ValueError("subset destination must be outside the frozen source")
    cases, original, images = load_swe_rpg_pce_cases(snapshot, image_manifest)
    if runtime_image_manifest is not None:
        replacement = json.loads(Path(runtime_image_manifest).read_text())
        if (replacement.get('dataset') != images['dataset'] or replacement.get('revision') != images['revision']
                or set(replacement.get('records', {})) != set(images['records'])):
            raise ValueError('runtime image authority changes the frozen dataset or image membership')
        for ref, old in images['records'].items():
            newer = replacement['records'][ref]
            if newer == old:
                continue
            runtime_image_ref(newer, ref)
            if newer.get('permission_derivative', {}).get('source_sif_sha256') != old['sif_sha256']:
                raise ValueError('runtime image derivative has a different frozen parent SIF')
            comparable = {k: v for k, v in newer.items() if k not in {'runtime_ref', 'permission_derivative'}}
            for key in ('sif_path', 'sif_sha256', 'sif_bytes'):
                comparable[key] = old[key]
            if comparable != old:
                raise ValueError('runtime image derivative changes original base/OCI authority')
        images = replacement
    selected = set(instance_ids)
    if not selected or len(selected) != len(instance_ids) or not selected <= {c.instance_id for c in cases}:
        raise ValueError("subset IDs must be nonempty, unique, frozen Python cases")
    raw = (snapshot / original["instances_file"]).read_bytes()
    lines = [line for line in raw.splitlines(keepends=True)
             if line.strip() and json.loads(line)["instance_id"] in selected]
    subset_rows = b"".join(lines)
    subset_images = {"schema_version": 1, "dataset": images["dataset"], "revision": images["revision"],
        "purpose": "frozen_task_subset", "source_image_manifest_sha256": file_sha256(image_manifest),
        "records": {ref: record for ref, record in images["records"].items()
                    if record["instance_id"] in selected}}
    if runtime_image_manifest is not None:
        subset_images['runtime_image_manifest_sha256'] = file_sha256(Path(runtime_image_manifest))
    image_bytes = (json.dumps(subset_images, sort_keys=True, indent=2) + "\n").encode()
    manifest = dict(original, instances_file="instances.jsonl", instances=len(selected), python_instances=len(selected),
        instances_sha256=hashlib.sha256(subset_rows).hexdigest(),
        image_manifest_sha256=hashlib.sha256(image_bytes).hexdigest(),
        subset_provenance={"source_manifest_sha256": file_sha256(snapshot / "manifest.json"),
            "source_instances_sha256": original["instances_sha256"],
            "source_image_manifest_sha256": file_sha256(image_manifest),
            "selection": sorted(selected), "source_row_bytes_preserved": True})
    contents = {"instances.jsonl": subset_rows, "images.json": image_bytes,
        "manifest.json": (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(),
        "selection.json": (json.dumps({"schema_version": 1, "selected_instance_ids": sorted(selected)},
                                      sort_keys=True, indent=2) + "\n").encode()}
    for name, digest in original["evaluator_files"].items():
        contents[name] = source_file(snapshot, name, digest).read_bytes()
    from src.optimization.safe_pce_preparation import _write_once
    for name, content in contents.items():
        _write_once(destination / name, content)
    load_swe_rpg_pce_cases(destination, destination / "images.json")
    return manifest
