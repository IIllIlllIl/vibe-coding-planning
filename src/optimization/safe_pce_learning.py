"""Task/outcome adapter for ACE learning with Safe PCE instead of a Checker."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from gepa.core.adapter import EvaluationBatch

from src.exceptions import OfflineReflectionBlocked
from src.optimization.behavioral_hybrid import validate_candidate
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.safe_pce_execution import (
    execute_pce, load_pce_config, materialize_pce_config, pce_cases, sha256, validate_learning_source,
)

TASK_SEMANTICS = "safe_pce_resolution_v1"


@dataclass(frozen=True)
class SafePCELearningCase:
    instance_id: str
    split: str
    source: object

    def planner_input(self):
        projection = getattr(self.source, "agent_projection", None) or self.source.plan_input
        return projection()


class IncompletePCEEvaluation(RuntimeError):
    """An auditable incomplete observation, never a scored unresolved task."""


def outcome_score(output: dict, *, mode: str, instance_id: str) -> float:
    evaluator = output.get("evaluator_result", {})
    allowed = ({"tests_parsed"} if mode == "polybench_pce" else
               {"official_tests_resolved", "official_tests_unresolved"})
    resolved = evaluator.get("evaluator_resolved")
    if (output.get("status") != "completed"
            or output.get("pce_status") != "completed"
            or evaluator.get("status") != "completed"
            or evaluator.get("terminal_kind") not in allowed
            or type(resolved) is not bool
            or evaluator.get("task_outcome") != ("resolved" if resolved else "unresolved")
            or evaluator.get("test_timed_out") is True):
        raise IncompletePCEEvaluation(f"{instance_id}: incomplete or unsupported PCE outcome; inspect raw worker/evaluator evidence")
    if mode == "polybench_pce":
        if not evaluator.get("parsed_test_result") or not evaluator.get("code_patch_applied") or not evaluator.get("test_patch_applied"):
            raise IncompletePCEEvaluation(f"{instance_id}: missing parsed tests or patch-application evidence")
    else:
        report = evaluator.get("official_report", {}).get(instance_id, {})
        if not isinstance(report, dict) or report.get("resolved") is not resolved or not report.get("tests_status"):
            raise IncompletePCEEvaluation(f"{instance_id}: missing official test-status evidence")
    return float(resolved)


def _validate_learning_evidence(output: dict) -> None:
    """A score cannot substitute for the recorded Plan and execution evidence."""
    if not isinstance(output.get("plan"), str) or not output["plan"].strip():
        raise IncompletePCEEvaluation("PCE learning evidence lacks the original Plan")
    for field in ("plan_trajectory", "code_trajectory"):
        if not isinstance(output.get(field), list):
            raise IncompletePCEEvaluation(f"PCE learning evidence lacks recorded {field}")
    if not isinstance(output.get("patch"), str):
        raise IncompletePCEEvaluation("PCE learning evidence lacks the original patch")
    for field in ("plan", "patch"):
        expected = output.get(f"{field}_sha256")
        if expected and expected != hashlib.sha256(output[field].encode()).hexdigest():
            raise IncompletePCEEvaluation(f"PCE learning evidence {field} hash mismatch")


def _append(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class SafePCEBackend:
    """Small data/execution/report hook into the retained GEPA controller."""
    def __init__(self, config):
        self.config = config
        if config.safe_pce_config is None:
            raise ValueError("paths.safe_pce_config is required")
        self.mode, self.pce = load_pce_config(config.safe_pce_config)
        validate_learning_source(self.pce)

    def load_snapshot(self):
        root = self.config.dataset_snapshot
        manifest = json.loads((root / "manifest.json").read_text())
        if (manifest.get("schema_version") != 1 or not manifest.get("complete")
                or manifest.get("provisional") or manifest.get("task_semantics") != TASK_SEMANTICS
                or manifest.get("pce_mode") != self.mode):
            raise ValueError("Safe PCE learning requires a complete task-only split manifest")
        for name, path in (("source_manifest_sha256", self.pce.dataset_snapshot / "manifest.json"),
                           ("image_manifest_sha256", self.pce.image_manifest)):
            if manifest.get(name) != sha256(path):
                raise ValueError(f"Safe PCE split {name} mismatch")
        sources = {case.instance_id: case for case in pce_cases(self.mode, self.pce)}
        splits = []
        for split in ("train", "validation"):
            path = root / f"{split}.jsonl"
            if manifest.get(f"{split}_sha256") != sha256(path):
                raise ValueError(f"Safe PCE {split} file hash mismatch")
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if not rows or len(rows) != manifest.get(f"{split}_instances"):
                raise ValueError(f"Safe PCE {split} count mismatch or empty split")
            cases = []
            for row in rows:
                if set(row) != {"instance_id", "split"} or row["split"] != split or row["instance_id"] not in sources:
                    raise ValueError("Safe PCE splits contain task IDs/splits only; IDs must belong to the selected PCE source")
                cases.append(SafePCELearningCase(row["instance_id"], split, sources[row["instance_id"]]))
            splits.append(cases)
        identifiers = [case.instance_id for cases in splits for case in cases]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Safe PCE tasks duplicate or overlap train/validation")
        if len(splits[0]) < self.config.search.reflection_minibatch_size:
            raise ValueError("Safe PCE minibatch exceeds distinct training tasks")
        if self.config.execution.backend == "hpc_slurm":
            from src.environment.repository_history import RepositoryHistoryCache
            cache = RepositoryHistoryCache(self.pce.container.sif_cache_dir.parent / "repository-history-cache-v1")
            for case in [item.source for cases in splits for item in cases]:
                if cache.validate(sif_sha256=case.image.sif_sha256, base_commit=case.base_commit) is None:
                    raise ValueError(f"missing prepared base-ancestor history: {case.instance_id}")
        return tuple(splits)

    def evaluate(self, cases, artifact, repetitions):
        identity = hashlib.sha256(json.dumps({"artifact": artifact, "repetitions": repetitions}, sort_keys=True).encode()).hexdigest()
        directory = self.config.run_dir / "pce_evaluations" / identity
        effective = materialize_pce_config(self.pce, artifact, directory, repetitions=repetitions,
                                          run_dir=self.config.run_dir,
                                          job_name_prefix=self.config.hpc.job_name_prefix + "-pce")
        physical_cases = ([case.source for case in cases] if self.mode == "polybench_pce" else
                          [case.source for case in cases for _ in range(repetitions)])
        outputs = execute_pce(effective, self.mode, physical_cases, backend=self.config.execution.backend)
        if len(outputs) != len(cases) * repetitions:
            raise ValueError("Safe PCE batch output count mismatch")
        waves = [[] for _ in range(repetitions)]
        for index, case in enumerate(cases):
            for repetition in range(repetitions):
                output = outputs[index * repetitions + repetition]
                key = "source_instance_id" if self.mode == "polybench_pce" else "instance_id"
                if output.get(key, output.get("instance_id")) != case.instance_id:
                    raise ValueError("Safe PCE batch task order/identity mismatch")
                rep_key = "repetition" if self.mode == "polybench_pce" else "pce_run_index"
                if output.get(rep_key, 1) != repetition + 1:
                    raise ValueError("Safe PCE physical repetition identity mismatch")
                waves[repetition].append(output)
        return waves

    def seed_replay(self, cases, guideline):
        digest = hashlib.sha256(guideline.encode()).hexdigest()
        records = self._records()
        replay = {row["instance_id"]: (row["output"], row["score"]) for row in records
                  if row["candidate_sha256"] == digest and row["split"] == "validation"}
        if set(replay) != {case.instance_id for case in cases}:
            raise ValueError("Safe PCE seed validation replay is incomplete")
        return replay

    def _records(self):
        path = self.config.run_dir / "safe_pce_evaluations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []

    def write_report(self, result, validation):
        records = self._records()
        metrics = []
        expected = {case.instance_id for case in validation}
        for index, candidate in enumerate(result.candidates):
            digest = hashlib.sha256(candidate["rules"].encode()).hexdigest()
            rows = {row["instance_id"]: row for row in records if row["candidate_sha256"] == digest and row["split"] == "validation"}
            if set(rows) != expected:
                raise ValueError("Safe PCE validation evidence differs from the frozen split")
            score = sum(row["score"] for row in rows.values()) / len(rows)
            if not math.isclose(score, result.val_aggregate_scores[index], abs_tol=1e-12):
                raise ValueError("Safe PCE validation score mismatch")
            metrics.append({"candidate_idx": index, "candidate_sha256": digest,
                            "primary_metric": "resolved_rate", "validation_score": score,
                            "resolved": sum(row["score"] == 1 for row in rows.values()),
                            "unresolved": sum(row["score"] == 0 for row in rows.values()),
                            "total": len(rows), "parents": result.parents[index]})
        atomic_json(self.config.run_dir / "result.json", json.loads(json.dumps(result.to_dict(), default=list)))
        atomic_json(self.config.run_dir / "safe_pce_candidate_metrics.json", metrics)
        (self.config.run_dir / "best_guideline.txt").write_text(result.best_candidate["rules"] + "\n")
        (self.config.run_dir / "candidate_tree.html").write_text(result.candidate_tree_html())


class SafePCEGEPAAdapter:
    """GEPA's same scalar/trajectory interface, with no acceptability schema."""
    def __init__(self, evaluator, *, limits, proposer=None, run_dir=None,
                 train_case_repetitions=1, startup_seed_replay=None, seed_rules_sha256=None,
                 primary_metric="resolved_rate", class_counts_by_split=None, batch_executor=None):
        if primary_metric != "resolved_rate" or batch_executor is not None or class_counts_by_split:
            raise ValueError("Safe PCE adapter requires resolved_rate and the PCE backend, not Checker/class labels")
        self.evaluator, self.limits, self.propose_new_texts = evaluator, limits, proposer
        self.run_dir = run_dir
        self.repetitions = train_case_repetitions
        self.startup_seed_replay = startup_seed_replay
        self.seed_rules_sha256 = seed_rules_sha256
        self.mode = evaluator.__self__.mode

    def evaluate(self, batch, candidate, capture_traces=False):
        if set(candidate) != {"rules"} or not isinstance(candidate["rules"], str):
            raise ValueError("only the string component rules may be optimized")
        if len({case.split for case in batch}) > 1:
            raise ValueError("Safe PCE evaluation cannot mix splits")
        if capture_traces and any(case.split != "train" for case in batch):
            raise ValueError("validation/test trajectories cannot enter learning evidence")
        if len({case.instance_id for case in batch}) != len(batch):
            raise ValueError("Safe PCE evaluation requires distinct tasks")
        try:
            artifact = validate_candidate(candidate["rules"], self.limits)
        except (ValueError, TypeError):
            return EvaluationBatch(outputs=[{"invalid_candidate": True} for _ in batch],
                                   scores=[-100.0] * len(batch), trajectories=None)
        digest = hashlib.sha256(candidate["rules"].encode()).hexdigest()
        if (self.startup_seed_replay is not None and digest == self.seed_rules_sha256 and not capture_traces
                and {case.instance_id for case in batch} == set(self.startup_seed_replay)):
            rows = [self.startup_seed_replay[case.instance_id] for case in batch]
            self.startup_seed_replay = None
            return EvaluationBatch(outputs=[row[0] for row in rows], scores=[row[1] for row in rows], trajectories=None)
        count = self.repetitions if batch and batch[0].split == "train" else 1
        physical = []
        failures = []
        try:
            waves = self.evaluator(batch, artifact, count)
        except Exception as exc:
            # GEPA catches ordinary proposal/evaluation exceptions. An
            # infrastructure/integrity error must not consume an iteration.
            # ControllerYield is BaseException and keeps its existing path.
            raise OfflineReflectionBlocked(exc) from exc
        if (not isinstance(waves, list) or len(waves) != count
                or any(not isinstance(wave, list) or len(wave) != len(batch) for wave in waves)):
            if self.run_dir is not None:
                _append(self.run_dir / "safe_pce_physical_evaluations.jsonl", [{
                    "candidate_sha256": digest, "status": "incomplete_batch",
                    "instance_ids": [case.instance_id for case in batch],
                    "expected_repetitions": count, "raw_pce_waves": waves}])
            raise OfflineReflectionBlocked(IncompletePCEEvaluation(
                "Safe PCE evidence wave count/length mismatch; raw observations retained, no score produced"))
        for repetition, outputs in enumerate(waves, 1):
            rows = []
            for case, output in zip(batch, outputs, strict=True):
                entry = {"candidate_sha256": digest, "instance_id": case.instance_id, "split": case.split,
                         "repetition": repetition, "pce_output": output}
                try:
                    _validate_learning_evidence(output)
                    score = outcome_score(output, mode=self.mode, instance_id=case.instance_id)
                except (IncompletePCEEvaluation, ValueError, KeyError) as exc:
                    entry.update(status="incomplete", error=str(exc))
                    failures.append(entry)
                else:
                    entry.update(status="scored", score=score)
                rows.append(entry)
            physical.append(rows)
            if self.run_dir is not None:
                _append(self.run_dir / "safe_pce_physical_evaluations.jsonl", rows)
        if failures:
            raise OfflineReflectionBlocked(IncompletePCEEvaluation(
                f"{len(failures)} incomplete Safe PCE observations; batch scores/learning withheld. "
                "Inspect safe_pce_physical_evaluations.jsonl and existing PCE worker artifacts."))
        outputs, scores, traces, log = [], [], [], []
        for index, case in enumerate(batch):
            runs = [rows[index] for rows in physical]
            score = sum(row["score"] for row in runs) / count
            output = {"instance_id": case.instance_id, "score": score, "repetition_count": count,
                      "pce_runs": runs}
            outputs.append(output)
            scores.append(score)
            if capture_traces:
                traces.append({"instance_id": case.instance_id, "split": "train", "task_semantics": TASK_SEMANTICS,
                               "planner_input": case.planner_input(), "score": score,
                               "pce_runs": runs})
            log.append({"candidate_sha256": digest, "instance_id": case.instance_id, "split": case.split,
                        "score": score, "primary_metric": "resolved_rate", "output": output})
        if self.run_dir is not None:
            _append(self.run_dir / "safe_pce_evaluations.jsonl", log)
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=traces if capture_traces else None)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        if components_to_update != ["rules"] or eval_batch.trajectories is None:
            raise ValueError("Safe PCE reflection requires training trajectories for rules")
        if any(row.get("split") != "train" for row in eval_batch.trajectories):
            raise ValueError("Safe PCE reflection accepts training evidence only")
        return {"rules": eval_batch.trajectories}
