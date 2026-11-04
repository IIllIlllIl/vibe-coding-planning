"""No-inference acceptance of autonomous reading and the unchanged ACE boundary."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from src.optimization import coordinated_ace as module
from src.optimization.agent_references import decode_phase_output
from src.optimization.behavioral_hybrid_ace import _playbook, IncrementalHybridACEProposer
from src.optimization.behavioral_hybrid_runtime import hybrid_limits, run_hybrid_optimization
from src.optimization.config import load_optimization_config
from src.optimization.playbook_adapter import GlobalPlaybookCounters
from src.optimization.safe_pce_preparation import prepare_historical_replay
from tests.test_optimization.test_safe_pce_learning import _prepared, _output, ARTIFACT
from tests.test_optimization.test_behavioral_hybrid_runtime import review


def test_native_conversation_resumes_same_private_state_without_exporting_auth(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from src.optimization import codex_cli_runtime as runtime
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    auth = tmp_path / "auth.json"
    auth.write_text('{"auth":"test-only"}')
    model = {"executor": "codex_cli", "model": "gpt-6-sol", "reasoning_effort": "high",
             "codex_binary": "/test/codex", "codex_auth_file": str(auth)}
    mounted = tmp_path / "mounted-output"
    mounted.mkdir()
    @contextmanager
    def launch(self, config, environment, attempt):
        yield [], environment, config, mounted
    monkeypatch.setattr(runtime.CodexSIFExecution, "launch", launch)
    monkeypatch.setattr(runtime, "_runtime_preflight", lambda *args, **kwargs: [])
    commands, homes = [], []
    def run(command, **kwargs):
        commands.append(command)
        assert "--ephemeral" not in command and "features.multi_agent=true" in command
        home = Path(kwargs["env"]["CODEX_HOME"])
        homes.append(home)
        assert (home / "auth.json").read_text() == '{"auth":"test-only"}'
        if len(commands) == 2:
            assert command[command.index("resume") + 1] == "same-root-thread"
        output = Path(command[command.index("--output-last-message") + 1]).name
        (mounted / output).write_text('{"stage":' + str(len(commands)) + '}')
        sessions = home / "sessions"
        sessions.mkdir(exist_ok=True)
        (sessions / "child.jsonl").write_bytes(b'{"command":"inspect"}\r\n')
        return SimpleNamespace(returncode=0, stdout='{"type":"thread.started","thread_id":"same-root-thread"}\n', stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    boundary = runtime.CodexSIFExecution(tmp_path / "test.sif", evidence,
        output_files=("one.json", "two.json"))
    with runtime.CodexConversation(model_config=model, container=boundary, attempt_dir=tmp_path / "attempt") as session:
        assert session.call_json(system="Role", user="Evidence", task="Reflect", output_filename="one.json")[0] == {"stage": 1}
        assert session.call_json(system="Role", user="Counted evidence", task="Curate", output_filename="two.json")[0] == {"stage": 2}
        archive = session.session_evidence()
        assert archive == [{"path": "child.jsonl", "text": '{"command":"inspect"}\r\n'}]
    assert homes[0] == homes[1] and not homes[0].exists()


def record(identifier):
    return {"instance_id": identifier, "split": "train", "task_semantics": "safe_pce_resolution_v1",
            "planner_input": {"task": "Preserve the required behavior"}, "score": 0.0,
            "pce_runs": [{"pce_output": _output(identifier), "score": 0.0}]}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    config = load_optimization_config("configs/gepa_safe_pce_rpg_codex_diag6_v1_20261102.yaml", require_api_keys=False)
    config = replace(config, hybrid=replace(config.hybrid, learning_mode="codex_subagents",
        prompt_bundle="configs/prompts/safe_pce_hybrid_subagents_v1.yaml", prompt_bundle_sha256=""))
    parent = deepcopy(ARTIFACT)
    rows = [record("task-A"), record("task-B")]
    ledger = GlobalPlaybookCounters()
    book = ledger.hydrate(_playbook(parent))
    previous = record("previous-task")
    assessed = review(parent, previous)
    counted, pending = ledger.apply_reviews(book, [assessed])
    ledger.commit(counted, pending, feedback=ledger.feedback_for(book, [assessed], [previous], include_inactive=True))
    monkeypatch.setattr("src.optimization.behavioral_hybrid_runtime.require_evidence_sif", lambda config: tmp_path / "test.sif")
    return config, {"parent": parent, "records": rows, "ledger": ledger.export()}


def test_one_conversation_host_counters_and_complete_unprioritized_index(inputs, tmp_path):
    config, payload = inputs
    instances = []
    class Conversation:
        def __init__(self, **kwargs):
            self.root = kwargs["container"].evidence_dir
            self.calls = 0
            instances.append(self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call_json(self, **kwargs):
            self.calls += 1
            index = json.loads((self.root / "index.json").read_text())
            assert "required_files" not in index and "optional_files" not in index
            if self.calls == 1:
                parent = json.loads((self.root / "parent.json").read_text())
                refs = json.loads((self.root / "records.json").read_text())
                output = {"reviews": [review(parent, case) for case in reversed(refs)]}
                (self.root.parent / "reviews_response.json").write_text(json.dumps(output))
                return output, [{"role": "assistant", "content": "reports"}]
            parent = json.loads((self.root / "parent.json").read_text())
            assert parent["counters"][0]["helpful"] == 3
            history = json.loads((self.root / "history.json").read_text())
            assert len(history["reports"]) == 3
            assert "reviews.json" in index["entries"]
            output = {"reasoning": "Maintain evidence-backed boundaries", "operations": []}
            (self.root.parent / "curator_response.json").write_text(json.dumps(output))
            return output, [{"role": "assistant", "content": "delta"}]
        def session_evidence(self):
            return [{"path": "child.jsonl", "text": '{"command":"inspect evidence"}\n'}]
    output, trajectory = module.run_coordinated_phase(config, payload, tmp_path / "attempt", conversation_factory=Conversation)
    assert len(instances) == 1 and instances[0].calls == 2
    assert [r["instance_id"] for r in output["reviews"]] == ["task-A", "task-B"]
    assert output["reviews"][0]["bullet_tags"][0]["id"] == "plan-00001"
    assert trajectory[-1]["role"] == "codex_session_archive"
    # Original ledger is not rewritten by the worker's tentative bookkeeping.
    assert json.loads((tmp_path / "attempt/inputs/ledger.json").read_text()) == payload["ledger"]
    module.verify_coordinator_evidence(tmp_path / "attempt", payload, output, hybrid_limits(config))


def test_case_view_paths_and_raw_scientific_payloads_are_lossless(inputs, tmp_path):
    config, payload = inputs
    prompts = yaml.safe_load(Path(config.hybrid.prompt_bundle).read_text())
    root = tmp_path / "evidence"
    module.write_coordinator_evidence(root, payload, hybrid_limits(config), prompts)
    records = json.loads((root / "records.json").read_text())
    for item, original in zip(records, payload["records"], strict=True):
        assert json.loads((root / item["fields"]["pce_runs"]).read_text()) == original["pce_runs"]
        for run in item["pce_evidence"]["runs"]:
            for key, path in run.items():
                if key != "run_number": assert (root / path).is_file()
            detail = json.loads((root / run["run"]).read_text())
            assert all((root / value["file"]).is_file() for value in detail["pce_output"].values()
                       if isinstance(value, dict) and set(value) == {"file"})


def test_invalid_ref_cannot_reach_curator_or_counter_update(inputs, tmp_path):
    config, payload = inputs
    class BadConversation:
        def __init__(self, **kwargs): self.calls = 0
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call_json(self, **kwargs):
            self.calls += 1
            assert self.calls == 1
            return {"reviews": []}, []
        def session_evidence(self): return []
    with pytest.raises(ValueError, match="exactly once"):
        module.run_coordinated_phase(config, payload, tmp_path / "attempt", conversation_factory=BadConversation)
    assert (tmp_path / "attempt/reviews_completion.json").is_file()


def test_coordinator_reuses_standard_ledger_and_operations(inputs, tmp_path):
    config, payload = inputs
    def forbidden(*args, **kwargs): pytest.fail("independent Ref/Cur must not run")
    def coordinate(parent, rows, ledger, index):
        assert index == 0
        assert not ledger["history"]
        return {"reviews": [review(parent, row) for row in rows],
                "curator_delta": {"reasoning": "Retain", "operations": []}}, [{"role": "assistant"}]
    proposer = IncrementalHybridACEProposer(forbidden, forbidden, hybrid_limits(config),
        coordinator=coordinate, ledger_path=tmp_path / "ledger.json")
    proposer({"rules": json.dumps(payload["parent"])}, {"rules": payload["records"]}, ["rules"])
    assert next(iter(proposer.ledger.snapshot().values()))["helpful"] == 2
    assert proposer.successful_proposals == 1


def test_coordinator_request_survives_controller_ledger_commit(inputs, tmp_path, monkeypatch):
    from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor
    config, payload = inputs
    config = replace(config, run_dir=tmp_path / "run", execution=replace(config.execution, backend="local"))
    payload = dict(payload, proposal_index=0)
    seen = []
    def phase(config, role, value, directory):
        seen.append(deepcopy(value))
        return {"reviews": [review(value["parent"], row) for row in value["records"]],
                "curator_delta": {"reasoning": "Retain", "operations": []}}, []
    monkeypatch.setattr("src.optimization.behavioral_hybrid_runtime.run_local_phase", phase)
    executor = HybridPhaseExecutor(config)
    executor.call("coordinator", payload)
    # Controller may already have committed current attributions before the
    # candidate PCE yields. Its existing request must remain the authority.
    changed = deepcopy(payload)
    changed["ledger"]["history"]["later-commit"] = {"instance_id": "task-A"}
    executor.call("coordinator", changed)
    assert seen == [payload, payload]
    changed["records"][0]["score"] = 1
    with pytest.raises(ValueError, match="request/source changed"):
        executor.call("coordinator", changed)
    from scripts.hpc_runtime import task_state_paths
    state = config.run_dir / "hpc_tasks/hybrid_coordinator/proposal-0000/task_state.json"
    state.write_text("{}")
    assert state in set(task_state_paths(config.run_dir))


def test_historical_replay_real_gepa_calls_only_new_candidate_pce(tmp_path):
    from src.optimization.safe_pce_learning import SafePCEBackend
    (tmp_path / "source").mkdir()
    config = _prepared(tmp_path / "source", iterations=1)
    backend = SafePCEBackend(config)
    train, validation = backend.load_snapshot()
    raw_config = yaml.safe_load((tmp_path / "source/learning.yaml").read_text())
    raw_config["hybrid"]["curator_model"] = {"executor": "codex_cli", "model": "gpt-6-sol", "reasoning_effort": "high"}
    source_config = tmp_path / "source/replay-source.yaml"
    source_config.write_text(yaml.safe_dump(raw_config))
    rows = [record(case.instance_id) for case in train[2:4]]
    prior = [record(case.instance_id) for case in train[:2]]
    ledger = GlobalPlaybookCounters()
    book = ledger.hydrate(_playbook(ARTIFACT))
    reports = [review(ARTIFACT, row) for row in [*prior, *rows]]
    history = ledger.feedback_for(book, reports, [*prior, *rows], include_inactive=True)
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps({"parent": ARTIFACT, "records": rows, "history": history,
                                    "candidate": "old candidate must not be imported"}))
    from src.optimization.audit import text_sha256
    digest = text_sha256(config.initial_rules_path.read_text().strip())
    physical = []
    for row in rows:
        row["pce_runs"][0].update(instance_id=row["instance_id"], split="train", candidate_sha256=digest, repetition=1, status="scored")
        physical.append({"candidate_sha256": digest, "instance_id": row["instance_id"], "split": "train", "score": 0.0,
            "primary_metric": "resolved_rate", "output": {"instance_id": row["instance_id"], "score": 0.0,
                "repetition_count": 1, "pce_runs": row["pce_runs"]}})
    proposal.write_text(json.dumps({"parent": ARTIFACT, "records": rows, "history": history,
                                    "candidate": "old candidate must not be imported"}))
    for case in validation:
        physical.append({"candidate_sha256": digest, "instance_id": case.instance_id, "split": "validation", "score": 0.0,
            "primary_metric": "resolved_rate", "output": {"instance_id": case.instance_id, "score": 0.0,
                "repetition_count": 1, "pce_runs": [{"pce_output": _output(case.instance_id), "score": 0.0}]}})
    evaluations = tmp_path / "evaluations.jsonl"
    evaluations.write_text(''.join(json.dumps(row) + '\n' for row in physical))
    pilot_config = tmp_path / "pilot.yaml"
    prepare_historical_replay(learning_config=source_config, proposal_path=proposal, evaluations_path=evaluations,
        snapshot=tmp_path / "pilot-inputs", run_dir=tmp_path / "pilot", output_config=pilot_config)
    pilot = load_optimization_config(pilot_config, require_api_keys=False)
    pilot = replace(pilot, execution=replace(pilot.execution, backend="local"))
    assert "old candidate must not be imported" not in (pilot.dataset_snapshot / "training_replay.json").read_text()
    calls = []
    class Backend(SafePCEBackend):
        def evaluate(self, cases, artifact, repetitions):
            assert artifact != ARTIFACT
            calls.append([case.instance_id for case in cases])
            return [[_output(case.instance_id, True) for case in cases]]
    class Phases:
        def call(self, role, payload):
            assert role == "coordinator"
            assert [r["instance_id"] for r in payload["records"]] == [r["instance_id"] for r in rows]
            assert all(r["split"] == "train" for r in payload["records"])
            assert len(payload["ledger"]["reports"]) == 2
            return {"reviews": [review(payload["parent"], row) for row in payload["records"]],
                "curator_delta": {"reasoning": "Learn reusable preservation", "operations": [
                    {"type": "ADD", "content": {"condition": "Does the approach preserve required boundaries?",
                     "guide": "Trace changed behavior and adjacent contract tests."},
                     "supporting_instance_ids": [rows[0]["instance_id"]]}]}}, []
    result = run_hybrid_optimization(pilot, phase_executor=Phases(), evaluation_backend=Backend(pilot))
    assert result is not None
    assert calls == [[row["instance_id"] for row in rows], [case.instance_id for case in validation]]
    assert (pilot.run_dir / "gepa_state.bin").is_file()
    assert len(json.loads((pilot.run_dir / "global_counter_ledger.json").read_text())["reports"]) == 4
