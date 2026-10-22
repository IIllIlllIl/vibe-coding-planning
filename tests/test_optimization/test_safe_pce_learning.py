"""No-model acceptance tests: real ACE/GEPA and retained Safe PCE boundaries."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace

import pytest
import yaml

from src.agents.planning_artifact import PLANNING_ARTIFACT_INSTRUCTIONS
from src.exceptions import OfflineReflectionBlocked
from src.optimization.behavioral_dataset import BehavioralCaseLoader
from src.optimization.behavioral_hybrid import HybridLimits, checker_projection, validate_candidate
from src.optimization.behavioral_hybrid_ace import apply_hybrid_delta, _playbook, IncrementalHybridACEProposer
from src.optimization.behavioral_hybrid_runtime import run_hybrid_optimization
from src.optimization.config import load_optimization_config
from src.optimization.resume import DistinctEpochShuffledBatchSampler, prepare_run_manifest
from src.optimization.safe_pce_execution import materialize_pce_config, sha256, execute_pce
from src.optimization.safe_pce_learning import SafePCEBackend, SafePCEGEPAAdapter, IncompletePCEEvaluation, outcome_score
from src.optimization.safe_pce_preparation import prepare
from tests.test_polybench_pce import _config, _frozen_inputs, _diff, _stub_workspace_evidence
from src.polybench_pce.dataset import canonical_image_ref
from tests.test_optimization.test_behavioral_hybrid_runtime import review

BASELINE = Path("configs/gepa_swe_chat_hybrid_formal3_v9_20261015.yaml")
LIMITS = HybridLimits(10, 200, 40, 80, shared_guide_enabled=False)
ARTIFACT = json.loads(Path("configs/prompts/safe_pce_hybrid_seed_v1.json").read_text())
PLAN = "# Plan\n\nInspect the relevant behavior and implement the requested fix.\n\nRule 1: The task requires preserving the existing boundary.\nInspect its tests before changing it.\n"


def _prepared(tmp_path, *, iterations=3):
    source, images, _ = _frozen_inputs(tmp_path)
    original = json.loads((source / "instances.jsonl").read_text())
    image_data = json.loads(images.read_text())
    original_image = next(iter(image_data["records"].values()))
    rows, ids = [], []
    for index in range(8):
        row = deepcopy(original["source_row"])
        row["instance_id"] = f"Org__Repo-{index + 1}"
        ids.append(row["instance_id"])
        digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        rows.append({"source_row": row, "row_sha256": digest})
        image_data["records"][canonical_image_ref(row["instance_id"])] = original_image
    rows_path = source / "instances.jsonl"
    rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = json.loads((source / "manifest.json").read_text())
    manifest.update(instances=len(rows), instances_sha256=sha256(rows_path))
    (source / "manifest.json").write_text(json.dumps(manifest))
    images.write_text(json.dumps(image_data))
    pce_path = _config(tmp_path, source, images)
    pce = yaml.safe_load(pce_path.read_text())
    pce["plan"].update(temperature=1.0, thinking="disabled", submission_protocol="direct_human_markdown_v5")
    pce_path.write_text(yaml.safe_dump(pce))
    baseline = yaml.safe_load(BASELINE.read_text())
    baseline["search"].update(reflection_minibatch_size=2, max_iterations=iterations, min_proposals=iterations,
                              max_metric_calls=100, projection_metric_calls=100)
    baseline["execution"]["backend"] = "local"
    baseline_path = tmp_path / "baseline.yaml"
    baseline_path.write_text(yaml.safe_dump(baseline))
    path = tmp_path / "learning.yaml"
    prepare(learning_config=baseline_path, pce_config=pce_path, selection={"train": ids[:6], "validation": ids[6:]},
            snapshot=tmp_path / "learning_snapshot", run_dir=tmp_path / "learning_run", output_config=path)
    return load_optimization_config(path, require_api_keys=False)


def _output(instance_id, resolved=False):
    return {"status": "completed", "pce_status": "completed", "instance_id": instance_id,
            "plan": PLAN, "plan_trajectory": [{"role": "assistant", "content": "Planner evidence"}],
            "patch": _diff("src/module.py"), "code_trajectory": [{"role": "assistant", "content": "Coder evidence"}],
            "evaluator_result": {"status": "completed", "terminal_kind": "tests_parsed",
                                 "task_outcome": "resolved" if resolved else "unresolved",
                                 "evaluator_resolved": resolved, "code_patch_applied": True,
                                 "test_patch_applied": True, "parsed_test_result": {"test_x": "PASSED" if resolved else "FAILED"}}}


def test_prepare_reuses_existing_config_and_single_staging_layout(tmp_path):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, validation = backend.load_snapshot()
    assert (len(train), len(validation)) == (6, 2)
    assert all(case.split == "train" for case in train)
    assert config.safe_pce_config.is_relative_to(config.dataset_snapshot)
    assert backend.pce.dataset_snapshot.is_relative_to(config.dataset_snapshot)
    assert backend.pce.plan.temperature == 1.0
    assert backend.pce.plan.thinking == "disabled"
    assert backend.pce.hpc.mem == "4G"
    assert config.search.primary_metric == "resolved_rate"
    assert config.checker_prompt == ""  # No fake classification interface.
    assert config.hybrid.shared_guide_enabled is False
    assert "shared_guide" not in json.loads(config.initial_rules_path.read_text())
    assert "without mandatory section headings" not in backend.pce.plan_prompt
    assert "Start with the files" not in backend.pce.plan_prompt
    assert "safe_pce_resolution_v1" in Path("scripts/hpc_submit_batch.sh").read_text()
    assert not (config.run_dir / "hpc_tasks").exists()


def test_candidate_only_enters_planner_as_template_data(tmp_path):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    artifact = deepcopy(ARTIFACT)
    artifact["checklist"][0]["guide"] = "Treat {{missing}} and {% if broken %} as repository text."
    effective = materialize_pce_config(backend.pce, artifact, tmp_path / "effective")
    assert "{{ planning_rules }}" in effective.plan_instance_template
    assert "{{missing}}" not in effective.plan_instance_template
    assert "{{missing}}" in effective.planning_rules
    assert "plan-00001" not in effective.planning_rules
    assert "shared_guide" not in json.loads(effective.planning_rules)
    assert effective.code_prompt == backend.pce.code_prompt
    assert effective.code_instance_template == backend.pce.code_instance_template
    assert effective.plan_prompt == backend.pce.plan_prompt
    assert effective.hpc.mem == backend.pce.hpc.mem
    assert effective.agent_network_disabled == backend.pce.agent_network_disabled
    with pytest.raises(ValueError, match="identity mismatch"):
        materialize_pce_config(backend.pce, ARTIFACT, tmp_path / "effective")


def test_planner_records_are_requested_without_an_extra_format_contract():
    assert "rule_number" in PLANNING_ARTIFACT_INSTRUCTIONS
    assert "## Checklist application" not in PLANNING_ARTIFACT_INSTRUCTIONS
    assert "Accept/Reject" not in PLANNING_ARTIFACT_INSTRUCTIONS


def test_rules_only_schema_is_explicit_and_never_drops_an_extra_shared_guide():
    assert validate_candidate(json.dumps(ARTIFACT), LIMITS) == ARTIFACT
    assert LIMITS.relaxed().shared_guide_enabled is False
    assert not any(key.startswith("shared_guide") for key in LIMITS.agent_view())
    assert "token_counter_model" not in replace(LIMITS, shared_guide_tokens=512).agent_view()
    with pytest.raises(ValueError, match="candidate fields"):
        validate_candidate(json.dumps(dict(ARTIFACT, shared_guide="Unexpected strategy")), LIMITS)
    with pytest.raises(ValueError, match="Curator fields"):
        apply_hybrid_delta(ARTIFACT, {"reasoning": "Keep", "operations": [],
                                     "shared_guide": "Unexpected strategy"}, [], LIMITS)
    # The existing Checker projection can transport the same conditions/guides
    # for later transfer, with no invented shared guide.
    visible = checker_projection(ARTIFACT)
    assert set(visible) == {"schema_version", "checklist"}
    assert visible["checklist"][0] == {"rule_number": 1,
        "condition": ARTIFACT["checklist"][0]["condition"], "guide": ARTIFACT["checklist"][0]["guide"]}


def test_rules_only_uses_existing_four_operations_and_semantic_identity():
    records = [{"instance_id": "train"}]
    def apply(parent, operations):
        return json.loads(apply_hybrid_delta(parent, {"reasoning": "Preserve reusable knowledge",
                                                      "operations": operations}, records, LIMITS))
    added = apply(ARTIFACT, [{"type": "ADD", "content": {
        "condition": "Does the approach preserve important existing behavior?",
        "guide": "Inspect affected paths and tests for behavior that must remain."},
        "supporting_instance_ids": ["train"]}])
    updated = apply(added, [{"type": "UPDATE", "target_id": added["checklist"][0]["id"],
        "content": {"condition": added["checklist"][0]["condition"],
                    "guide": "Trace critical requirements through the proposed behavior and affected tests."},
        "supporting_instance_ids": ["train"]}])
    assert _playbook(updated).bullets[0].text != _playbook(added).bullets[0].text
    assert "shared_guide" not in json.loads(_playbook(updated).bullets[0].text)
    merged = apply(updated, [{"type": "MERGE", "target_ids": [item["id"] for item in updated["checklist"]],
        "content": {"condition": "Does the proposed behavior satisfy its critical contracts?",
                    "guide": "Compare explicit requirements and important existing boundaries with the proposed behavior."},
        "supporting_instance_ids": ["train"]}])
    assert len(merged["checklist"]) == 1
    removed = apply(added, [{"type": "REMOVE", "target_id": added["checklist"][1]["id"],
                            "supporting_instance_ids": ["train"]}])
    assert removed == ARTIFACT


def test_rules_only_refiner_keeps_schema_and_existing_transaction(tmp_path):
    caps = replace(LIMITS, max_items=1)
    records = [{"instance_id": "train", "split": "train"}]
    refined = []
    def curate(parent, reviews, rows, history, limits):
        assert history["events"]
        return {"reasoning": "Add a supported concern", "operations": [{"type": "ADD",
            "content": {"condition": "Does the approach preserve critical behavior?",
                        "guide": "Inspect affected paths for important behavior boundaries."},
            "supporting_instance_ids": ["train"]}]}, []
    def refine(draft, limits):
        assert "shared_guide" not in draft and len(draft["checklist"]) == 2
        refined.append(draft)
        return {"schema_version": 1, "checklist": [{"id": draft["checklist"][0]["id"],
            "condition": "Does the approach preserve critical behavior contracts?",
            "guide": "Compare explicit requirements and important existing boundaries with the proposed behavior."}]}, []
    proposer = IncrementalHybridACEProposer(lambda parent, row: (review(parent, row), []),
        curate, caps, refiner=refine, ledger_path=tmp_path / "ledger.json")
    result = proposer({"rules": json.dumps(ARTIFACT)}, {"rules": records}, ["rules"])
    assert refined and validate_candidate(result["rules"], caps)
    assert (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("executor", ["codex_cli", "mini_swe"])
def test_reflector_task_reaches_existing_transport_without_old_experiment_context(tmp_path, monkeypatch, executor):
    from src.optimization import playbook_runtime, codex_cli_runtime
    captured = []
    def external(**kwargs):
        captured.append(kwargs)
        return {}, []
    monkeypatch.setattr(playbook_runtime, "_codex_evidence_sif", lambda config: tmp_path / "existing.sif")
    monkeypatch.setattr(codex_cli_runtime, "run_codex_json_agent", external)
    monkeypatch.setattr(playbook_runtime, "_run_evidence_json_agent", external)
    common = dict(model_config={"executor": executor}, reflection_config={},
                  system="Analyze planning rules.", instance_template="Read {{ evidence_path }}.",
                  evidence_dir=str(tmp_path), internal_playbook=json.dumps(ARTIFACT))
    task = "Analyze the planning rules using the complete Planner, Coder, and evaluator evidence."
    playbook_runtime.run_evidence_reflector(**common, task=task)
    assert captured[-1]["task"] == task
    # Existing experiments retain their default role task.
    playbook_runtime.run_evidence_reflector(**common)
    assert captured[-1]["task"] == "Attribute this case to every active rejection rule."


def test_curator_worker_relaxes_only_size_for_rules_only_refinement(tmp_path):
    from src.optimization.behavioral_hybrid_runtime import validate_phase
    config = _prepared(tmp_path)
    config = replace(config, hybrid=replace(config.hybrid, max_items=1, refiner_enabled=True))
    records = [{"instance_id": "train", "split": "train"}]
    delta = {"reasoning": "Add a supported distinct concern", "operations": [{"type": "ADD",
        "content": {"condition": "Does the approach preserve important behavior?", "guide": "Inspect affected behavior boundaries."},
        "supporting_instance_ids": ["train"]}]}
    payload = {"parent": ARTIFACT, "records": records}
    validate_phase("curator", payload, delta, config)
    # Structural errors still fail; the worker must not quietly drop a guide.
    with pytest.raises(ValueError, match="Curator fields"):
        validate_phase("curator", payload, dict(delta, shared_guide="Unexpected"), config)


@pytest.mark.parametrize("size", [16, 24])
def test_pce_raw_plan_and_complete_evidence_survive_existing_file_layout(tmp_path, size):
    from src.optimization.agent_references import phase_references
    from src.optimization.behavioral_hybrid_evidence import write_hybrid_evidence
    records = []
    for index in range(size):
        output = _output(f"source-task-{index}")
        output["plan"] = (PLAN + "\n  Multiline observation: the contract is still violated.\n"
                          + '{"id": "plan-00001", "example": "{{ data }}"}\n' + "evidence\n" * 8192)
        output["plan_trajectory"].append({"id": "plan-00001", "content": {"instance_id": output["instance_id"]}})
        records.append({"instance_id": output["instance_id"], "split": "train",
                        "planner_input": {"issue_description": "Fix the behavior"},
                        "pce_runs": [{"pce_output": output}], "score": 0})
    payload = {"parent": ARTIFACT, "records": records,
               "reviews": [review(ARTIFACT, row) for row in records], "limits": LIMITS.agent_view()}
    original = deepcopy(payload)
    projected, _, _ = phase_references("curator", payload)
    assert payload == original
    manifest = write_hybrid_evidence(tmp_path, "curator", projected, canonical_parent=ARTIFACT)
    index = json.loads((tmp_path / "records.json").read_text())
    assert len(index) == size
    for number, row in enumerate(index):
        recovered = {key: json.loads((tmp_path / path).read_text()) for key, path in row["fields"].items()}
        assert recovered == projected["records"][number]
        assert recovered["pce_runs"] == records[number]["pce_runs"]
        assert recovered["pce_runs"][0]["pce_output"]["plan"] == records[number]["pce_runs"][0]["pce_output"]["plan"]
        assert all(path in manifest["optional_files"] for path in row["fields"].values())
    reviews = json.loads((tmp_path / "reflection_index.json").read_text())
    for actual, expected in zip(reviews, projected["reviews"], strict=True):
        assert all(actual[key] == expected[key] for key in expected)
    assert "reflection_index.json" in manifest["required_files"]


@pytest.mark.parametrize("resolved", [False, True])
def test_only_completed_benchmark_outcomes_score(resolved):
    assert outcome_score(_output("task", resolved), mode="polybench_pce", instance_id="task") == float(resolved)


@pytest.mark.parametrize("change", [
    {"terminal_kind": "test_timeout"}, {"terminal_kind": "test_patch_not_applied"},
    {"parsed_test_result": {}}, {"task_outcome": "unknown"}, {"evaluator_resolved": None},
    {"test_timed_out": True}, {"code_patch_applied": False},
])
def test_operational_or_silent_failures_never_score_zero(change):
    output = _output("task")
    output["evaluator_result"].update(change)
    with pytest.raises(IncompletePCEEvaluation):
        outcome_score(output, mode="polybench_pce", instance_id="task")


def test_adapter_full_trace_validation_boundary_and_invalid_score(tmp_path, monkeypatch):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, validation = backend.load_snapshot()
    calls = []
    def execute(effective, mode, cases, **kwargs):
        calls.append(cases)
        return [_output(case.instance_id, index % 2 == 0) for index, case in enumerate(cases)]
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)
    adapter = SafePCEGEPAAdapter(backend.evaluate, limits=LIMITS, run_dir=config.run_dir)
    raw = json.dumps(ARTIFACT)
    result = adapter.evaluate(train[:2], {"rules": raw}, capture_traces=True)
    assert result.scores == [1.0, 0.0]
    record = result.trajectories[0]
    assert record["planner_input"] == train[0].planner_input()
    assert "test_patch" not in json.dumps(record["planner_input"])
    assert "source_row" not in record
    assert record["pce_runs"][0]["pce_output"]["plan"] == PLAN
    assert "planner_application" not in record["pce_runs"][0]
    assert record["pce_runs"][0]["pce_output"]["code_trajectory"]
    assert "predicted_accept" not in record and "observed_accept" not in record
    with pytest.raises(ValueError, match="validation/test"):
        adapter.evaluate(validation, {"rules": raw}, capture_traces=True)
    assert adapter.evaluate(train[:2], {"rules": "broken"}).scores == [-100.0, -100.0]
    assert len(calls) == 1


def test_incomplete_batch_retains_successes_without_partial_learning(tmp_path, monkeypatch):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    def execute(effective, mode, cases, **kwargs):
        output = [_output(case.instance_id) for case in cases]
        output[-1].update(status="attempts_exhausted", pce_status="failed")
        return output
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)
    adapter = SafePCEGEPAAdapter(backend.evaluate, limits=LIMITS, run_dir=config.run_dir)
    with pytest.raises(OfflineReflectionBlocked):
        adapter.evaluate(train[:2], {"rules": json.dumps(ARTIFACT)}, capture_traces=True)
    rows = [json.loads(line) for line in (config.run_dir / "safe_pce_physical_evaluations.jsonl").read_text().splitlines()]
    assert [row["status"] for row in rows] == ["scored", "incomplete"]
    assert not (config.run_dir / "safe_pce_evaluations.jsonl").exists()
    assert not (config.run_dir / "global_counter_ledger.json").exists()


@pytest.mark.parametrize("field", ["plan", "plan_trajectory", "code_trajectory", "patch"])
def test_completed_outcome_cannot_hide_missing_learning_evidence(tmp_path, monkeypatch, field):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    def execute(effective, mode, cases, **kwargs):
        outputs = [_output(case.instance_id) for case in cases]
        outputs[-1].pop(field)
        return outputs
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)
    adapter = SafePCEGEPAAdapter(backend.evaluate, limits=LIMITS, run_dir=config.run_dir)
    with pytest.raises(OfflineReflectionBlocked):
        adapter.evaluate(train[:2], {"rules": json.dumps(ARTIFACT)}, capture_traces=True)
    rows = [json.loads(line) for line in (config.run_dir / "safe_pce_physical_evaluations.jsonl").read_text().splitlines()]
    assert rows[-1]["status"] == "incomplete" and field not in rows[-1]["pce_output"]
    assert not (config.run_dir / "safe_pce_evaluations.jsonl").exists()


@pytest.mark.parametrize("waves", [[], [[_output("one")]], [[_output("one"), _output("two")]]])
def test_partial_evidence_waves_are_retained_and_never_averaged(tmp_path, waves):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    adapter = SafePCEGEPAAdapter(backend.evaluate, limits=LIMITS, run_dir=config.run_dir, train_case_repetitions=2)
    adapter.evaluator = lambda *args: waves
    with pytest.raises(OfflineReflectionBlocked):
        adapter.evaluate(train[:2], {"rules": json.dumps(ARTIFACT)}, capture_traces=True)
    saved = json.loads((config.run_dir / "safe_pce_physical_evaluations.jsonl").read_text())
    assert saved["status"] == "incomplete_batch" and saved["raw_pce_waves"] == waves
    assert not (config.run_dir / "safe_pce_evaluations.jsonl").exists()


def test_real_three_iteration_gepa_and_resume_without_models(tmp_path, monkeypatch):
    config = _prepared(tmp_path)
    calls = []
    def execute(effective, mode, cases, **kwargs):
        improved = "revision" in effective.planning_rules
        calls.append([case.instance_id for case in cases])
        return [_output(case.instance_id, improved) for case in cases]
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)
    class Phases:
        curators = 0
        def call(self, role, payload):
            if role == "reflector":
                record = payload["record"]
                assert record["split"] == "train" and record["pce_runs"][0]["pce_output"]["plan"] == PLAN
                assert "checker_output" not in record
                return review(payload["parent"], record), []
            self.curators += 1
            assert payload["history"]["events"]
            assert "training instance ID" in payload["history"]["counting_policy"]
            item = payload["parent"]["checklist"][0]
            return {"reasoning": "Synthetic evidence-based update", "operations": [{
                "type": "UPDATE", "target_id": item["id"],
                "content": {"condition": item["condition"],
                            "guide": f"Compare requirements with independent evidence; revision {self.curators}."},
                "supporting_instance_ids": [payload["records"][0]["instance_id"]]}]}, []
    phases = Phases()
    result = run_hybrid_optimization(config, phase_executor=phases)
    assert phases.curators == 3
    assert result.val_aggregate_scores[0] == 0.0
    assert max(result.val_aggregate_scores) == 1.0
    assert (config.run_dir / "gepa_state.bin").exists()
    assert (config.run_dir / "gepa_resume_state.json").exists()
    assert (config.run_dir / "safe_pce_candidate_metrics.json").exists()
    assert not (config.run_dir / "behavioral_evaluations.jsonl").exists()
    assert not (config.run_dir / "cost_report.json").exists()
    assert json.loads((config.run_dir / "controller_status.json").read_text())["status"] == "completed"
    count = len(calls)
    run_hybrid_optimization(config, phase_executor=phases)
    assert len(calls) == count


def test_sampler_epoch_tail_unique_and_state_restorable():
    loader = BehavioralCaseLoader([SimpleNamespace(instance_id=f"task-{n}") for n in range(5)])
    rng = random.Random(42)
    sampler = DistinctEpochShuffledBatchSampler(3, rng)
    for index in range(5):
        batch = sampler.next_minibatch_ids(loader, SimpleNamespace(i=index))
        assert len(batch) == len(set(batch)) == 3
    restored = DistinctEpochShuffledBatchSampler(3, random.Random())
    restored.rng.setstate(sampler.rng.getstate())
    for field in ("shuffled_ids", "epoch", "id_freqs", "last_trainset_size"):
        setattr(restored, field, deepcopy(getattr(sampler, field)))
    for index in range(5, 10):
        assert sampler.next_minibatch_ids(loader, SimpleNamespace(i=index)) == restored.next_minibatch_ids(loader, SimpleNamespace(i=index))


def test_pce_prompts_and_inputs_frozen_in_optimizer_manifest(tmp_path):
    config = _prepared(tmp_path)
    config.run_dir.mkdir()
    raw = config.initial_rules_path.read_text().strip()
    prepare_run_manifest(config, initial_rules=raw)
    pce = yaml.safe_load(config.safe_pce_config.read_text())
    pce["prompts"]["code_system"] += " changed"
    config.safe_pce_config.write_text(yaml.safe_dump(pce))
    with pytest.raises(ValueError, match="configuration or source differs"):
        prepare_run_manifest(config, initial_rules=raw)


def test_real_polybench_worker_and_runner_preserve_plan_handoff(tmp_path, monkeypatch):
    from src.polybench_pce.runner import PolyBenchPCERunner
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    effective = materialize_pce_config(backend.pce, ARTIFACT, tmp_path / "effective")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr("src.polybench_pce.worker.configure_docker_capacity", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(PolyBenchPCERunner, "_verify_sif", lambda *a: None)
    monkeypatch.setattr(PolyBenchPCERunner, "_prepared_history", lambda *a: tmp_path / "prepared.bundle")
    monkeypatch.setattr("src.polybench_pce.runner.install_repository_history_bundle", lambda **kw: {"installed": True})
    monkeypatch.setattr("src.polybench_pce.runner.restore_repository_to_base", lambda *a, **kw: None)
    monkeypatch.setattr(PolyBenchPCERunner, "_environment", lambda *a, **kw: SimpleNamespace(cleanup=lambda: None))
    _stub_workspace_evidence(monkeypatch)
    observed = []
    def planner(agent_config, issue, env, **kwargs):
        assert "record_checklist_application" not in kwargs
        assert json.loads(kwargs["planning_rules"])["checklist"][0]["rule_number"] == 1
        assert "test_patch" not in kwargs["planning_rules"]
        assert agent_config.agent.temperature == 1.0 and agent_config.agent.thinking == "disabled"
        observed.append("plan")
        return PLAN, [{"role": "assistant", "content": "START_PLAN\n" + PLAN + "END_PLAN"}]
    def coder(agent_config, plan, issue, env, **kwargs):
        assert plan == PLAN
        assert agent_config.prompts.plan_generation_prompt == ""
        assert agent_config.prompts.plan_instance_template == ""
        assert "planning_rules" not in kwargs
        observed.append("code")
        return _diff("src/module.py"), [{"role": "assistant", "content": "Code evidence"}]
    monkeypatch.setattr("src.polybench_pce.runner.plan_agent.run", planner)
    monkeypatch.setattr("src.polybench_pce.runner.code_agent.run", coder)
    original_run = PolyBenchPCERunner.run
    def run(self, case):
        self.evaluator = lambda patch, case, **kw: _output(case.instance_id, True)["evaluator_result"]
        return original_run(self, case)
    monkeypatch.setattr(PolyBenchPCERunner, "run", run)
    cases = [train[0].source]
    outputs = execute_pce(effective, backend.mode, cases, backend="local")
    assert observed == ["plan", "code"]
    assert outputs[0]["plan"] == PLAN
    assert outcome_score(outputs[0], mode=backend.mode, instance_id=cases[0].instance_id) == 1.0
    execute_pce(effective, backend.mode, cases, backend="local")
    assert observed == ["plan", "code"]  # Exact completed output reuse.
    checkpoints = list(effective.run_dir.rglob("checkpoints/task_0000/plan.json"))
    assert len(checkpoints) == 1
    saved = json.loads(checkpoints[0].read_text())["payload"]
    assert saved["plan"] == PLAN and saved["raw_plan_submission"] == "START_PLAN\n" + PLAN + "END_PLAN"


def test_hpc_evaluation_delegates_one_whole_wave_to_pce_executor(tmp_path, monkeypatch):
    from src.polybench_pce.hpc_executor import PolyBenchPCEHPCExecutor
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    effective = materialize_pce_config(backend.pce, ARTIFACT, tmp_path / "effective")
    calls = []
    def wave(self, **kwargs):
        tasks = kwargs["tasks"]
        assert len(tasks) == 3
        script = kwargs["write_script"]([0, 1, 2], 1).read_text()
        assert "#SBATCH --array=0,1,2" in script
        assert "src.polybench_pce.worker" in script
        assert str(effective.config_path) in script
        outputs = []
        for task in tasks:
            value = _output(task.instance_id)
            value.update(fingerprint=kwargs["fingerprint"], task_index=task.index,
                         source_instance_id=task.instance_id, repetition=1, final_validation_label=None)
            kwargs["validate_output"](task, value)
            outputs.append(value)
        calls.append(tasks)
        return outputs
    monkeypatch.setattr("src.optimization.hpc.task_batch.SlurmTaskBatch.run", wave)
    output = execute_pce(effective, backend.mode, [case.source for case in train[:3]], backend="hpc_slurm")
    assert len(output) == 3 and len(calls) == 1


def test_threshold_failure_sets_terminal_status(tmp_path, monkeypatch):
    config = _prepared(tmp_path)
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce",
                        lambda effective, mode, cases, **kw: [_output(case.instance_id) for case in cases])
    class Phases:
        def call(self, role, payload):
            pytest.fail("optimization stub must not invoke learning agents")
    with pytest.raises(RuntimeError, match="threshold"):
        run_hybrid_optimization(config, phase_executor=Phases(), optimize_fn=lambda **kw: SimpleNamespace())
    assert json.loads((config.run_dir / "controller_status.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("resolved", [False, True])
def test_verified_official_outcome_boundary(resolved):
    output = _output("task", resolved)
    output["evaluator_result"].update(
        terminal_kind="official_tests_resolved" if resolved else "official_tests_unresolved",
        official_report={"task": {"resolved": resolved, "tests_status": {"FAIL_TO_PASS": {"success": ["test_x"]}}}})
    assert outcome_score(output, mode="swe_verified_pce", instance_id="task") == float(resolved)
    output["evaluator_result"]["official_report"]["task"]["tests_status"] = {}
    with pytest.raises(IncompletePCEEvaluation):
        outcome_score(output, mode="swe_verified_pce", instance_id="task")


def test_verified_binding_uses_existing_worker_and_safe_source_projection(tmp_path, monkeypatch):
    from tests.test_swe_verified_pce_pcce import _snapshot
    from src.optimization.safe_pce_execution import load_pce_config, pce_cases
    source, images, case = _snapshot(tmp_path)
    path = tmp_path / "verified.yaml"
    raw = {"mode": "swe_verified_pce", "paths": {"dataset_snapshot": str(source),
           "image_manifest": str(images), "run_dir": str(tmp_path / "unused")},
           "plan": {"model": "deepseek-v4-flash", "api_base": "https://api.deepseek.com", "max_attempts": 3,
                    "temperature": 1, "thinking": "disabled", "submission_protocol": "direct_human_markdown_v5"},
           "code": {"model": "deepseek-v4-flash", "api_base": "https://api.deepseek.com", "max_attempts": 3},
           "container": {"runtime": "apptainer", "sif_cache_dir": str(tmp_path / "sifs")},
           "hpc": {"max_task_attempts": 3, "cpus_per_task": 1, "mem": "4G", "time": "00:45:00"},
           "evaluator": {"slurm_timeout_outcome": "unknown"},
           "prompts": {"plan_system": "Plan", "plan_instance": "{{ task }}", "code_system": "Code", "code_instance": "{{ plan }}"}}
    path.write_text(yaml.safe_dump(raw))
    mode, base = load_pce_config(path)
    assert pce_cases(mode, base)[0].agent_projection() == case.agent_projection()
    effective = materialize_pce_config(base, ARTIFACT, tmp_path / "effective")
    def wave(self, **kwargs):
        script = kwargs["write_script"]([0], 1).read_text()
        assert "src.swe_verified_pce.worker" in script
        assert str(effective.config_path) in script
        value = _output(case.instance_id)
        value.update(fingerprint=kwargs["fingerprint"], task_index=0, pce_run_index=1, final_validation_label=None)
        kwargs["validate_output"](kwargs["tasks"][0], value)
        return [value]
    monkeypatch.setattr("src.optimization.hpc.task_batch.SlurmTaskBatch.run", wave)
    result = execute_pce(effective, mode, [case], backend="hpc_slurm")
    assert len(result) == 1
    assert "GOLD" not in effective.planning_rules and "TEST" not in effective.planning_rules


@pytest.mark.parametrize("role", ["reflector", "curator", "refiner"])
def test_existing_learning_worker_accepts_pce_evidence(tmp_path, monkeypatch, role):
    import src.optimization.behavioral_hybrid_runtime as runtime
    from src.optimization.behavioral_hybrid_worker import run_task
    config = _prepared(tmp_path)
    path = tmp_path / "learning.yaml"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda config: None)
    record = {"instance_id": "Org__Repo-1", "split": "train", "task_semantics": "safe_pce_resolution_v1",
              "planner_input": {"issue_description": "Fix the bug"}, "score": 0,
              "pce_runs": [{"pce_output": _output("Org__Repo-1")}]}
    if role == "reflector":
        payload = {"parent": ARTIFACT, "record": record}
        output = review({**ARTIFACT, "checklist": [{**ARTIFACT["checklist"][0], "id": "item-01"}]}, {"instance_id": "case-01"})
    elif role == "curator":
        payload = {"parent": ARTIFACT, "records": [record], "reviews": [review(ARTIFACT, record)], "history": {}}
        output = {"reasoning": "Keep supported knowledge", "operations": []}
    else:
        payload = {"draft": ARTIFACT, "limits": {}, "split": "train"}
        output = {**ARTIFACT, "checklist": [{**ARTIFACT["checklist"][0], "id": "item-01"}]}
    def agent(**kwargs):
        assert "Checker" not in kwargs["system"]
        assert "human acceptance" not in kwargs["system"]
        assert "Accept/Reject" not in kwargs["system"]
        assert "shared_guide" not in kwargs["system"]
        if role == "reflector":
            assert kwargs["task"] == "Analyze the planning rules using the complete Planner, Coder, and evaluator evidence."
        inputs = Path(kwargs["evidence_dir"])
        manifest = json.loads((inputs / "manifest.json").read_text())
        assert manifest["required_files"]
        for relative in manifest["files"]:
            assert (inputs / relative).is_file()
        if role == "reflector":
            index = json.loads((inputs / "record.json").read_text())
            runs = json.loads((inputs / index["fields"]["pce_runs"]).read_text())
            assert runs[0]["pce_output"]["plan"] == PLAN
            assert index["fields"]["pce_runs"] in manifest["required_files"]
        return output, [{"role": "assistant", "content": "Synthetic completion"}]
    monkeypatch.setattr(runtime, "run_evidence_reflector", agent)
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
    manifest = {"mode": "behavioral_hybrid_phase", "role": role, "payload": payload,
                "fingerprint": runtime.phase_identity(config, role, payload)}
    task_path, output_path = tmp_path / "task.json", tmp_path / "output.json"
    task_path.write_text(json.dumps(manifest))
    assert run_task(config_path=path, manifest_path=task_path, output_path=output_path,
                    attempt_dir=tmp_path / "attempt") == 0
    assert json.loads(output_path.read_text())["status"] == "completed"


def test_repetitions_share_one_array_and_standard_supervisor_task_root(tmp_path, monkeypatch):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    backend.config = replace(config, execution=replace(config.execution, backend="hpc_slurm"))
    waves = []
    def wave(self, **kwargs):
        tasks = kwargs["tasks"]
        assert len(tasks) == 4
        assert kwargs["batch_dir"].parent.parent == config.run_dir / "hpc_tasks"
        script = kwargs["write_script"]([0, 1, 2, 3], 1).read_text()
        assert "#SBATCH --array=0,1,2,3" in script
        outputs = []
        for task in tasks:
            manifest = json.loads(task.manifest_path.read_text())
            value = _output(task.instance_id, manifest["repetition"] == 1)
            value.update(fingerprint=kwargs["fingerprint"], task_index=task.index,
                         source_instance_id=manifest["source_instance_id"],
                         repetition=manifest["repetition"], final_validation_label=None)
            kwargs["validate_output"](task, value)
            outputs.append(value)
        waves.append(tasks)
        return outputs
    monkeypatch.setattr("src.optimization.hpc.task_batch.SlurmTaskBatch.run", wave)
    adapter = SafePCEGEPAAdapter(backend.evaluate, limits=LIMITS, train_case_repetitions=2, run_dir=config.run_dir)
    result = adapter.evaluate(train[:2], {"rules": json.dumps(ARTIFACT)}, capture_traces=True)
    assert result.scores == [0.5, 0.5]
    assert len(waves) == 1
    assert len(result.trajectories[0]["pce_runs"]) == 2


def test_executor_errors_and_yield_keep_existing_abort_boundary(tmp_path, monkeypatch):
    from src.exceptions import ControllerYield
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    train, _ = backend.load_snapshot()
    adapter = SafePCEGEPAAdapter(backend.evaluate, limits=LIMITS, run_dir=config.run_dir)
    def fail(*args, **kwargs):
        raise RuntimeError("transport or integrity failure")
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", fail)
    with pytest.raises(OfflineReflectionBlocked) as caught:
        adapter.evaluate(train[:2], {"rules": json.dumps(ARTIFACT)}, capture_traces=True)
    assert isinstance(caught.value.cause, RuntimeError)
    def yield_wave(*args, **kwargs):
        raise ControllerYield(batch_dir="existing_pce_wave", job_id="42", reason="waiting")
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", yield_wave)
    with pytest.raises(ControllerYield):
        adapter.evaluate(train[:2], {"rules": json.dumps(ARTIFACT)}, capture_traces=True)
