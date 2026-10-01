"""Run the frozen A-E Checker-only diagnostic on two historical R/U pairs.

No Planner, Coder, Evaluator, Reflector, or Curator is invoked. Each style,
Plan, and repetition is an independent Repo Checker task.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import yaml

from src.exceptions import ControllerYield
from src.optimization.hpc.task_batch import TaskAttemptsExhausted, atomic_json
from src.optimization.paired_playbook import validate_paired_checker_result
from src.optimization.playbook import RejectPlaybook
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.repo_playbook import render_concern_playbook
from src.polybench_pcce.paired_gate import load_gate_units, load_paired_gate_config
from src.polybench_pce.dataset import file_sha256


def _resolve(root: Path, name: str) -> Path:
    path = Path(name)
    return path if path.is_absolute() else root / path


def prepare(config_path: Path):
    root = config_path.resolve().parents[1]
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config.get("mode") != "checker_rule_style_probe" or config.get("repetitions") != 3:
        raise ValueError("probe requires three independent repetitions")
    paths = config["paths"]
    poly_config = load_paired_gate_config(_resolve(root, paths["polybench_gate_config"]))
    poly_units, _ = load_gate_units(poly_config)
    if len(poly_units) != 2 or {x["evaluator_result"]["evaluator_resolved"] for _, x in poly_units} != {True, False}:
        raise ValueError("PolyBench selection must be exactly one R/U pair")
    pair_id = config["verified_pair_id"]
    pair_path = _resolve(root, paths["verified_pairs"])
    matches = [json.loads(line) for line in pair_path.read_text(encoding="utf-8").splitlines()
               if line.strip() and json.loads(line).get("pair_id") == pair_id]
    if len(matches) != 1:
        raise ValueError("Verified pair identity is not unique")
    pair = matches[0]
    images_path = _resolve(root, paths["verified_images"])
    images = json.loads(images_path.read_text(encoding="utf-8"))["records"]
    found = [(name, data) for name, data in images.items()
             if data["instance_id"] == pair["task_id"]]
    if len(found) != 1:
        raise ValueError("Verified image identity is not unique")
    image_name, image = found[0]
    repo = pair["repository"]
    if not image["base_commit_verified"] or image["expected_base_commit"] != repo["base_commit"]:
        raise ValueError("Verified image base commit differs from pair")
    runtime_path = _resolve(root, paths["checker_runtime_config"])
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    poly_prompt = yaml.safe_load(_resolve(root, paths["polybench_checker_prompt"]).read_text(encoding="utf-8"))
    gepa_prompt = yaml.safe_load(_resolve(root, runtime["inputs"]["prompt_bundle"]).read_text(encoding="utf-8"))
    for key in ("checker_system", "checker_instance"):
        if poly_prompt[key] != gepa_prompt[key]:
            raise ValueError(f"Checker prompt differs across benchmarks: {key}")
    if runtime["models"]["checker"] != {
        "executor": "mini_swe", "model": "deepseek-flash",
        "temperature": 0.0, "thinking": "disabled",
    }:
        raise ValueError("Checker model differs from PolyBench probe")

    cases = []
    for case, outcome in poly_units:
        cases.append({
            "case": "polybench-sam", "side": "R" if outcome["evaluator_result"]["evaluator_resolved"] else "U",
            "issue": case.issue_description, "plan": outcome["plan"],
            "repository": {"repo": case.repo, "base_commit": case.base_commit,
                           "instance_id": case.instance_id, "image_name": case.image.requested_ref},
            "image_authority": {"requested_ref": case.image.requested_ref,
                                "sif_path": case.image.sif_path, "sif_sha256": case.image.sif_sha256,
                                "sif_bytes": case.image.sif_bytes},
        })
    for side, observation in (("R", pair["resolved_observation"]), ("U", pair["unresolved_observation"])):
        cases.append({
            "case": "verified-cds", "side": side,
            "issue": pair["issue_description"], "plan": observation["plan"],
            "repository": {"repo": repo["repo"], "base_commit": repo["base_commit"],
                           "instance_id": repo["instance_id"], "image_name": image_name},
            "image_authority": {"requested_ref": image_name, "sif_path": image["sif_path"],
                                "sif_sha256": image["sif_sha256"], "sif_bytes": image["sif_bytes"]},
        })
    if {(c["case"], c["side"]) for c in cases} != {
        (case, side) for case in ("polybench-sam", "verified-cds") for side in ("R", "U")
    }:
        raise ValueError("probe requires exactly four distinct Plans")
    styles = config["styles"]
    if set(styles) != set("ABCDE"):
        raise ValueError("probe requires styles A-E")
    items = []
    books = {}
    for style in "ABCDE":
        path = _resolve(root, styles[style])
        book = RejectPlaybook.parse(path.read_text(encoding="utf-8"))
        books[style] = book
        visible = render_concern_playbook(book)
        for case in cases:
            for rep in range(1, 4):
                items.append({
                    "instance_id": f"probe::{style}::{case['case']}::{case['side']}::rep-{rep:02d}",
                    "validation_rule_count": len(book.bullets), "output_contract": "binary_v2",
                    "repository": case["repository"], "image_authority": case["image_authority"],
                    "prompt_values": {"issue": case["issue"], "plan": case["plan"],
                                      "checker_visible_playbook": visible, "retry_feedback": ""},
                })
    hashes = {"probe_config_sha256": file_sha256(config_path),
              "polybench_gate_config_sha256": file_sha256(poly_config.config_path),
              "verified_pairs_sha256": file_sha256(pair_path),
              "verified_images_sha256": file_sha256(images_path),
              "checker_runtime_config_sha256": file_sha256(runtime_path),
              **{f"style_{style}_sha256": file_sha256(_resolve(root, path)) for style, path in styles.items()}}
    return config, poly_config, runtime_path, cases, books, items, hashes


def run(config_path: Path) -> dict | None:
    config, poly_config, runtime_path, cases, books, items, hashes = prepare(config_path)
    root = config_path.resolve().parents[1]
    run_dir = _resolve(root, config["paths"]["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "mode": "checker_rule_style_probe",
                "source_hashes": hashes, "repetitions": 3,
                "unit_ids": [item["instance_id"] for item in items],
                "checker_input_boundary": "task, Plan, frozen base repository, style text only; no outcome"}
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("probe run manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)
    hpc = replace(poly_config.hpc, job_name_prefix=config["job_name_prefix"])
    executor = PlaybookHPCExecutor(config_path=runtime_path, run_dir=run_dir, hpc=hpc,
                                   checker_observation_contract="executed_tools_v1")
    try:
        outputs = executor.run_wave("paired_repo_checker", items)
    except ControllerYield as exc:
        atomic_json(run_dir / "controller_status.json", {"status": "yielded", "reason": exc.reason,
                     "batch_dir": exc.batch_dir, "worker_job_id": exc.job_id})
        return None
    except TaskAttemptsExhausted:
        batch_dir = executor.batch_dir_for("paired_repo_checker", items)
        outputs = []
        for index in range(len(items)):
            path = batch_dir / "outputs" / f"task_{index:04d}.json"
            outputs.append(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})
    rows = []
    for item, output in zip(items, outputs, strict=True):
        _, style, case, side, repetition = item["instance_id"].split("::")
        raw = output.get("agent_output") if output.get("status") == "completed" else None
        result = (validate_paired_checker_result(raw, books[style], levels=False, require_reason=False)
                  if isinstance(raw, dict) else None)
        rows.append({"instance_id": item["instance_id"], "style": style, "case": case,
                     "side": side, "repetition": repetition, "checker_rejected": result.rejected if result else None,
                     "triggered_rule_numbers": [r.rule_number for r in result.rule_results if r.triggered] if result else [],
                     "status": "completed" if result else "operational_incomplete",
                     "checker_output": output if result else None})
    outcome_path = run_dir / "checker_probe_outcomes.jsonl"
    temporary = outcome_path.with_suffix(outcome_path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                         encoding="utf-8")
    temporary.replace(outcome_path)
    summary = {"schema_version": 1, "status": "completed" if all(r["checker_rejected"] is not None for r in rows)
               else "completed_with_incomplete", "units": len(rows),
               "completed_units": sum(r["checker_rejected"] is not None for r in rows)}
    atomic_json(run_dir / "result.json", summary)
    atomic_json(run_dir / "controller_status.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only:
        prepared = prepare(args.config)
        print(json.dumps({"cases": len(prepared[3]), "styles": sorted(prepared[4]),
                          "tasks": len(prepared[5]), "hashes": prepared[6]}, indent=2))
    else:
        run(args.config)
