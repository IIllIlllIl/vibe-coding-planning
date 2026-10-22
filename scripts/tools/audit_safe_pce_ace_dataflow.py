"""Read-only structural audit of a terminal Verified Safe-PCE/ACE smoke.

This checks persisted evidence, not model comprehension or leakage semantics.
It calls no model, container, scheduler, or artifact writer. JSON goes to stdout.
"""
import argparse
import hashlib
import json
from pathlib import Path

from src.agents.plan_agent import _extract_bounded_direct_plan
from src.optimization.agent_references import phase_references, decode_phase_output
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot, validate_phase
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import pce_cases
from src.optimization.safe_pce_execution import load_pce_config
from src.optimization.behavioral_hybrid import checker_projection
from src.optimization.safe_pce_learning import SafePCEBackend, outcome_score, _validate_learning_evidence


def _json(path):
    return json.loads(path.read_text())


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _equal(actual, expected, boundary):
    if actual != expected:
        raise ValueError(f"dataflow mismatch: {boundary}")


def _within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("audit reference escapes its evidence root")
    return path


def audit(config_path):
    config = load_optimization_config(config_path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    backend = SafePCEBackend(config)
    if backend.mode != "swe_verified_pce":
        raise ValueError("this checkpoint audit currently supports Verified only")
    root = config.run_dir
    _equal(_json(root / "controller_status.json")["status"], "completed", "terminal controller")
    train = {row["instance_id"] for row in _rows(config.dataset_snapshot / "train.jsonl")}
    validation = {row["instance_id"] for row in _rows(config.dataset_snapshot / "validation.jsonl")}
    if train & validation:
        raise ValueError("overlapping splits")
    sources = {case.instance_id: case for case in pce_cases(backend.mode, backend.pce)}
    physical = _rows(root / "safe_pce_physical_evaluations.jsonl")
    aggregate = _rows(root / "safe_pce_evaluations.jsonl")
    if not physical or not aggregate:
        raise ValueError("missing PCE evidence")
    texts = [config.initial_rules_path.read_text().strip()]
    texts.extend(_json(path)["candidate"] for path in (root / "hybrid_proposals").glob("*.json")
                 if "candidate" in _json(path))
    artifacts = {hashlib.sha256(text.encode()).hexdigest(): json.loads(text) for text in texts}
    for row in physical:
        _equal(row["status"], "scored", "physical completion")
        output = row["pce_output"]
        _validate_learning_evidence(output)
        _equal(row["score"], outcome_score(output, mode=backend.mode, instance_id=row["instance_id"]), "score")
        expected_split = train if row["split"] == "train" else validation
        if row["instance_id"] not in expected_split:
            raise ValueError("physical observation outside frozen split")
        artifact = artifacts[row["candidate_sha256"]]
        repetitions = config.search.train_case_repetitions if row["split"] == "train" else 1
        effective_key = hashlib.sha256(json.dumps({"artifact": artifact,
            "repetitions": repetitions}, sort_keys=True).encode()).hexdigest()
        _, effective = load_pce_config(root / "pce_evaluations" / effective_key / "effective_pce.yaml")
        _equal(json.loads(effective.planning_rules), checker_projection(artifact), "candidate -> Planner projection")
        for field in ("code_prompt", "code_instance_template", "plan_prompt"):
            _equal(getattr(effective, field), getattr(backend.pce, field), "fixed prompt: " + field)
        inputs = [str(message.get("content", "")) for message in output["plan_trajectory"]
                  if message.get("role") in {"system", "user"}]
        for expected in (sources[row["instance_id"]].issue_description, effective.planning_rules):
            if not any(expected in message for message in inputs):
                raise ValueError("task/artifact absent from recorded Planner input")
        batch = _within(root / "hpc_tasks/pce", output["fingerprint"])
        task = f'task_{output["task_index"]:04d}.json'
        worker = _json(batch / "outputs" / task)
        for key in ("instance_id", "pce_run_index", "plan", "plan_sha256", "plan_trajectory",
                    "patch", "patch_sha256", "code_trajectory", "evaluator_result"):
            _equal(output[key], worker[key], f"worker -> physical: {key}")
        checkpoints = _within(root, output["checkpoint_relative_path"])
        plan = _json(checkpoints / "plan.json")["payload"]
        code = _json(checkpoints / "code.json")["payload"]
        evaluation = _json(checkpoints / "evaluate.json")["payload"]
        for field, expected in (("plan", plan["plan"]), ("plan_trajectory", plan["trajectory"]),
                                ("patch", code["patch"]), ("code_trajectory", code["trajectory"]),
                                ("evaluator_result", evaluation["evaluator_result"])):
            _equal(output[field], expected, "checkpoint -> output: " + field)
        _equal(plan["plan_sha256"], hashlib.sha256(plan["plan"].encode()).hexdigest(), "Plan SHA-256")
        bounded, error = _extract_bounded_direct_plan(plan["raw_plan_submission"],
            start_marker="START_PLAN", end_marker="END_PLAN")
        _equal(error, None, "direct submission boundary")
        _equal(bounded, output["plan"], "terminal submission -> Plan")
        if not any(output["plan"] in str(message.get("content", ""))
                   for message in output["code_trajectory"] if message.get("role") in {"system", "user"}):
            raise ValueError("exact Plan absent from recorded Coder input")
    for row in aggregate:
        runs = row["output"]["pce_runs"]
        expected = config.search.train_case_repetitions if row["split"] == "train" else 1
        _equal(len(runs), expected, "physical repetition count")
        for run in runs:
            if run not in physical:
                raise ValueError("aggregate contains a modified or unrecorded physical result")
            for field in ("candidate_sha256", "instance_id", "split"):
                _equal(run[field], row[field], "aggregate identity: " + field)
        _equal(row["score"], sum(run["score"] for run in runs) / expected, "repetition mean")
    phases = {role: 0 for role in ("reflector", "curator", "refiner")}
    manual = []
    for role in phases:
        for path in sorted((root / "hpc_tasks" / f"hybrid_{role}").glob("*/outputs/task_*.json")):
            output = _json(path)
            if output.get("status") != "completed":
                raise ValueError(f"incomplete retained {role} output: {path}")
            payload = _json(path.parent.parent / "tasks" / path.name)["payload"]
            validate_phase(role, payload, output["agent_output"], config)
            projected, cases, items = phase_references(role, payload)
            attempts = path.parent.parent / "attempts" / path.stem
            matches = []
            for completion in sorted(attempts.glob("*/completion.json")):
                resolved = completion.with_name("resolved_completion.json")
                if resolved.is_file() and _json(resolved)["agent_output"] == output["agent_output"]:
                    matches.append(completion)
            if not matches:
                raise ValueError(f"no matching raw completion: {path}")
            completion = matches[-1]
            _equal(decode_phase_output(role, _json(completion)["agent_output"], cases, items),
                   output["agent_output"], "raw completion -> canonical output")
            evidence = completion.parent / "inputs"
            manifest = _json(evidence / "manifest.json")
            for name in manifest["files"]:
                if not _within(evidence, name).is_file():
                    raise ValueError(f"missing staged evidence: {name}")
            if role in {"reflector", "curator"}:
                _equal(_json(evidence / "counted_playbook.json"), projected["parent"], "parent and counters -> Agent")
                records = [projected["record"]] if role == "reflector" else projected["records"]
                indexes = [_json(evidence / "record.json")] if role == "reflector" else _json(evidence / "records.json")
                _equal(len(indexes), len(records), "record count")
                for index, record in zip(indexes, records, strict=True):
                    restored = {key: _json(_within(evidence, file)) for key, file in index["fields"].items()}
                    _equal(restored, record, "lossless staged record")
                canonical = [payload["record"]] if role == "reflector" else payload["records"]
                for record in canonical:
                    if record["instance_id"] not in train or record["split"] != "train":
                        raise ValueError("non-training evidence supplied to learning")
                    source = sources[record["instance_id"]]
                    _equal(record["planner_input"], source.agent_projection(), "source -> decision-time input")
                    for run in record["pce_runs"]:
                        if run not in physical:
                            raise ValueError("learning record changed its raw PCE evidence")
            if role == "curator":
                if payload.get("history"):
                    _equal(_json(evidence / "rule_feedback_history.json"), payload["history"], "complete training history")
                _equal(_json(evidence / "case_reflections.json"), projected["reviews"], "complete reviews")
                index = _json(evidence / "reflection_index.json")
                _equal(len(index), len(projected["reviews"]), "reflection count")
                for entry, review in zip(index, projected["reviews"], strict=True):
                    restored = {key: value for key, value in entry.items() if key != "key_insights"}
                    restored["key_insights"] = [{key: value for key, value in insight.items() if key != "id"}
                                                for insight in entry["key_insights"]]
                    _equal(restored, review, "complete Ref -> required Cur index")
                if "reflection_index.json" not in manifest["required_files"]:
                    raise ValueError("complete Ref index is not required reading")
            if role == "refiner":
                _equal(_json(evidence / "draft.json"), projected["draft"], "complete draft -> Refiner")
            phases[role] += 1
            manual.append({"role": role, "completion": str(completion),
                           "required_files": manifest["required_files"]})
    if not phases["reflector"] or not phases["curator"]:
        raise ValueError("learning phases missing")
    ledger = _json(root / "global_counter_ledger.json")
    for event in ledger["history"].values():
        if event["instance_id"] not in train:
            raise ValueError("non-training feedback entered the counter ledger")
    for name in ("gepa_state.bin", "gepa_resume_state.json", "result.json", "safe_pce_candidate_metrics.json"):
        if not (root / name).is_file():
            raise ValueError(f"missing terminal authority: {name}")
    return {"structural_fidelity": "passed", "physical_records": len(physical),
            "aggregate_records": len(aggregate), "phases": phases,
            "manual_review_required": manual,
            "manual_scope": "Required-file tool reads/truncation recovery, rule application, source-access and evaluator-noise audit. Stored equality is not model comprehension or a leakage clearance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.config), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
