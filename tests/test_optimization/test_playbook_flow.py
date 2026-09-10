from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from src.optimization.models import GEPACase, RepositoryRef
from src.optimization.playbook import (
    BulletTokenLimitError,
    PlaybookBullet,
    RejectPlaybook,
    apply_curator_operations,
    manage_playbook_length,
    validate_bullet_token_limit,
    validate_checker_result,
)
from src.optimization.playbook_adapter import (
    ConfigurableRoundReflector,
    PlaybookGEPAAdapter,
    TwoStagePlaybookProposer,
)
from src.optimization.playbook_runner import run_playbook_search
from src.exceptions import ControllerYield
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskAttemptsExhausted
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.playbook_hpc_agents import HPCPlaybookChecker, HPCPlaybookProposalAgents
from src.optimization import playbook_worker
from src.optimization import playbook_runtime
from src.optimization.playbook_cli import (
    _optional_instance_ids,
    _token_counter,
    _validate_frozen_inputs,
)


def _playbook(*bullets: PlaybookBullet) -> RejectPlaybook:
    return RejectPlaybook(tuple(bullets))


def test_curator_bullet_limit_reports_ids_and_token_counts() -> None:
    playbook = _playbook(
        PlaybookBullet("plan-00001", "short rule"),
        PlaybookBullet("plan-00002", "one two three four five"),
    )
    with pytest.raises(
        BulletTokenLimitError,
        match=r"per-bullet token limit \(4\): plan-00002=5",
    ):
        validate_bullet_token_limit(
            playbook, token_counter=lambda text: len(text.split()),
            maximum_bullet_tokens=4,
        )


def test_v4_prompts_freeze_atomic_curation_and_duplicate_only_merge() -> None:
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_reject_playbook_v4_20260910.yaml"
    ).read_text(encoding="utf-8"))
    reflector = prompts["reflector_system"]
    curator = prompts["curator_system"]
    refiner = prompts["refiner_system"]
    assert "attributable to the Plan" in reflector
    assert "did not follow it or implemented it incorrectly" in reflector
    assert "Do not mechanically map each case" in curator
    assert "Create exactly one bullet for each distinct eligible failure mode" in curator
    assert "distinct failure modes must become separate bullets" in curator
    assert "equivalent findings from different cases" in curator
    assert "exactly one failure mode" in curator
    assert "no more than 32" in curator
    assert "over 64" in curator
    assert "Briefly record these self-check decisions" in curator
    assert "language-level duplicates" in curator
    assert "If either source bullet could trigger" in curator
    assert "language-level duplicates" in refiner
    assert "Leave distinct but related bullets separate" in refiner


def _case(*, resolved: bool) -> GEPACase:
    return GEPACase(
        instance_id="repo__repo-1",
        split="train",
        resolved=resolved,
        issue_description="Fix the behavior.",
        plan="Implement the fix.",
        repository=RepositoryRef("repo/repo", "abc", "repo__repo-1"),
        asi={
            "plan_trajectory": [],
            "code_trajectory": [],
            "generated_patch": "patch",
            "evaluator_result": {"resolved": resolved},
        },
    )


def _raw(*triggered: bool) -> dict:
    return {
        "rule_results": [
            {
                "rule_number": index,
                "triggered": value,
                "plan_evidence": ["quoted Plan text"] if value else [],
                "reason": "triggered" if value else "not triggered",
            }
            for index, value in enumerate(triggered, start=1)
        ]
    }


def test_checker_projection_hides_ids_and_counters_and_host_uses_or() -> None:
    playbook = _playbook(
        PlaybookBullet("plan-00001", "It is a placeholder.", 4, 2),
        PlaybookBullet("plan-00002", "It leaves a central choice open.", 1, 0),
    )
    visible = playbook.render_for_checker()
    assert "Rule 1. It is a placeholder." in visible
    assert "plan-00001" not in visible
    assert "helpful" not in visible
    assert validate_checker_result(_raw(False, True), playbook).rejected is True
    assert validate_checker_result(_raw(False, False), playbook).rejected is False


def test_playbook_config_rejects_frozen_input_fingerprint_drift(tmp_path) -> None:
    seed = tmp_path / "seed.json"
    prompt = tmp_path / "prompt.yaml"
    seed.write_text("seed")
    prompt.write_text("prompt")
    config = tmp_path / "config.yaml"
    raw = {
        "inputs": {
            "initial_playbook": str(seed),
            "initial_playbook_sha256": "0" * 64,
            "prompt_bundle": str(prompt),
            "prompt_bundle_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        }
    }
    with pytest.raises(ValueError, match="initial_playbook fingerprint mismatch"):
        _validate_frozen_inputs(config, raw)


def test_playbook_length_uses_model_tokens_not_whitespace_words() -> None:
    count = _token_counter("deepseek-v4-flash")
    text = "Reject the plan when: The Plan is a placeholder."
    assert count(text) > len(text.split())


def test_checker_contract_requires_every_rule_in_order() -> None:
    playbook = _playbook(PlaybookBullet("plan-00001", "It is a placeholder."))
    bad = _raw(False)
    bad["rule_results"][0]["rule_number"] = 2
    try:
        validate_checker_result(bad, playbook)
    except ValueError as exc:
        assert "ordinal" in str(exc)
    else:
        raise AssertionError("invalid ordinal was accepted")


def test_adapter_derives_cost_sensitive_scores_and_rejects_bad_checker_contract() -> None:
    playbook = _playbook(PlaybookBullet("plan-00001", "It is a placeholder."))

    def reject_checker(checker_input):
        assert set(checker_input) == {"issue", "plan", "checker_visible_playbook"}
        return _raw(True), []

    adapter = PlaybookGEPAAdapter(reject_checker, proposer=None)
    result = adapter.evaluate(
        [_case(resolved=True), replace(_case(resolved=False), instance_id="repo__repo-2")],
        {"rules": playbook.serialize()},
        capture_traces=True,
    )
    assert result.scores == [-5.0, 0.0]
    assert result.outputs[0]["derived_decision"] == "REJECT"

    def invalid_checker(checker_input):
        del checker_input
        return {"rule_results": []}, []

    with pytest.raises(ValueError, match="one result per playbook bullet"):
        PlaybookGEPAAdapter(invalid_checker, proposer=None).evaluate(
            [_case(resolved=False)], {"rules": playbook.serialize()}
        )


def test_adapter_does_not_convert_operational_failure_to_invalid() -> None:
    playbook = _playbook(PlaybookBullet("plan-00001", "It is a placeholder."))

    def failed_checker(checker_input):
        del checker_input
        raise TimeoutError("model timed out")

    with pytest.raises(TimeoutError, match="model timed out"):
        PlaybookGEPAAdapter(failed_checker, proposer=None).evaluate(
            [_case(resolved=False)], {"rules": playbook.serialize()}
        )


def test_overlength_bullet_is_invalid_without_calling_checker() -> None:
    playbook = _playbook(
        PlaybookBullet("plan-00001", "one two three four five")
    )

    def checker(_):
        raise AssertionError("invalid candidates must not call the Checker")

    result = PlaybookGEPAAdapter(
        checker,
        proposer=None,
        token_counter=lambda text: len(text.split()),
        maximum_bullet_tokens=4,
    ).evaluate(
        [_case(resolved=True), replace(_case(resolved=False), instance_id="case-2")],
        {"rules": playbook.serialize()},
        capture_traces=True,
    )

    assert result.scores == [-100.0, -100.0]
    assert all(item["derived_decision"] == "INVALID" for item in result.outputs)
    assert result.outputs[0]["invalid_bullet_ids"] == ["plan-00001"]
    assert result.trajectories is not None
    assert result.trajectories[0]["score"] == -100.0


def test_two_stage_proposer_attributes_then_counts_then_curates() -> None:
    parent = _playbook(PlaybookBullet("plan-00001", "It is a placeholder."))
    seen = {}

    def reflector(record):
        return {
            "instance_id": record["instance_id"],
            "reasoning": "The triggered rule matched the trajectory.",
            "error_identification": "The Plan had no strategy.",
            "root_cause_analysis": "The Plan was a placeholder.",
            "correct_approach": "Reject this Plan.",
            "key_insight": "Placeholders do not guide implementation.",
            "bullet_tags": [
                {
                    "id": "plan-00001",
                    "tag": "helpful",
                    "attribution": "The rule identified the observed failure.",
                    "confidence": "high",
                }
            ],
            "uncertainty": "Resolved remains a proxy.",
        }

    def curator(counted, reviews, records):
        seen["helpful"] = counted.bullets[0].helpful
        seen["reviews"] = len(reviews)
        seen["records"] = len(records)
        return {"reasoning": "No durable change.", "operations": []}

    proposer = TwoStagePlaybookProposer(
        reflector=reflector,
        curator=curator,
        token_counter=lambda text: len(text.split()),
    )
    proposal = proposer(
        {"rules": parent.serialize()},
        {"rules": [{"instance_id": "repo__repo-1"}]},
        ["rules"],
    )
    result = RejectPlaybook.parse(proposal["rules"])
    assert result.bullets[0].helpful == 1
    assert seen == {"helpful": 1, "reviews": 1, "records": 1}
    assert proposer.successful_proposals == 1


def test_curator_cannot_change_host_owned_counters() -> None:
    parent = _playbook(PlaybookBullet("plan-00001", "It is a placeholder."))

    def reflector(record):
        return {
            "instance_id": record["instance_id"],
            "reasoning": "Attribution.",
            "error_identification": "Error.",
            "root_cause_analysis": "Cause.",
            "correct_approach": "Approach.",
            "key_insight": "Insight.",
            "bullet_tags": [{
                "id": "plan-00001", "tag": "helpful",
                "attribution": "Supported.", "confidence": "high",
            }],
            "uncertainty": "Proxy label.",
        }

    def curator(counted, reviews, records):
        del counted, reviews, records
        return {
            "reasoning": "Try to target one bullet twice.",
            "operations": [
                {"type": "DELETE", "target_id": "plan-00001", "supporting_instance_ids": ["x"], "risk_analysis": "risk"},
                {"type": "DELETE", "target_id": "plan-00001", "supporting_instance_ids": ["x"], "risk_analysis": "risk"},
            ],
        }

    proposer = TwoStagePlaybookProposer(
        reflector=reflector,
        curator=curator,
        token_counter=lambda text: len(text.split()),
    )
    with pytest.raises(ValueError, match="more than once"):
        proposer(
            {"rules": parent.serialize()},
            {"rules": [{"instance_id": "repo__repo-1"}]},
            ["rules"],
        )


def test_length_manager_refines_then_deterministically_prunes() -> None:
    playbook = _playbook(
        PlaybookBullet("plan-00001", "low value long rule words", 0, 1),
        PlaybookBullet("plan-00002", "supported rule", 3, 0),
    )
    calls = []

    def refiner(value):
        calls.append(value)
        return value

    shortened, report = manage_playbook_length(
        playbook,
        token_counter=lambda text: len(text.split()),
        semantic_refiner=refiner,
        maximum_tokens=8,
    )
    assert calls == [playbook]
    assert [item.id for item in shortened.bullets] == ["plan-00002"]
    assert report["deterministically_removed_ids"] == ["plan-00001"]


def test_length_manager_does_not_call_refiner_at_ten_thousand_or_less() -> None:
    playbook = _playbook(PlaybookBullet("plan-00001", "short rule"))
    shortened, report = manage_playbook_length(
        playbook,
        token_counter=lambda _: 10_000,
        semantic_refiner=lambda _: (_ for _ in ()).throw(AssertionError()),
    )
    assert shortened == playbook
    assert report["semantic_refiner_ran"] is False


def test_runner_wires_negative_score_search_with_zero_perfect_score(
    tmp_path,
) -> None:
    playbook = _playbook(PlaybookBullet("plan-00001", "It is a placeholder."))
    playbook_path = tmp_path / "seed.json"
    playbook_path.write_text(playbook.serialize(), encoding="utf-8")
    snapshot = tmp_path / "dataset"
    snapshot.mkdir()

    def row(case):
        return {
            "instance_id": case.instance_id,
            "split": case.split,
            "resolved": case.resolved,
            "checker_input": case.checker_payload(),
            "asi": case.asi,
        }

    train_case = _case(resolved=True)
    validation_case = replace(
        train_case, instance_id="repo__repo-2", split="validation",
        repository=replace(train_case.repository, instance_id="repo__repo-2"),
    )
    (snapshot / "manifest.json").write_text(
        json.dumps({
            "complete": True, "provisional": False,
            "train_instances": 1, "validation_instances": 1,
        }), encoding="utf-8",
    )
    (snapshot / "train.jsonl").write_text(
        json.dumps(row(train_case)) + "\n", encoding="utf-8"
    )
    (snapshot / "validation.jsonl").write_text(
        json.dumps(row(validation_case)) + "\n", encoding="utf-8"
    )
    captured = {}

    def optimize_fn(**kwargs):
        captured.update(kwargs)
        return "result"

    adapter = PlaybookGEPAAdapter(lambda checker_input: (_raw(False), []), None)
    result = run_playbook_search(
        dataset_snapshot=snapshot,
        initial_playbook_path=playbook_path,
        run_dir=tmp_path / "run",
        adapter=adapter,
        max_metric_calls=12,
        max_iterations=None,
        seed=7,
        optimize_fn=optimize_fn,
    )
    assert result == "result"
    assert captured["perfect_score"] == 0.0
    assert captured["skip_perfect_score"] is True
    assert captured["max_metric_calls"] == 12
    assert RejectPlaybook.parse(captured["seed_candidate"]["rules"]) == playbook
    assert json.loads((tmp_path / "run/controller_status.json").read_text())["status"] == "completed"
    assert json.loads((tmp_path / "run/result.json").read_text())["run_status"] == "completed"


def test_curator_delta_operations_are_host_applied_with_new_ids() -> None:
    source = _playbook(
        PlaybookBullet("plan-00001", "old one", 2, 1),
        PlaybookBullet("plan-00002", "old two", 1, 0),
    )
    result = apply_curator_operations(source, {
        "reasoning": "Revise and add.",
        "operations": [
            {
                "type": "REVISE", "target_id": "plan-00001",
                "content": "revised one", "supporting_instance_ids": ["case-1"],
                "risk_analysis": "narrower",
            },
            {
                "type": "ADD", "content": "new three",
                "supporting_instance_ids": ["case-2"], "risk_analysis": "specific",
            },
        ],
    })
    assert [(x.id, x.text, x.helpful, x.harmful, x.lineage) for x in result.bullets] == [
        ("plan-00002", "old two", 1, 0, ()),
        ("plan-00003", "revised one", 0, 0, ("plan-00001",)),
        ("plan-00004", "new three", 0, 0, ()),
    ]


def test_reflection_round_count_is_configurable_and_passes_prior() -> None:
    seen = []

    def call(record, prior):
        seen.append(prior)
        return {"round": len(seen), "instance_id": record["instance_id"]}

    reflector = ConfigurableRoundReflector(call, rounds=3)
    assert reflector({"instance_id": "case-1"})["round"] == 3
    assert seen == [
        None,
        {"round": 1, "instance_id": "case-1"},
        {"round": 2, "instance_id": "case-1"},
    ]


def test_formal_playbook_config_can_select_the_complete_frozen_splits() -> None:
    inputs = {"train_instance_ids": None, "validation_instance_ids": None}
    assert _optional_instance_ids(inputs, "train_instance_ids") is None
    assert _optional_instance_ids(inputs, "validation_instance_ids") is None
    assert _optional_instance_ids({"train_instance_ids": ["case-1"]}, "train_instance_ids") == [
        "case-1"
    ]


def test_hpc_checker_wave_submits_one_array_and_hides_outcome(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_reject_playbook\n", encoding="utf-8")
    hpc = HPCConfig(
        submit=True, worker_config_path=str(config), max_running_array_tasks=0,
        job_name_prefix="playbook-smoke",
    )
    executor = PlaybookHPCExecutor(config_path=config, run_dir=tmp_path / "run", hpc=hpc)
    submitted = []
    executor.runtime = SlurmTaskBatch(hpc, submitter=lambda path: submitted.append(path) or "123")
    checker = HPCPlaybookChecker(executor)
    cases = [_case(resolved=True), replace(_case(resolved=False), instance_id="repo__repo-2")]
    with pytest.raises(ControllerYield):
        checker.evaluate_batch(cases, _playbook(PlaybookBullet("plan-00001", "placeholder")))
    assert len(submitted) == 1
    script = submitted[0].read_text(encoding="utf-8")
    assert "#SBATCH --array=0,1" in script
    assert "#SBATCH --array=0,1%" not in script
    manifests = sorted((tmp_path / "run/hpc_tasks/checker").glob("*/tasks/*.json"))
    assert len(manifests) == 2
    serialized = "\n".join(path.read_text() for path in manifests)
    assert "resolved" not in serialized
    assert "historical_evidence" not in serialized
    assert "helpful" not in serialized and "harmful" not in serialized


def test_playbook_worker_writes_atomic_agent_evidence(tmp_path, monkeypatch) -> None:
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text("checker_system: system\nchecker_instance: '{{ issue }} {{ plan }} {{ checker_visible_playbook }}'\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"inputs:\n  prompt_bundle: {prompts}\nmodels:\n  checker:\n    model: fake\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({
        "role": "checker", "fingerprint": "abc", "task_index": 0,
        "instance_id": "case", "prompt_values": {
            "issue": "issue", "plan": "plan", "checker_visible_playbook": "rules",
            "retry_feedback": "",
        }, "validation_rule_count": 1,
    }))
    class FakeModel:
        def __init__(self, _config): pass
        def __call__(self, system, user):
            return _raw(False), [{"role": "user", "content": user}]
    monkeypatch.setattr(playbook_worker, "PromptModel", FakeModel)
    output = tmp_path / "output.json"
    assert playbook_worker.run_task(
        config_path=config, manifest_path=manifest, output_path=output,
        attempt_dir=tmp_path / "attempt",
    ) == 0
    value = json.loads(output.read_text())
    assert value["status"] == "completed"
    assert value["fingerprint"] == "abc"
    assert value["trajectory"]
    raw = json.loads((tmp_path / "attempt/agent_completion.json").read_text())
    assert raw["status"] == "agent_completed"


def test_playbook_worker_checkpoints_invalid_agent_output_before_failure(
    tmp_path, monkeypatch
) -> None:
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text(
        "checker_system: system\n"
        "checker_instance: '{{ issue }} {{ plan }} {{ checker_visible_playbook }}'\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"inputs:\n  prompt_bundle: {prompts}\nmodels:\n  checker:\n    model: fake\n",
        encoding="utf-8",
    )
    playbook = _playbook(PlaybookBullet("plan-00001", "placeholder"))
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({
        "role": "checker", "fingerprint": "abc", "task_index": 0,
        "instance_id": "case", "validation_rule_count": 1,
        "prompt_values": {
            "issue": "issue", "plan": "plan",
            "checker_visible_playbook": playbook.render_for_checker(),
            "retry_feedback": "",
        },
    }))

    class FakeModel:
        def __init__(self, _config): pass
        def __call__(self, system, user):
            return {"rule_results": []}, [{"role": "assistant", "content": "raw"}]

    monkeypatch.setattr(playbook_worker, "PromptModel", FakeModel)
    output = tmp_path / "output.json"
    attempt = tmp_path / "attempt"
    assert playbook_worker.run_task(
        config_path=config, manifest_path=manifest, output_path=output,
        attempt_dir=attempt,
    ) == 1
    assert json.loads((attempt / "agent_completion.json").read_text())["status"] == "agent_completed"
    failure = json.loads(output.read_text())
    assert failure["status"] == "agent_failed"
    assert failure["failure_kind"] == "agent_output_contract"


def test_playbook_worker_gives_host_validation_feedback_to_fresh_retry(
    tmp_path, monkeypatch
) -> None:
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text(
        "checker_system: system\n"
        "checker_instance: >-\n"
        "  {{ issue }} {{ plan }} {{ checker_visible_playbook }}"
        " {% if retry_feedback %}HOST: {{ retry_feedback }}{% endif %}\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"inputs:\n  prompt_bundle: {prompts}\nmodels:\n  checker:\n    model: fake\n",
        encoding="utf-8",
    )
    playbook = _playbook(PlaybookBullet("plan-00001", "placeholder"))
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({
        "role": "checker", "fingerprint": "abc", "task_index": 0,
        "instance_id": "case", "validation_rule_count": 1,
        "prompt_values": {
            "issue": "issue", "plan": "plan",
            "checker_visible_playbook": playbook.render_for_checker(),
            "retry_feedback": "",
        },
    }))
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"error": "plan_evidence must be an array"}))
    seen = {}

    class FakeModel:
        def __init__(self, _config): pass
        def __call__(self, system, user):
            seen["user"] = user
            return _raw(False), []

    monkeypatch.setattr(playbook_worker, "PromptModel", FakeModel)
    assert playbook_worker.run_task(
        config_path=config, manifest_path=manifest,
        output_path=tmp_path / "output.json", attempt_dir=tmp_path / "attempt",
        previous_output_path=previous,
    ) == 0
    assert "HOST: plan_evidence must be an array" in seen["user"]


def test_curator_worker_retries_overlength_completion_with_host_feedback(
    tmp_path, monkeypatch
) -> None:
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text(
        "curator_system: system\n"
        "curator_instance: '{{ counted_internal_playbook }} {{ case_reflections }}'\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"inputs:\n  prompt_bundle: {prompts}\n"
        "models:\n  checker:\n    model: fake\n  curator:\n    model: fake\n"
        "length:\n  maximum_bullet_tokens: 4\n",
        encoding="utf-8",
    )
    playbook = _playbook(PlaybookBullet("plan-00001", "placeholder"))
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({
        "role": "curator", "fingerprint": "abc", "task_index": 0,
        "instance_id": None, "validation_playbook": playbook.serialize(),
        "prompt_values": {
            "counted_internal_playbook": playbook.serialize(),
            "case_reflections": "[]",
        },
    }))
    responses = iter([
        {"reasoning": "long", "operations": [{
            "type": "ADD", "content": "one two three four five",
            "supporting_instance_ids": ["case"], "risk_analysis": "risk",
        }]},
        {"reasoning": "short", "operations": [{
            "type": "ADD", "content": "one two three",
            "supporting_instance_ids": ["case"], "risk_analysis": "risk",
        }]},
    ])
    users = []

    class FakeModel:
        def __init__(self, _config): pass
        def __call__(self, system, user):
            users.append(user)
            return next(responses), []

    monkeypatch.setattr(playbook_worker, "PromptModel", FakeModel)
    monkeypatch.setattr(
        playbook_worker.litellm, "token_counter",
        lambda *, model, text: len(text.split()),
    )
    first_output = tmp_path / "first.json"
    assert playbook_worker.run_task(
        config_path=config, manifest_path=manifest, output_path=first_output,
        attempt_dir=tmp_path / "attempt_01",
    ) == 1
    first = json.loads(first_output.read_text())
    assert first["error_type"] == "BulletTokenLimitError"
    assert (tmp_path / "attempt_01/agent_completion.json").is_file()

    assert playbook_worker.run_task(
        config_path=config, manifest_path=manifest,
        output_path=tmp_path / "second.json", attempt_dir=tmp_path / "attempt_02",
        previous_output_path=first_output,
    ) == 0
    assert "plan-00002=5" in users[1]
    assert "Return a complete corrected Curator JSON object" in users[1]


def test_evidence_reflector_disables_implicit_cwd_mount(
    tmp_path, monkeypatch
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    captured = {}

    class FakeEnvironment:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, command, **kwargs):
            captured["artifact_command"] = (command, kwargs)
            return {
                "output": '{"instance_id":"case","bullet_tags":[]}WARNING',
                "stdout": '{"instance_id":"case","bullet_tags":[]}',
                "stderr": "WARNING",
                "returncode": 0,
            }

        def cleanup(self):
            captured["cleaned"] = True

    class FakeAgent:
        messages = [{"role": "assistant", "content": "submitted"}]

        def run(self, **kwargs):
            captured["task"] = kwargs
            return "Submitted", '{"instance_id":"case"}WARNING'

    monkeypatch.setenv("TEST_API_KEY", "not-a-secret")
    monkeypatch.setattr(
        playbook_runtime,
        "import_minisweagent",
        lambda: (object, object, None),
    )
    monkeypatch.setattr(playbook_runtime, "build_model", lambda *args: object())
    monkeypatch.setattr(playbook_runtime, "ApptainerEnvironment", FakeEnvironment)
    def fake_build_default_agent(*args, **kwargs):
        captured["agent_build"] = kwargs
        return FakeAgent()

    monkeypatch.setattr(
        playbook_runtime,
        "build_default_agent",
        fake_build_default_agent,
    )
    monkeypatch.setattr(
        playbook_runtime,
        "raise_for_permanent_provider_error",
        lambda *args: None,
    )

    output, _ = playbook_runtime.run_evidence_reflector(
        model_config={"model": "fake", "api_key_env": "TEST_API_KEY"},
        reflection_config={
            "evidence_sif_cache_dir": str(tmp_path / "cache"),
        },
        system="system",
        instance_template="instance",
        evidence_dir=str(evidence),
        internal_playbook="playbook",
        retry_feedback="previous JSON was malformed",
    )

    assert output["instance_id"] == "case"
    assert captured["run_args"] == [
        "--no-mount",
        "cwd",
        "--pwd",
        "/evidence",
        "--bind",
        f"{evidence.resolve()}:/evidence:ro",
    ]
    assert captured["network_disabled"] is True
    assert captured["isolate_tmp"] is True
    assert captured["cwd"] == "/evidence"
    assert captured["timeout"] == 1800
    assert captured["agent_build"]["step_limit"] == 0
    assert captured["task"]["retry_feedback"] == "previous JSON was malformed"
    assert captured["artifact_command"] == (
        "cat /tmp/reflection.json",
        {"cwd": "/evidence", "timeout": 1800},
    )
    assert captured["cleaned"] is True


def test_hpc_proposal_agents_use_file_backed_reflector_waves_and_singletons(
    tmp_path,
) -> None:
    calls = []
    playbook = _playbook(PlaybookBullet("plan-00001", "placeholder"))
    class Executor:
        run_dir = tmp_path / "run"

        def run_wave(self, role, items):
            calls.append((role, len(items), items))
            if role == "reflector":
                return [{"agent_output": {"round": len([c for c in calls if c[0] == "reflector"])}}
                        for _ in items]
            if role == "curator":
                return [{"agent_output": {"reasoning": "no change", "operations": []}}]
            return [{"agent_output": {"reasoning": "shorten", "operations": []}}]
    agents = HPCPlaybookProposalAgents(Executor(), maximum_tokens=10_000)
    records = [{"instance_id": f"case-{i}", "internal_playbook": playbook.serialize()}
               for i in range(3)]
    reviews = agents.reflect_batch(records, rounds=3)
    assert [call[:2] for call in calls[:3]] == [
        ("reflector", 3), ("reflector", 3), ("reflector", 3),
    ]
    assert all(item["round"] == 3 for item in reviews)
    assert "reflection_case_bundle" not in calls[0][2][0]["prompt_values"]
    assert calls[0][2][0]["prompt_values"]["evidence_path"] == "/evidence"
    second_evidence = calls[1][2][0]["evidence_dir"]
    assert (Path(second_evidence) / "prior_reflection.json").is_file()
    manifest = json.loads((Path(second_evidence) / "manifest.json").read_text())
    assert manifest["contains_repository"] is False
    assert set(manifest["files"]) == {
        "classification.json", "plan_trajectory.json", "code_trajectory.json",
        "evaluator_result.json", "generated.patch", "prior_reflection.json",
    }
    third_evidence = calls[2][2][0]["evidence_dir"]
    assert json.loads(
        (Path(third_evidence) / "prior_reflection.json").read_text()
    )["round"] == 2
    assert agents.curate(playbook, reviews)["operations"] == []
    assert agents.refine(playbook) == playbook
    assert [call[:2] for call in calls[-2:]] == [
        ("curator", 1), ("refiner", 1)
    ]


def test_exhausted_curator_length_retries_return_invalid_candidate(tmp_path) -> None:
    counted = _playbook(PlaybookBullet("plan-00001", "placeholder"))
    output = {"reasoning": "still long", "operations": [{
        "type": "ADD", "content": "one two three four five",
        "supporting_instance_ids": ["case"], "risk_analysis": "risk",
    }]}

    class Executor:
        run_dir = tmp_path / "run"

        def batch_dir_for(self, role, items):
            return self.run_dir / "hpc_tasks" / role / "fingerprint"

        def run_wave(self, role, items):
            batch = self.batch_dir_for(role, items)
            completion = batch / "attempts/task_0000/attempt_03/agent_completion.json"
            completion.parent.mkdir(parents=True)
            completion.write_text(json.dumps({"agent_output": output}))
            raise TaskAttemptsExhausted("three attempts")

    agents = HPCPlaybookProposalAgents(
        Executor(), maximum_tokens=10_000, maximum_bullet_tokens=4,
        token_counter=lambda text: len(text.split()),
    )
    recovered = agents.curate(counted, [])
    proposed = apply_curator_operations(counted, recovered)
    assert proposed.bullets[-1].text == "one two three four five"


def test_runner_marks_reflection_failure_as_operationally_incomplete(
    tmp_path,
) -> None:
    playbook = _playbook(PlaybookBullet("plan-00001", "placeholder"))
    playbook_path = tmp_path / "seed.json"
    playbook_path.write_text(playbook.serialize())
    snapshot = tmp_path / "dataset"
    snapshot.mkdir()
    case = _case(resolved=True)
    row = {
        "instance_id": case.instance_id, "split": case.split,
        "resolved": case.resolved, "checker_input": case.checker_payload(),
        "asi": case.asi,
    }
    (snapshot / "manifest.json").write_text(json.dumps({
        "complete": True, "provisional": False,
        "train_instances": 1, "validation_instances": 1,
    }))
    (snapshot / "train.jsonl").write_text(json.dumps(row) + "\n")
    validation = {
        **row,
        "instance_id": "repo__repo-2",
        "split": "validation",
        "checker_input": {
            **row["checker_input"],
            "repository": {
                **row["checker_input"]["repository"],
                "instance_id": "repo__repo-2",
            },
        },
    }
    (snapshot / "validation.jsonl").write_text(json.dumps(validation) + "\n")

    class Proposer:
        failures = [{"error_type": "TaskAttemptsExhausted", "error": "reflector failed"}]
        successful_proposals = 0

    adapter = PlaybookGEPAAdapter(lambda _: (_raw(False), []), Proposer())
    with pytest.raises(RuntimeError, match="operationally incomplete"):
        run_playbook_search(
            dataset_snapshot=snapshot, initial_playbook_path=playbook_path,
            run_dir=tmp_path / "run", adapter=adapter, max_metric_calls=2,
            max_iterations=1, seed=1,
            optimize_fn=lambda **_: object(),
            abort_on_operational_incomplete=True,
        )
    status = json.loads((tmp_path / "run/controller_status.json").read_text())
    assert status["status"] == "failed"


def test_fresh_formal_30it_contract_uses_atomic_seed_and_v5_prompts() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_path = (
        repo_root
        / "configs/gepa_verified_reject_playbook_formal_30it_v2_20260910.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    _validate_frozen_inputs(config_path, raw)
    seed = RejectPlaybook.parse(
        (repo_root / raw["inputs"]["initial_playbook"]).read_text(encoding="utf-8")
    )
    assert len(seed.bullets) == 1
    assert seed.bullets[0].text == "The Plan is a placeholder."
    assert seed.bullets[0].helpful == seed.bullets[0].harmful == 0

    prompts = yaml.safe_load(
        (repo_root / raw["inputs"]["prompt_bundle"]).read_text(encoding="utf-8")
    )
    reflector = " ".join(prompts["reflector_system"].split())
    curator = " ".join(prompts["curator_system"].split())
    assert "could reasonably have been discovered during planning" in reflector
    assert "The historical Planner need not actually have" in reflector
    assert "must not depend on facts that became available only after" in reflector
    assert "create at most one bullet" in curator
    assert "Create exactly one bullet for each" not in curator
    assert "must not require a post-implementation" in curator

    assert raw["run_id"] == "verified-reject-playbook-formal-30it-v2-20260910"
    assert raw["search"]["max_iterations"] == 30
    assert raw["search"]["reflection_minibatch_size"] == 8
    assert raw["reflection"]["rounds"] == 3
    assert raw["length"]["maximum_bullet_tokens"] == 64
    assert raw["hpc"]["max_task_attempts"] == 3
    assert raw["readiness"] == {
        "runnable": True,
        "launched": False,
        "missing": [],
    }

    supervisor = yaml.safe_load(
        (
            repo_root
            / "configs/gepa_verified_reject_playbook_formal_30it_supervisor_v2_20260910.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--target-iterations") + 1] == "30"
    assert arguments[arguments.index("--gepa-config") + 1] == str(
        config_path.relative_to(repo_root)
    )
    assert "formal-30it-v2-20260910" in arguments[
        arguments.index("--remote-dir") + 1
    ]


def test_replacement_formal_contract_delegates_reflector_limit_to_slurm() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_path = (
        repo_root
        / "configs/gepa_verified_reject_playbook_formal_30it_v3_20260910.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    _validate_frozen_inputs(config_path, raw)
    assert "max_steps" not in raw["reflection"]
    assert raw["reflection"]["command_timeout_seconds"] == 1800
    assert raw["length"]["maximum_visible_tokens"] == 2048
    assert raw["hpc"]["agent_time"] == "00:35:00"
    assert raw["hpc"]["max_running_array_tasks"] == 0
    assert raw["paths"]["run_dir"].endswith("formal-30it-v3-20260910")
    assert "resume" not in raw

    supervisor = yaml.safe_load(
        (
            repo_root
            / "configs/gepa_verified_reject_playbook_formal_30it_supervisor_v3_20260910.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(
        config_path.relative_to(repo_root)
    )
    assert "formal-30it-v3-20260910" in arguments[
        arguments.index("--remote-dir") + 1
    ]
