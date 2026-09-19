#!/usr/bin/env python3
"""Export a compact, hash-bound index from Safe-PCE outcome authorities.

The raw outcome rows remain outside Git.  This tool retains only the Plan,
outcome, stable provenance, and references needed to materialize retrospective
Reflection evidence later.
"""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _jsonl(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"outcome row is not an object: {path}")
                yield value


def _output_path(run_root, row):
    relative = row.get("checkpoint_relative_path")
    if isinstance(relative, str) and "/checkpoints/" in relative:
        prefix, suffix = relative.split("/checkpoints/", 1)
        task_name = suffix.rsplit("/", 1)[-1]
        candidate = run_root / prefix / "outputs" / f"{task_name}.json"
        if candidate.is_file():
            return candidate
    matches = sorted(
        run_root.glob(f"hpc_tasks/pce/*/outputs/task_{int(row['task_index']):04d}.json")
    )
    matching = []
    for candidate in matches:
        value = json.loads(candidate.read_text(encoding="utf-8"))
        if value.get("instance_id") == row.get("instance_id") and value.get(
            "plan_sha256"
        ) == row.get("plan_sha256"):
            matching.append(candidate)
    if len(matching) != 1:
        raise ValueError(
            "could not resolve one independent output artifact for "
            f"{row.get('instance_id')} task={row.get('task_index')}"
        )
    return matching[0]


def _source_access_events(run_root, source_access):
    if not isinstance(source_access, dict):
        return []
    events = []
    for attempt in source_access.get("attempts") or []:
        if not isinstance(attempt, dict) or not attempt.get("log_relative_path"):
            continue
        path = run_root / str(attempt["log_relative_path"])
        if not path.is_file():
            raise ValueError("declared source-access log is missing: {}".format(path))
        expected = attempt.get("log_sha256")
        if expected and _sha256(path) != expected:
            raise ValueError("source-access log hash mismatch: {}".format(path))
        for row in _jsonl(path):
            urls = []
            for raw_url in row.get("urls") or []:
                parsed = urlsplit(str(raw_url))
                # Query strings can carry credentials and are not needed for
                # provenance auditing. Preserve only scheme/host/path.
                urls.append(
                    urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                )
            events.append(
                {
                    "phase": row.get("phase"),
                    "client": row.get("client"),
                    "decision": row.get("decision"),
                    "reason": row.get("reason"),
                    "executed": row.get("executed"),
                    "execution_status": row.get("execution_status"),
                    "returncode": row.get("returncode"),
                    "prompt_url_match": row.get("prompt_url_match"),
                    "urls": urls,
                    "command_sha256": row.get("command_sha256"),
                }
            )
    return events


def _completed_observation(
    *,
    source_id,
    run_root,
    row,
    artifact=None,
):
    if row.get("status") != "completed":
        return None
    evaluator = row.get("evaluator_result")
    resolved = (
        evaluator.get("evaluator_resolved") if isinstance(evaluator, dict) else None
    )
    if not isinstance(resolved, bool):
        # Historical controllers sometimes used status=completed for a fully
        # executed pipeline whose evaluator nevertheless ended unknown or
        # timed out. Such rows are operationally incomplete, never U.
        return None
    plan = row.get("plan")
    plan_sha256 = row.get("plan_sha256")
    if not isinstance(plan, str) or not plan.strip():
        raise ValueError(f"{source_id}:{row.get('task_index')} has no Plan")
    actual_plan_sha = hashlib.sha256(plan.encode("utf-8")).hexdigest()
    if plan_sha256 != actual_plan_sha:
        raise ValueError(f"{source_id}:{row.get('task_index')} Plan hash mismatch")
    artifact = artifact or _output_path(run_root, row)
    artifact_sha = _sha256(artifact)
    instance_id = str(row["instance_id"])
    task_index = int(row["task_index"])
    pce_run_index = row.get("pce_run_index")
    identity = {
        "source_id": source_id,
        "instance_id": instance_id,
        "task_index": task_index,
        "pce_run_index": pce_run_index,
        "artifact_sha256": artifact_sha,
    }
    source_access = row.get("source_access")
    source_summary = None
    if isinstance(source_access, dict):
        source_summary = source_access.get("all_attempts_summary") or source_access.get(
            "summary"
        )
    workspace = row.get("code_workspace_evidence")
    implementation = (
        workspace.get("implementation_submission")
        if isinstance(workspace, dict)
        else None
    )
    return {
        "schema_version": 1,
        "observation_id": _stable_id(identity),
        "source_id": source_id,
        "instance_id": instance_id,
        "task_index": task_index,
        "pce_run_index": pce_run_index,
        "outcome": "resolved" if resolved else "unresolved",
        "plan": plan,
        "plan_sha256": plan_sha256,
        "patch_sha256": row.get("patch_sha256"),
        "terminal_reason": row.get("terminal_reason"),
        "reliability_audit": {
            "plan_boundary": row.get("plan_boundary"),
            "source_access_summary": source_summary,
            "source_access_events": _source_access_events(run_root, source_access),
            "implementation_submission": implementation,
            "final_validation_label": row.get("final_validation_label"),
        },
        "artifact_path": str(artifact),
        "artifact_sha256": artifact_sha,
        "historical_evidence": {
            "plan_trajectory": {
                "artifact_path": str(artifact),
                "artifact_sha256": artifact_sha,
                "json_field": "plan_trajectory",
            },
            "code_trajectory": {
                "artifact_path": str(artifact),
                "artifact_sha256": artifact_sha,
                "json_field": "code_trajectory",
            },
            "generated_patch": {
                "artifact_path": str(artifact),
                "artifact_sha256": artifact_sha,
                "json_field": "patch",
            },
            "evaluator_result": {
                "artifact_path": str(artifact),
                "artifact_sha256": artifact_sha,
                "json_field": "evaluator_result",
            },
        },
    }


def export(
    *,
    sources,
    runs,
    output_path,
):
    observations = []
    source_summary = []
    for source_id, raw_path in sources:
        if not raw_path.is_file():
            raise FileNotFoundError(raw_path)
        run_root = raw_path.parent
        rows = list(_jsonl(raw_path))
        completed = []
        for row in rows:
            value = _completed_observation(
                source_id=source_id, run_root=run_root, row=row
            )
            if value is not None:
                completed.append(value)
        observations.extend(completed)
        source_summary.append(
            {
                "source_id": source_id,
                "raw_path": str(raw_path),
                "raw_sha256": _sha256(raw_path),
                "rows": len(rows),
                "completed_observations": len(completed),
            }
        )
    for source_id, run_root in runs:
        if not run_root.is_dir():
            raise FileNotFoundError(run_root)
        artifacts = sorted(run_root.glob("hpc_tasks/pce/*/outputs/task_*.json"))
        completed = []
        for artifact in artifacts:
            row = json.loads(artifact.read_text(encoding="utf-8"))
            if not isinstance(row, dict):
                raise ValueError(f"PCE output is not an object: {artifact}")
            value = _completed_observation(
                source_id=source_id,
                run_root=run_root,
                row=row,
                artifact=artifact,
            )
            if value is not None:
                completed.append(value)
        observations.extend(completed)
        source_summary.append(
            {
                "source_id": source_id,
                "run_root": str(run_root),
                "output_artifacts": len(artifacts),
                "completed_observations": len(completed),
            }
        )
    ids = [item["observation_id"] for item in observations]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate observation identity across sources")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in observations:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(output_path)
    manifest = {
        "schema_version": 1,
        "observation_count": len(observations),
        "instance_count": len({item["instance_id"] for item in observations}),
        "output_sha256": _sha256(output_path),
        "sources": source_summary,
    }
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="SOURCE_ID=/absolute/path/to/raw_pce_outcomes.jsonl",
    )
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        help="SOURCE_ID=/absolute/path/to/run root containing task outputs",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sources = []
    for raw in args.source:
        source_id, separator, path = raw.partition("=")
        if not separator or not source_id or not path:
            raise ValueError("--source must be SOURCE_ID=PATH")
        sources.append((source_id, Path(path)))
    runs = []
    for raw in args.run:
        source_id, separator, path = raw.partition("=")
        if not separator or not source_id or not path:
            raise ValueError("--run must be SOURCE_ID=PATH")
        runs.append((source_id, Path(path)))
    if not sources and not runs:
        raise ValueError("at least one --source or --run is required")
    print(
        json.dumps(
            export(sources=sources, runs=runs, output_path=args.output),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
