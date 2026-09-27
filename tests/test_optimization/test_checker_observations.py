"""Checker transport regressions; no containers, API calls or inference."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from src.agents._deps import DEFAULT_ACTION_PROTOCOL, DEFAULT_FORMAT_ERROR_TEMPLATE, build_default_agent
from src.optimization import playbook_worker
from src.optimization.playbook_runtime import (
    CheckerActionRecorder,
    PlaybookAgentOutputContractError,
    validate_checker_observations,
)


def result(source="repository"):
    return {"rule_results": [{
        "rule_number": 1, "triggered": True, "finding": "Mismatch",
        "evidence": [{"source": source, "location": "file.py", "observation": "Actual fact"}],
    }]}


def journal(actions):
    return [{"role": "host_checker_action_observations", "content": actions}]


class Environment:
    def __init__(self):
        self.calls = []

    def get_template_vars(self):
        return {}

    def execute(self, command, **kwargs):
        self.calls.append(command)
        output = ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n{}" if command == "submit"
                  else "ACTUAL_REPOSITORY_CONTENT")
        return {"output": output, "returncode": 0}


@pytest.mark.parametrize("inspect", [True, False])
def test_real_parser_recovery_counts_only_executed_actions(inspect):
    from minisweagent.agents.default import DefaultAgent

    invalid = "```bash\nread_one\n```\n```bash\nread_two\n```\nConfirmed."
    responses = [invalid] + (["```bash\nread_real\n```"] if inspect else []) + ["```bash\nsubmit\n```"]

    class Model:
        n_calls = 0
        cost = 0

        def get_template_vars(self):
            return {}

        def query(self, messages):
            if self.n_calls == 1:
                assert "rejected before execution" in messages[-1]["content"]
                assert "None of its text or\ncommands ran" in messages[-1]["content"]
            self.n_calls += 1
            return {"content": responses[self.n_calls - 1]}

    env = Environment()
    recorder = CheckerActionRecorder(env)
    agent = build_default_agent(
        DefaultAgent, Model(), recorder, system_template="Review", instance_template="{{task}}",
        step_limit=0,
    )
    status, _ = agent.run(task="Task")
    assert status == "Submitted"
    assert env.calls == (["read_real", "submit"] if inspect else ["submit"])
    assert recorder.actions[-1]["terminal_submission"] is True
    output = result()
    before = copy.deepcopy(output)
    if inspect:
        validate_checker_observations(output, journal(recorder.actions))
    else:
        with pytest.raises(PlaybookAgentOutputContractError, match="no successful tool observation"):
            validate_checker_observations(output, journal(recorder.actions))
    assert output == before


@pytest.mark.parametrize("source", ["plan", "issue"])
def test_direct_issue_or_plan_judgment_needs_no_repository_action(source):
    validate_checker_observations(result(source), journal([]))


@pytest.mark.parametrize("action", [
    {"returncode": 1, "has_output": True, "terminal_submission": False},
    {"returncode": 0, "has_output": False, "terminal_submission": False},
    {"returncode": 0, "has_output": True, "terminal_submission": True},
])
def test_failed_empty_or_terminal_command_is_not_repository_observation(action):
    with pytest.raises(PlaybookAgentOutputContractError):
        validate_checker_observations(result(), journal([action]))


def test_host_setup_and_assistant_claims_are_not_agent_observations():
    trace = [
        {"role": "host_repository_baseline", "content": "file.py read"},
        {"role": "assistant", "content": "Confirmed after sed file.py"},
        *journal([]),
    ]
    with pytest.raises(PlaybookAgentOutputContractError):
        validate_checker_observations(result(), trace)


def test_worker_checkpoints_raw_completion_before_rejection_and_retries(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text("checker_system: review\nchecker_instance: task\n")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "inputs": {"prompt_bundle": str(prompts)},
        "models": {"checker": {"model": "fake"}},
        "container": {"sif_cache_dir": "/cache"},
        "repo_checker": {"output_contract": "binary_v2", "observation_contract": "executed_tools_v1"},
    }))
    manifest = tmp_path / "task.json"
    manifest.write_text(json.dumps({
        "role": "paired_repo_checker", "fingerprint": "fixed", "task_index": 0,
        "output_contract": "binary_v2", "instance_id": "blind-observation",
        "validation_rule_count": 1, "repository": {}, "image_authority": {},
        "prompt_values": {"issue": "Task", "plan": "Plan", "checker_visible_playbook": "Rule"},
    }))
    original = result()
    received_feedback = []

    def fake_run(**kwargs):
        received_feedback.append(kwargs["retry_feedback"])
        actions = [] if not kwargs["retry_feedback"] else [
            {"returncode": 0, "has_output": True, "terminal_submission": False}
        ]
        return original, journal(actions)

    monkeypatch.setattr(playbook_worker, "run_repository_checker", fake_run)
    output = tmp_path / "output.json"
    attempt = tmp_path / "attempt_01"
    assert playbook_worker.run_task(config_path=config, manifest_path=manifest, output_path=output, attempt_dir=attempt) == 1
    raw = json.loads((attempt / "agent_completion.json").read_text())
    assert raw["agent_output"] == original
    failure = json.loads(output.read_text())
    assert failure["failure_stage"] == "agent_output_validation"
    assert "rule_results[0] (rule 1)" in failure["error"]
    retry = tmp_path / "retry.json"
    assert playbook_worker.run_task(config_path=config, manifest_path=manifest, output_path=retry,
                                   attempt_dir=tmp_path / "attempt_02", previous_output_path=output) == 0
    assert "no successful tool observation" in received_feedback[-1]
    assert json.loads(retry.read_text())["agent_output"] == original


def test_prompt_changes_only_checker_transport_not_reflection_or_curation():
    root = Path("configs/prompts")
    old = yaml.safe_load((root / "offline_gepa_paired_binary_ace_codex_v9_pair_prediction_20260927.yaml").read_text())
    new = yaml.safe_load((root / "offline_gepa_paired_binary_ace_codex_v10_interactive_review_20260927.yaml").read_text())
    for key in old:
        if key not in {"prompt_id", "checker_system", "checker_instance"}:
            assert new[key] == old[key]
    assert new["checker_system"] == old["checker_system"]
    assert "## Interaction" not in new["checker_system"]
    assert "reply with one bash block" not in new["checker_instance"]
    assert "Proposed or rejected commands provide no" in DEFAULT_ACTION_PROTOCOL
    assert "wait for the environment's" in DEFAULT_ACTION_PROTOCOL
    assert "Before sending, check that your entire reply" in DEFAULT_ACTION_PROTOCOL


def test_checker_receives_one_centralized_protocol_and_shared_recovery():
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v10_interactive_review_20260927.yaml"
    ).read_text())

    class CaptureAgent:
        def __init__(self, model, environment, **kwargs):
            self.kwargs = kwargs

    agent = build_default_agent(CaptureAgent, None, None,
                                system_template=prompts["checker_system"],
                                instance_template=prompts["checker_instance"], step_limit=0)
    assert agent.kwargs["system_template"].count("## Mini-swe action protocol") == 1
    assert agent.kwargs["system_template"].endswith(DEFAULT_ACTION_PROTOCOL)
    assert agent.kwargs["format_error_template"] == DEFAULT_FORMAT_ERROR_TEMPLATE
    assert "wait for the environment" not in prompts["checker_instance"]


def test_checker_replay_config_reuses_completed_proposal_without_new_learning():
    from src.optimization.playbook_cli import _validate_frozen_inputs
    from src.optimization.pending_playbook_resume import file_hash

    config_path = Path("configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v4_checker_replay1_20260927.yaml")
    new = yaml.safe_load(config_path.read_text())
    old = yaml.safe_load(Path("configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v4_20260927.yaml").read_text())
    authority = json.loads(Path("configs/recovery/blocking_it9_v4_completed_checker_replay1_20260927.json").read_text())
    _validate_frozen_inputs(config_path.resolve(), new)
    assert new["paths"] == old["paths"]
    assert new["models"]["reflector"] == old["models"]["reflector"]
    assert new["models"]["curator"] == old["models"]["curator"]
    assert new["models"]["checker"] == {
        "executor": "mini_swe", "model": "deepseek-flash",
        "thinking": "enabled", "reasoning_effort": "high",
    }
    assert new["search"] == old["search"]
    assert new["scoring"] == old["scoring"]
    assert new["repo_checker"]["observation_contract"] == "executed_tools_v1"
    assert "checkpoint_import" not in new  # Never import the old candidate screen.
    assert authority["subsample_ids"] == new["search"]["frozen_minibatch_ids"]
    assert authority["proposed_rule_count"] == 38
    assert authority["replacement_runtime_config"]["replacement_sha256"] == file_hash(config_path)
    assert authority["replacement_prompt_bundle"]["replacement_sha256"] == file_hash(Path(new["inputs"]["prompt_bundle"]))
    for name, sha in authority["execution_support_hashes"].items():
        # This stopped replay is a historical authority, not the current
        # execution configuration. Check its frozen source revision rather
        # than requiring future protocol fixes to have the old file hashes.
        import hashlib
        import subprocess
        original = subprocess.run(
            ["git", "show", f"b8eba8f51a67d38e5a1f221fe638cfd420e3b8cf:{name}"],
            check=True, capture_output=True,
        ).stdout
        assert hashlib.sha256(original).hexdigest() == sha
    assert new["hpc"]["cpus_per_task"] == 1 and new["hpc"]["mem"] == "4G"
    assert new["hpc"]["max_running_array_tasks"] == 12
