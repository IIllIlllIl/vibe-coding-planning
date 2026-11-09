"""Task/outcome adapter for ACE learning with Safe PCE instead of a Checker."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil

from gepa.core.adapter import EvaluationBatch

from src.exceptions import OfflineReflectionBlocked
from src.optimization.audit import optional_diagnostic
from src.optimization.behavioral_hybrid import validate_candidate
from src.optimization.hpc.task_batch import atomic_json, atomic_write, retry_io
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

    def __init__(self, message: str, *, failure_record: dict | None = None):
        super().__init__(message)
        self.failure_record = failure_record or {}


def outcome_score(output: dict, *, mode: str, instance_id: str) -> float:
    evaluator = output.get("evaluator_result", {})
    if mode not in {"polybench_pce", "swe_verified_pce", "swe_rpg_pce"}:
        raise ValueError(f"unsupported Safe PCE scoring mode: {mode}")
    if output.get("pce_status") == "agent_failed":
        from src.optimization.online_models import ONLINE_OUTCOME_POLICY_VERSION
        failure = output.get("last_worker_output", {})
        attempts = output.get("agent_failure_attempts", [])
        if (output.get("status") != "completed"
                or output.get("outcome_policy_version") != ONLINE_OUTCOME_POLICY_VERSION
                or output.get("terminal_phase") not in {"plan", "code"}
                or failure.get("failure_category") != "agent"
                or failure.get("terminal_phase") != output.get("terminal_phase")
                or failure.get("instance_id") != instance_id
                or not isinstance(attempts, list)
                or not all(type(attempt) is int and attempt > 0 for attempt in attempts)
                or len(set(attempts)) < 3
                or evaluator != {"status": "not_run", "reason": "agent_failed_before_evaluation"}):
            raise IncompletePCEEvaluation(f"{instance_id}: invalid terminal Agent failure evidence")
        return 0.0
    allowed = ({"tests_parsed"} if mode == "polybench_pce" else
               {"rpg_tests_resolved", "rpg_tests_unresolved"} if mode == "swe_rpg_pce" else
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
    elif mode == "swe_rpg_pce":
        report = evaluator.get("rpg_report", {})
        codes = evaluator.get("stage_exit_codes", {})
        if (report.get("instance_id") != instance_id or report.get("resolved") is not resolved
                or not evaluator.get("rpg_parser", {}).get("ok") or not evaluator.get("test_status")
                or evaluator.get("code_patch_applied") is not True or evaluator.get("test_patch_applied") is not True
                or any(codes.get(k) != 0 for k in ("setup", "test_patch", "code_patch", "parser"))
                or any(k not in codes or codes[k] in {126, 127} for k in ("rebuild", "test", "print"))):
            raise IncompletePCEEvaluation(f"{instance_id}: missing RPG official grading/stage evidence")
    else:
        report = evaluator.get("official_report", {}).get(instance_id, {})
        if not isinstance(report, dict) or report.get("resolved") is not resolved or not report.get("tests_status"):
            raise IncompletePCEEvaluation(f"{instance_id}: missing official test-status evidence")
        targets = {name for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
                   for names in report["tests_status"].get(group, {}).values() for name in names}
        if not targets.intersection(evaluator.get("parsed_test_result", {})):
            raise IncompletePCEEvaluation(f"{instance_id}: no observed official target-test result")
    return float(resolved)


def _validate_learning_evidence(output: dict) -> None:
    """A score cannot substitute for the recorded Plan and execution evidence."""
    if not isinstance(output.get("plan"), str) or (not output["plan"].strip()
            and not (output.get("pce_status") == "agent_failed" and output.get("terminal_phase") == "plan")):
        raise IncompletePCEEvaluation("PCE learning evidence lacks the original Plan")
    for field in ("plan_trajectory", "code_trajectory"):
        if not isinstance(output.get(field), list):
            raise IncompletePCEEvaluation(f"PCE learning evidence lacks recorded {field}")
    if not isinstance(output.get("patch"), str):
        raise IncompletePCEEvaluation("PCE learning evidence lacks the original patch")
    if output.get("patch_submission", {}).get("policy") == "split_code_test_v1" and not isinstance(output.get("test_patch"), str):
        raise IncompletePCEEvaluation("PCE learning evidence lacks the original test patch")
    for field in ("plan", "patch", "test_patch"):
        expected = output.get(f"{field}_sha256")
        if expected and (not isinstance(output.get(field), str) or expected != hashlib.sha256(output[field].encode()).hexdigest()):
            raise IncompletePCEEvaluation(f"PCE learning evidence {field} hash mismatch")


def _iter_records(path: Path):
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def _append(path: Path, records):
    """Persist replay rows atomically; omit only exact cached-row repeats.

    The Controller is the sole writer. Distinct physical outputs/repetitions
    remain distinct, even for the same case and candidate. Existing malformed
    evidence is reported, never silently truncated or repaired. Retain hashes,
    not serialized history strings; copy the existing prefix byte-for-byte.
    """
    encode = lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True)
    identity = lambda row: hashlib.sha256(encode(row).encode("utf-8")).digest()
    seen = retry_io(lambda: {identity(row) for row in _iter_records(path)})
    added = []
    for record in records:
        line = encode(record).encode("utf-8")
        key = hashlib.sha256(line).digest()
        if key not in seen:
            seen.add(key)
            added.append(line)
    if added:
        def write_temporary(temporary):
            with temporary.open("wb") as target:
                if path.exists():
                    with path.open("rb") as source:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                        if source.tell():
                            source.seek(-1, 2)
                            if source.read(1) != b"\n":
                                target.write(b"\n")
                for line in added:
                    target.write(line)
                    target.write(b"\n")
        atomic_write(path, write_temporary)


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
        from .learning_diagnostics import load_learning_diagnostics
        source_manifest = json.loads((self.pce.dataset_snapshot / "manifest.json").read_text())
        self.learning_diagnostics = load_learning_diagnostics(root, manifest,
            [case.instance_id for case in splits[0]], source_revision=source_manifest.get("revision"))
        if self.config.execution.backend == "hpc_slurm":
            from src.environment.repository_history import RepositoryHistoryCache
            cache = RepositoryHistoryCache(self.pce.container.sif_cache_dir.parent / "repository-history-cache-v1")
            for case in [item.source for cases in splits for item in cases]:
                if cache.validate(sif_sha256=case.image.sif_sha256, base_commit=case.base_commit) is None:
                    raise ValueError(f"missing prepared base-ancestor history: {case.instance_id}")
        return tuple(splits)

    def learning_diagnostics_for(self, identifier):
        return getattr(self, "learning_diagnostics", {}).get(identifier)

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
        return _iter_records(path)

    def write_report(self, result, validation):
        candidate_digests = [hashlib.sha256(candidate["rules"].encode()).hexdigest()
                             for candidate in result.candidates]
        wanted = set(candidate_digests)
        validation_rows = {}
        for row in self._records():
            if row["split"] == "validation" and row["candidate_sha256"] in wanted:
                failed = any(run["pce_output"].get("pce_status") == "agent_failed"
                             for run in row["output"]["pce_runs"])
                validation_rows.setdefault(row["candidate_sha256"], {})[row["instance_id"]] = (row["score"], failed)
        metrics = []
        expected = {case.instance_id for case in validation}
        for index, digest in enumerate(candidate_digests):
            rows = validation_rows.get(digest, {})
            if set(rows) != expected:
                raise ValueError("Safe PCE validation evidence differs from the frozen split")
            score = sum(row[0] for row in rows.values()) / len(rows)
            if not math.isclose(score, result.val_aggregate_scores[index], abs_tol=1e-12):
                raise ValueError("Safe PCE validation score mismatch")
            metrics.append({"candidate_idx": index, "candidate_sha256": digest,
                            "primary_metric": "resolved_rate", "validation_score": score,
                            "resolved": sum(row[0] == 1 for row in rows.values()),
                            "unresolved": sum(row[0] == 0 for row in rows.values()),
                            "agent_failed": sum(row[1] for row in rows.values()),
                            "total": len(rows), "parents": result.parents[index]})
        def write_result(temporary):
            # Retain the complete result, including original trajectories,
            # without materializing several full JSON/Unicode copies.
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(result.to_dict(), stream, indent=2, ensure_ascii=False,
                          sort_keys=True, default=list)
                stream.write("\n")
        atomic_write(self.config.run_dir / "result.json", write_result)
        # result.json is the retained scientific result. These views are
        # reproducible from it and the scored evaluation records.
        with optional_diagnostic("candidate metrics export"):
            atomic_json(self.config.run_dir / "safe_pce_candidate_metrics.json", metrics)
        with optional_diagnostic("best candidate export"):
            (self.config.run_dir / "best_guideline.txt").write_text(result.best_candidate["rules"] + "\n")
        with optional_diagnostic("candidate tree export"):
            (self.config.run_dir / "candidate_tree.html").write_text(result.candidate_tree_html())


def finalize_safe_pce_run(config):
    """Export an already completed search without constructing any Agent/engine.

    Reporting code may differ from the frozen run. All non-source experiment
    semantics must still match; its manifest and checkpoints remain unchanged.
    """
    from gepa.core.result import GEPAResult
    from gepa.core.state import GEPAState
    from .behavioral_runner import _controller_lock
    from .resume import MANIFEST_VERSION, _semantic_config

    if config.task.semantics != TASK_SEMANTICS or config.safe_pce_config is None:
        raise ValueError("finalize-only requires Safe PCE learning")
    with _controller_lock(config):
        root = config.run_dir
        manifest = json.loads((root / "run_manifest.json").read_text())
        fingerprint = hashlib.sha256(json.dumps(manifest["semantic_config"],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if fingerprint != manifest.get("semantic_sha256"):
            raise ValueError("finalize-only frozen manifest fingerprint mismatch")
        original = dict(manifest["semantic_config"])
        expected = _semantic_config(config, initial_rules=config.initial_rules_path.read_text().strip())
        # This path never resumes optimization under changed source.
        original.pop("source")
        expected.pop("source")
        # A separate reporting staging root relocates the prompt file. Its
        # frozen content digest, rather than that incidental path, must match.
        for semantic in (original, expected):
            hybrid = semantic.get("hybrid", {})
            if hybrid.get("prompt_bundle_sha256"):
                hybrid.pop("prompt_bundle", None)
        if (manifest.get("version") != MANIFEST_VERSION or original != expected
                or manifest["latest_max_metric_calls"] != config.search.max_metric_calls):
            raise ValueError("finalize-only experiment semantics differ from the frozen run")
        progress = json.loads((root / "iteration_progress.json").read_text())
        resume = json.loads((root / "gepa_resume_state.json").read_text())
        if (progress.get("last_event") != "optimization_completed"
                or progress.get("completed_iterations") != config.search.max_iterations
                or resume.get("gepa_state_i", -1) + 1 != progress["completed_iterations"]):
            raise ValueError("finalize-only requires an aligned, completed search checkpoint")
        backend = SafePCEBackend(config)
        _, validation = backend.load_snapshot()
        state = GEPAState.load(str(root))
        if state.i != resume["gepa_state_i"]:
            raise ValueError("finalize-only checkpoint iteration mismatch")
        result = GEPAResult.from_state(state, run_dir=str(root), seed=config.search.seed)
        del state  # Release the full training cache before scanning/exporting evidence.
        backend.write_report(result, validation)
        warnings = bool(resume.get("reflection_failures")) or (
            resume.get("successful_proposals", 0) < config.search.min_proposals)
        atomic_json(root / "controller_status.json", {
            "schema_version": 1,
            "status": "completed_with_warnings" if warnings else "completed",
            "finalization_recovered": True,
        })
        return result


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
                with optional_diagnostic("incomplete PCE observation index"):
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
                    if output.get("pce_status") == "incomplete":
                        last = output.get("last_worker_output") or {}
                        raise IncompletePCEEvaluation(
                            f"{case.instance_id}: PCE incomplete after "
                            f"{output.get('attempts_exhausted')} allowed attempts; "
                            f"{last.get('error_type', 'missing worker output')}: {last.get('error', '')}")
                    _validate_learning_evidence(output)
                    score = outcome_score(output, mode=self.mode, instance_id=case.instance_id)
                except (IncompletePCEEvaluation, ValueError, KeyError) as exc:
                    entry.update(status="incomplete", error=str(exc))
                    failures.append(entry)
                else:
                    entry.update(status="scored", score=score)
                rows.append(entry)
            physical.append(rows)
        if failures:
            if self.run_dir is not None:
                # Successful batches already retain every raw output in the
                # required replay input. Keep this optional copy only when an
                # incomplete batch cannot enter that input.
                with optional_diagnostic("incomplete PCE observation index"):
                    _append(self.run_dir / "safe_pce_physical_evaluations.jsonl",
                            [row for rows in physical for row in rows])
            raise OfflineReflectionBlocked(IncompletePCEEvaluation(
                f"{len(failures)} incomplete Safe PCE observations; batch scores/learning withheld. "
                "Inspect safe_pce_physical_evaluations.jsonl and existing PCE worker artifacts.",
                failure_record={"details": [row["pce_output"]["failure_record"] for row in failures
                                            if row["pce_output"].get("failure_record")]}))
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
            try:
                # This is a replay input, not a log: retain exact physical
                # outputs and scores before returning them to GEPA.
                _append(self.run_dir / "safe_pce_evaluations.jsonl", log)
            except Exception as exc:
                raise OfflineReflectionBlocked(exc) from exc
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=traces if capture_traces else None)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        if components_to_update != ["rules"] or eval_batch.trajectories is None:
            raise ValueError("Safe PCE reflection requires training trajectories for rules")
        if any(row.get("split") != "train" for row in eval_batch.trajectories):
            raise ValueError("Safe PCE reflection accepts training evidence only")
        # Add retrospective references only after training execution. Raw PCE
        # outputs/logs and the caller's original trajectories remain untouched.
        import copy
        from_backend = getattr(self.evaluator.__self__, "learning_diagnostics_for", None)
        records = [dict(record) for record in eval_batch.trajectories]
        if from_backend is not None:
            for record in records:
                diagnostics = from_backend(record["instance_id"])
                if diagnostics is not None:
                    record["retrospective_diagnostics"] = copy.deepcopy(diagnostics)
        return {"rules": records}
