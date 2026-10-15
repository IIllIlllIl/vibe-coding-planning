"""Production hybrid dispatch and shared transport regressions, without inference."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import os

import pytest

from src.exceptions import ControllerYield, OfflineReflectionBlocked
from src.optimization import behavioral_hybrid_runtime as runtime
from src.optimization import behavioral_hybrid_worker as worker
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer
from src.optimization.config import load_optimization_config
from tests.test_optimization.test_behavioral_hybrid_runtime import PARENT, LIMITS, review


def config(tmp_path):
    value = load_optimization_config("configs/gepa_swe_chat_hybrid_formal3_v4_20261011.yaml", require_api_keys=False)
    return replace(value, run_dir=tmp_path / "run", hpc=replace(value.hpc, max_running_array_tasks=2))


def test_full_wave_yield_resume_and_retry_only_invalid_index(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    executor = runtime.HybridPhaseExecutor(cfg)
    submissions = []
    executor.runtime.submitter = lambda path: submissions.append(path.read_text()) or str(100 + len(submissions))
    records = [{"instance_id": f"case-{i}", "split": "train"} for i in range(3)]
    with pytest.raises(ControllerYield):
        executor.reflect_batch(PARENT, records)
    assert len(submissions) == 1
    assert "#SBATCH --array=0,1,2%2" in submissions[0]
    assert "module load tools/Apptainer" in submissions[0]
    assert "test -n" not in submissions[0]  # Native Codex does not require a DS key.
    subprocess.run(["bash", "-n"], input=submissions[0], text=True, check=True)
    batch = next((cfg.run_dir / "hpc_tasks/hybrid_reflector").iterdir())
    manifests = sorted((batch / "tasks").glob("*.json"))
    assert len(manifests) == 3
    outputs = []
    for index, path in enumerate(manifests):
        manifest = json.loads(path.read_text())
        output = batch / "outputs" / path.name
        output.parent.mkdir(exist_ok=True)
        value = {"status": "completed", "fingerprint": manifest["fingerprint"], "role": "reflector",
                 "agent_output": review(PARENT, records[index]), "trajectory": [{"index": index}]}
        if index == 1:
            value["agent_output"] = {}
            value.update(status="agent_failed", error="Invalid Reflector output", error_type="ValueError")
        output.write_text(json.dumps(value))
        outputs.append(output)
    retained = {index: outputs[index].read_bytes() for index in (0, 2)}
    # Fresh Controller instance; shared batch journal owns recovery.
    resumed = runtime.HybridPhaseExecutor(cfg)
    resumed.runtime.submitter = executor.runtime.submitter
    with pytest.raises(ControllerYield):
        resumed.reflect_batch(PARENT, records)
    assert len(submissions) == 2
    assert "#SBATCH --array=1" in submissions[1]
    assert "failed_outputs/attempt_01/task_${TASK_ID}.json" in submissions[1]
    assert (batch / "failed_outputs/attempt_01/task_0001.json").exists()
    subprocess.run(["bash", "-n"], input=submissions[1], text=True, check=True)
    manifest = json.loads(manifests[1].read_text())
    outputs[1].write_text(json.dumps({"status": "completed", "fingerprint": manifest["fingerprint"], "role": "reflector",
        "agent_output": review(PARENT, records[1]), "trajectory": [{"index": 1}]}))
    result = resumed.reflect_batch(PARENT, records)
    assert [item[0]["instance_id"] for item in result] == [r["instance_id"] for r in records]
    assert len(submissions) == 2
    assert all(outputs[index].read_bytes() == value for index, value in retained.items())


@pytest.mark.parametrize("role", ["reflector", "curator", "refiner"])
def test_actual_worker_role_payload_and_validation(tmp_path, monkeypatch, role):
    cfg = config(tmp_path)
    monkeypatch.setattr(worker, "load_optimization_config", lambda *_: cfg)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    record = {"instance_id": "case", "split": "train"}
    if role == "reflector":
        payload = {"parent": PARENT, "record": record}
        expected = review(PARENT, record)
    elif role == "curator":
        payload = {"parent": PARENT, "records": [record], "history": {}}
        expected = {"reasoning": "Retain grounded knowledge", "operations": [], "shared_guide": PARENT["shared_guide"]}
    else:
        payload = {"draft": PARENT, "limits": vars(LIMITS), "split": "train"}
        expected = PARENT
    calls = []
    from src.optimization.agent_references import phase_references, project_identities
    projected, case_refs, item_refs = phase_references(role, payload)
    if "limits" in projected:
        projected["limits"] = runtime.hybrid_limits(cfg).agent_view()
    agent_expected = project_identities(expected, case_refs, item_refs)
    def agent(**kwargs):
        calls.append(kwargs)
        return agent_expected, [{"role": "assistant", "content": "mock model boundary"}]
    monkeypatch.setattr(runtime, "run_evidence_reflector", agent)
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({"mode": "behavioral_hybrid_phase", "role": role,
        "fingerprint": runtime.phase_identity(cfg, role, payload), "payload": payload}))
    output = tmp_path / "output.json"
    assert worker.run_task(config_path=Path("unused"), manifest_path=manifest, output_path=output,
                           attempt_dir=tmp_path / "attempt") == 0
    assert json.loads(output.read_text())["agent_output"] == expected
    assert len(calls) == 1
    if role == "refiner":
        assert json.loads(calls[0]["counted_internal_playbook"]) == projected["draft"]
        assert "refine" in calls[0]["task"].lower()
    assert (tmp_path / "attempt/completion.json").exists()
    assert json.loads((tmp_path / "attempt/completion.json").read_text())["agent_output"] == agent_expected
    assert json.loads((tmp_path / "attempt/resolved_completion.json").read_text())["agent_output"] == expected
    evidence = tmp_path / "attempt/inputs"
    staged = json.loads((evidence / "manifest.json").read_text())
    assert not (evidence / "input.json").exists()
    assert "manifest.json" in calls[0]["instance_template"]
    assert all((evidence / path).is_file() for path in staged["files"])
    if role == "refiner":
        assert json.loads((evidence / "draft.json").read_text()) == projected["draft"]
    else:
        assert json.loads((evidence / "counted_playbook.json").read_text()) == projected["parent"]
        index = json.loads((evidence / ("record.json" if role == "reflector" else "records.json")).read_text())
        entry = index if role == "reflector" else index[0]
        restored = {key: json.loads((evidence / path).read_text()) for key, path in entry["fields"].items()}
        assert restored == (projected["record"] if role == "reflector" else projected["records"][0])
    assert (tmp_path / "attempt/reference_map.json").exists()


def test_batch_proposer_uses_wave_and_rejects_incomplete_reviews(tmp_path):
    records = [{"instance_id": f"case-{i}", "split": "train"} for i in range(3)]
    waves = []
    def batch(parent, rows):
        waves.append(rows)
        return [(review(parent, row), []) for row in rows]
    def serial(*_):
        pytest.fail("production wave must not fall back to serial Reflector")
    def curate(parent, reviews, rows, history, caps):
        assert len(reviews) == 3
        return {"reasoning": "Retain", "operations": [], "shared_guide": parent["shared_guide"]}, []
    proposer = IncrementalHybridACEProposer(serial, curate, LIMITS, batch_reflector=batch,
                                           ledger_path=tmp_path / "ledger.json")
    candidate = {"rules": json.dumps(PARENT)}
    proposer(candidate, {"rules": records}, ["rules"])
    assert len(waves) == 1
    before = (tmp_path / "ledger.json").read_bytes()
    proposer.batch_reflector = lambda parent, rows: []
    with pytest.raises(ValueError):
        proposer(candidate, {"rules": records}, ["rules"])
    assert (tmp_path / "ledger.json").read_bytes() == before


def test_curator_bad_reference_feedback_reaches_corrected_worker_retry(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(worker, "load_optimization_config", lambda *_: cfg)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    payload = {"parent": PARENT, "records": [{"instance_id": "long-canonical-id", "split": "train"}],
               "history": {}}
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({"mode": "behavioral_hybrid_phase", "role": "curator",
        "fingerprint": runtime.phase_identity(cfg, "curator", payload), "payload": payload}))
    calls = []
    def agent(**kwargs):
        calls.append(kwargs)
        return {"reasoning": "Revise grounded condition", "shared_guide": PARENT["shared_guide"],
                "operations": [{"type": "UPDATE", "target_id": "item-01",
                    "content": {"condition": "The Plan contradicts a material explicit requirement",
                                "guide": "Compare the proposed behavior against the task"},
                    "supporting_instance_ids": ["case-99" if len(calls) == 1 else "case-01"]}]}, []
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
    failed = tmp_path / "failed.json"
    assert worker.run_task(config_path=Path("unused"), manifest_path=manifest, output_path=failed,
                           attempt_dir=tmp_path / "attempt_01") == 1
    failure = json.loads(failed.read_text())
    assert failure["failure_stage"] == "agent_output_validation"
    assert "case-99" in failure["error"] and "case-01" in failure["error"]
    raw = (tmp_path / "attempt_01/completion.json").read_bytes()
    output = tmp_path / "completed.json"
    assert worker.run_task(config_path=Path("unused"), manifest_path=manifest, output_path=output,
                           attempt_dir=tmp_path / "attempt_02", previous_output_path=failed) == 0
    assert calls[1]["retry_feedback"].startswith(failure["error"])
    assert "Previous raw Agent output" in calls[1]["retry_feedback"]
    assert "case-99" in calls[1]["retry_feedback"]
    result = json.loads(output.read_text())["agent_output"]
    assert result["operations"][0]["supporting_instance_ids"] == ["long-canonical-id"]
    assert result["operations"][0]["target_id"] == "plan-00001"
    assert (tmp_path / "attempt_01/completion.json").read_bytes() == raw


def test_production_entrypoint_wires_batch_callback(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(runtime, "verify_hybrid_snapshot", lambda *_: None)
    captured = {}
    def run(config, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(runtime, "run_behavioral_optimization", run)
    runtime.run_hybrid_optimization(cfg)
    assert captured["proposer"].batch_reflector is not None
    assert captured["proposer"].abort_on_failure


def test_refiner_worker_rejects_invalid_full_artifact(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(worker, "load_optimization_config", lambda *_: cfg)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    monkeypatch.setattr(runtime, "run_evidence_curator", lambda **_: ({"shared_guide": "bad"}, []))
    payload = {"draft": PARENT, "limits": vars(LIMITS), "split": "train"}
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"mode": "behavioral_hybrid_phase", "role": "refiner",
        "fingerprint": runtime.phase_identity(cfg, "refiner", payload), "payload": payload}))
    output = tmp_path / "output.json"
    assert worker.run_task(config_path=Path("unused"), manifest_path=path, output_path=output,
                           attempt_dir=tmp_path / "attempt") == 1
    assert json.loads(output.read_text())["status"] == "agent_failed"
    failure = json.loads(output.read_text())
    assert failure["failure_stage"] == "agent_output_validation"
    assert failure["failure_kind"] == "agent_output_contract"
    assert failure["schema_version"] == 1
    assert (tmp_path / "attempt/completion.json").exists()
    assert (tmp_path / "attempt/failure.json").exists()


def test_refiner_size_retry_receives_raw_previous_output_and_all_counts(tmp_path, monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "token_counter", lambda **kw: len(kw["text"]))
    cfg = load_optimization_config("configs/gepa_swe_chat_hybrid_reference_token_smoke_v2_20261013.yaml",
                                   require_api_keys=False)
    monkeypatch.setattr(worker, "load_optimization_config", lambda *_: cfg)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    draft = {"schema_version": 1, "shared_guide": "Inspect the task", "checklist": [
        {"id": "plan-00006", "condition": "Check requirements", "guide": "a" * 140},
        {"id": "plan-00009", "condition": "Check compatibility", "guide": "b" * 150}]}
    payload = {"draft": draft, "limits": vars(runtime.hybrid_limits(cfg)), "split": "train"}
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({"mode": "behavioral_hybrid_phase", "role": "refiner",
        "fingerprint": runtime.phase_identity(cfg, "refiner", payload), "payload": payload}))
    calls = []
    def agent(**kwargs):
        calls.append(kwargs)
        return {"schema_version": 1, "shared_guide": "Inspect the task", "checklist": [
            {"id": "item-01", "condition": "Check requirements", "guide": "a" * (97 if len(calls) == 1 else 90)},
            {"id": "item-02", "condition": "Check compatibility", "guide": "b" * (98 if len(calls) == 1 else 90)}]}, []
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
    failed = tmp_path / "failed.json"
    assert worker.run_task(config_path=Path("unused"), manifest_path=manifest, output_path=failed,
                           attempt_dir=tmp_path / "attempt_01") == 1
    failure = json.loads(failed.read_text())
    assert failure["failure_kind"] == "agent_output_contract"
    assert [v["actual"] for v in failure["validation_violations"]] == [97, 98]
    assert [v["path"] for v in failure["validation_violations"]] == ["checklist[0].guide", "checklist[1].guide"]
    raw = (tmp_path / "attempt_01/completion.json").read_bytes()
    assert failure["raw_agent_output"] == json.loads(raw)["agent_output"]
    evidence = tmp_path / "attempt_01/inputs"
    limits = json.loads((evidence / "limits.json").read_text())
    assert limits == runtime.hybrid_limits(cfg).agent_view()
    assert not any("words" in key for key in limits)
    assert [v["actual"] for v in json.loads((evidence / "size_violations.json").read_text())] == [140, 150]
    completed = tmp_path / "completed.json"
    assert worker.run_task(config_path=Path("unused"), manifest_path=manifest, output_path=completed,
                           attempt_dir=tmp_path / "attempt_02", previous_output_path=failed) == 0
    feedback = calls[1]["retry_feedback"]
    assert "checklist[1].guide exceeds 96 tokens (received 98)" in feedback
    previous_json = feedback.split("Previous raw Agent output (rejected, not a valid artifact):\n")[1].split("\nCorrect")[0]
    assert json.loads(previous_json) == failure["raw_agent_output"]
    assert json.loads(completed.read_text())["agent_output"]["checklist"][0]["id"] == "plan-00006"
    assert (tmp_path / "attempt_01/completion.json").read_bytes() == raw


def test_generated_array_script_dispatches_exact_index_and_retry_paths(tmp_path):
    cfg = config(tmp_path)
    env_file = tmp_path / "private.env"
    env_file.write_text("")
    capture = tmp_path / "arguments.txt"
    boundary = tmp_path / "python-boundary"
    boundary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_CAPTURE"\n')
    boundary.chmod(0o700)
    cfg = replace(cfg, hpc=replace(cfg.hpc, remote_env_file=str(env_file), python_bin=str(boundary)))
    executor = runtime.HybridPhaseExecutor(cfg)
    batch = tmp_path / "wave"
    script = executor.script_for_wave(batch, "reflector", [2], 2)
    env = dict(os.environ, SLURM_ARRAY_TASK_ID="2", TEST_CAPTURE=str(capture))
    subprocess.run(["bash"], input="module() { :; }\n" + script, text=True, env=env, check=True)
    args = capture.read_text().splitlines()
    assert args[args.index("--manifest") + 1] == str(batch / "tasks/task_0002.json")
    assert args[args.index("--output") + 1] == str(batch / "outputs/task_0002.json")
    assert args[args.index("--attempt-dir") + 1] == str(batch / "attempts/task_0002/attempt_02")
    assert args[args.index("--previous-output") + 1] == str(batch / "failed_outputs/attempt_01/task_0002.json")


@pytest.mark.parametrize("wave", [True, False])
def test_submission_reconciliation_queries_actual_script_job_name(tmp_path, monkeypatch, wave):
    cfg = config(tmp_path)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    executor = runtime.HybridPhaseExecutor(cfg)
    def run(**kwargs):
        script = kwargs["write_script"]([task.index for task in kwargs["tasks"]], 1).read_text()
        assert f'#SBATCH --job-name={kwargs["job_name"](1)}\n' in script
        return [{"agent_output": {}, "trajectory": []} for _ in kwargs["tasks"]]
    monkeypatch.setattr(executor.runtime, "run", run)
    if wave:
        executor.reflect_batch(PARENT, [{"instance_id": "case", "split": "train"}])
    else:
        executor.call("curator", {"parent": PARENT, "records": [{"instance_id": "case", "split": "train"}]})


def test_exhausted_worker_failure_escapes_gepa_exception_boundary(tmp_path):
    from src.optimization.hpc.task_batch import TaskAttemptsExhausted
    def fail(*_):
        raise TaskAttemptsExhausted("Reflector exhausted bounded attempts")
    proposer = IncrementalHybridACEProposer(fail, fail, LIMITS, batch_reflector=fail,
        abort_on_failure=True, ledger_path=tmp_path / "ledger.json", trace_dir=tmp_path / "traces")
    with pytest.raises(OfflineReflectionBlocked) as raised:
        proposer({"rules": json.dumps(PARENT)}, {"rules": [{"instance_id": "case", "split": "train"}]}, ["rules"])
    assert isinstance(raised.value.cause, TaskAttemptsExhausted)
    assert not isinstance(raised.value, Exception)
    assert not (tmp_path / "ledger.json").exists()
    assert json.loads(next((tmp_path / "traces").glob("*.json")).read_text())["status"] == "failed"
    assert proposer.proposal_failures == 1
