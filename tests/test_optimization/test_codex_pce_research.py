"""Local loop/dataflow acceptance; no model, container or scheduler is invoked."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from src.optimization.codex_pce_research import GuideConversation, ResearchConfig, load_config, run_research
from src.optimization.safe_pce_execution import load_pce_config, materialize_pce_config
from src.optimization.safe_pce_learning import IncompletePCEEvaluation
from src.exceptions import ControllerYield
from tests.test_optimization.test_safe_pce_learning import _prepared, _output


def _fixture(tmp_path):
    prepared = _prepared(tmp_path)
    manifest_path = prepared.dataset_snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for split in ("train", "validation"):
        path = prepared.dataset_snapshot / f"{split}.jsonl"
        row = json.loads(path.read_text().splitlines()[0])
        path.write_text(json.dumps(row) + "\n")
        manifest[f"{split}_instances"] = 1
        manifest[f"{split}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    seed = tmp_path / "seed.md"
    seed.write_bytes(b"# Seed\r\nInspect the task.\r\n")
    return ResearchConfig(prepared.dataset_snapshot, prepared.safe_pce_config, seed,
        tmp_path / "research", "Improve the planning guide using fixed Safe PCE evidence.", 2,
        {}, tmp_path / "unused-test.sif", "0" * 64)


class _Conversation:
    instances = []
    proposals = ["# Explore\r\nCheck {{ meaning }} and {% syntax %}.\r\n", "# Revision\nFollow the input to the consumer.\n"]

    def __init__(self, config, evidence, private):
        self.evidence = evidence
        self.indices = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def propose(self, index):
        self.indices.append(index)
        manifest = json.loads((self.evidence / "manifest.json").read_text())
        assert len(manifest["rounds"]) == index
        for path in self.evidence.rglob("*"):
            if path.is_file():
                text = path.read_bytes().decode()
                assert "VALIDATION_PRIVATE_TOKEN" not in text
                assert '"split": "validation"' not in text
                assert "Org__Repo-7" not in text
        if index == 2:
            assert manifest["rounds"][1]["validation"]["resolved"] == 0
            assert (self.evidence / "guides/0001.md").read_bytes().decode() == self.proposals[0]
        return self.proposals[index - 1]

    def __exit__(self, *exc):
        return False


def _executor(monkeypatch, *, fail_once=False, unresolved=False):
    calls = []
    failed = False
    def execute(effective, mode, cases, *, backend):
        nonlocal failed
        assert backend == "local" and mode == "polybench_pce"
        calls.append((effective.run_dir, cases[0].instance_id, effective.planning_rules))
        validation = cases[0].instance_id == "Org__Repo-7"
        if fail_once and validation and "Explore" in effective.planning_rules and not failed:
            failed = True
            raise OSError("synthetic evaluator interruption")
        resolved = not unresolved and ("Explore" not in effective.planning_rules if validation else "Seed" not in effective.planning_rules)
        output = _output(cases[0].instance_id, resolved)
        output["plan"] = ("VALIDATION_PRIVATE_TOKEN" if validation else "TRAIN_ORIGINAL_TOKEN") + "\n" + "Long evidence\n" * 2000
        output["plan_sha256"] = hashlib.sha256(output["plan"].encode()).hexdigest()
        return [output]
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)
    return calls


def test_two_task_loop_keeps_failed_guides_full_history_and_private_validation(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    _Conversation.instances = []
    calls = _executor(monkeypatch)
    rows = run_research(config, conversation_factory=_Conversation)
    assert len(calls) == 6 and [row["validation"]["resolved"] for row in rows] == [1, 0, 1]
    assert len(_Conversation.instances) == 1 and _Conversation.instances[0].indices == [1, 2]
    assert len({str(call[0]) for call in calls}) == 3
    assert all(call[2] == config.seed.read_bytes().decode() for call in calls[:2])
    assert all(call[2] == _Conversation.proposals[0] for call in calls[2:4])
    assert (config.run_dir / "analysis_inputs/guides/0001.md").read_bytes().decode() == _Conversation.proposals[0]
    assert rows[1]["validation"]["improved_over_best"] is False
    assert rows[2]["validation"]["improved_over_best"] is False
    assert "VALIDATION_PRIVATE_TOKEN" in (config.run_dir / "private/round-0001-validation.json").read_text()
    record = json.loads((config.run_dir / "analysis_inputs/rounds/0000/records.json").read_text())[0]
    root = config.run_dir / "analysis_inputs/rounds/0000"
    original = json.loads((root / record["fields"]["pce_runs"]).read_text())[0]["pce_output"]
    assert original["plan"].endswith("Long evidence\n" * 2000)
    assert len(run_research(config, conversation_factory=_Conversation)) == 3
    assert len(calls) == 6 and len(_Conversation.instances) == 1


def test_resume_reuses_completed_train_and_pending_guide_after_evaluator_failure(tmp_path, monkeypatch):
    config = replace(_fixture(tmp_path), max_updates=1)
    _Conversation.instances = []
    calls = _executor(monkeypatch, fail_once=True)
    with pytest.raises(OSError, match="evaluator interruption"):
        run_research(config, conversation_factory=_Conversation)
    assert json.loads((config.run_dir / "private/state.json").read_text())["pending"]["round"] == 1
    rows = run_research(config, conversation_factory=_Conversation)
    assert len(rows) == 2 and len(calls) == 5
    assert len(_Conversation.instances) == 1


def test_same_guide_in_new_round_is_a_fresh_pce_experiment(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    calls = _executor(monkeypatch)
    class SameGuide(_Conversation):
        proposals = [config.seed.read_bytes().decode()] * 2
        def propose(self, index):
            return self.proposals[index - 1]
    rows = run_research(config, conversation_factory=SameGuide)
    assert len(rows) == 3 and len(calls) == 6
    assert len({str(call[0]) for call in calls}) == 3


def test_free_guide_only_changes_the_planner_artifact_binding(tmp_path):
    config = _fixture(tmp_path)
    _, base = load_pce_config(config.pce_config)
    guide = "\r\n自由文本 {{ missing }}\r\n{% irrelevant %}\n"
    effective = materialize_pce_config(base, guide, tmp_path / "effective")
    assert effective.planning_rules == guide
    assert "rule_number" not in effective.plan_instance_template
    assert "Use the supplied planning guide" in effective.plan_instance_template
    assert effective.plan_prompt == base.plan_prompt
    assert effective.code_prompt == base.code_prompt
    assert effective.code_instance_template == base.code_instance_template
    assert effective.plan == base.plan and effective.code == base.code
    assert effective.container == base.container
    assert effective.evaluator_timeout == base.evaluator_timeout
    assert effective.agent_network_disabled == base.agent_network_disabled
    assert effective.evaluator_network_disabled == base.evaluator_network_disabled


def test_changed_frozen_inputs_do_not_resume_a_different_experiment(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    _executor(monkeypatch)
    run_research(config, conversation_factory=_Conversation)
    config.seed.write_text("Changed seed")
    with pytest.raises(ValueError, match="inputs or configuration changed"):
        run_research(config, conversation_factory=_Conversation)


def test_operational_failure_never_becomes_an_unresolved_score(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    def broken(*args, **kwargs):
        return [{"status": "failed", "instance_id": "Org__Repo-1"}]
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", broken)
    with pytest.raises(IncompletePCEEvaluation):
        run_research(config, conversation_factory=_Conversation)
    assert not (config.run_dir / "private/round-0000-train.json").exists()


def test_empty_guide_is_rejected_without_rewriting_or_evaluating_it(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    calls = _executor(monkeypatch)
    class Empty(_Conversation):
        def propose(self, index):
            return " \n"
    with pytest.raises(ValueError, match="nonempty UTF-8"):
        run_research(config, conversation_factory=Empty)
    assert len(calls) == 2


def test_native_empty_submission_receives_feedback_and_retains_raw_attempts(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    config.analysis_sif.write_bytes(b"test-only image")
    config = replace(config, analysis_sif_sha256=hashlib.sha256(config.analysis_sif.read_bytes()).hexdigest())
    calls = []
    class Session:
        def __init__(self, **kwargs):
            pass
        def call_text(self, **kwargs):
            calls.append(kwargs)
            return ("\n" if len(calls) == 1 else "Raw guide\r\n"), []
    monkeypatch.setattr("src.optimization.codex_pce_research.CodexConversation", Session)
    proposer = GuideConversation(config, tmp_path, tmp_path / "private")
    assert proposer.propose(1) == "Raw guide\r\n"
    assert "final response is empty" in calls[1]["user"]
    attempts = json.loads((tmp_path / "private/analysis-turn-0001.json").read_text())
    assert attempts[0]["raw_response"] == "\n"


def test_example_config_uses_free_text_goal_and_positive_update_budget():
    config = load_config("configs/codex_pce_research.example.yaml")
    assert config.max_updates == 2 and config.seed.is_file()
    assert config.model["executor"] == "codex_cli"
    assert not (config.run_dir / "analysis_inputs/guides/0001.md").exists()


def test_hpc_yield_uses_shared_backend_without_reopening_native_conversation(tmp_path, monkeypatch):
    config = replace(_fixture(tmp_path), execution_backend="hpc_slurm")
    _Conversation.instances = []
    execute = _executor(monkeypatch)
    from src.optimization import safe_pce_learning
    original = safe_pce_learning.execute_pce
    seen, sleeps = [], []
    def asynchronous(effective, mode, cases, *, backend):
        assert backend == "hpc_slurm"
        seen.append((effective.run_dir, cases[0].instance_id))
        if len(seen) == 1:
            raise ControllerYield(batch_dir="existing-pce-batch", job_id="123", reason="waiting")
        return original(effective, mode, cases, backend="local")
    monkeypatch.setattr(safe_pce_learning, "execute_pce", asynchronous)
    monkeypatch.setattr("src.environment.repository_history.RepositoryHistoryCache.validate",
                        lambda *args, **kwargs: (tmp_path, {}))
    monkeypatch.setattr("src.optimization.codex_pce_research.time.sleep", sleeps.append)
    rows = run_research(config, conversation_factory=_Conversation)
    assert len(rows) == 3 and len(execute) == 6 and len(seen) == 7
    assert seen[0] == seen[1] and len(sleeps) == 1
    assert len(_Conversation.instances) == 1 and _Conversation.instances[0].indices == [1, 2]
