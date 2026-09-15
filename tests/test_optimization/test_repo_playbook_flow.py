from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from src.exceptions import ControllerYield
from src.optimization import playbook_runtime, playbook_worker
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import SlurmTaskBatch
from src.optimization.models import GEPACase, RepositoryRef
from src.optimization.playbook import PlaybookBullet, RejectPlaybook
from src.optimization.playbook_adapter import RepoPlaybookGEPAAdapter
from src.optimization.playbook_cli import (
    _repo_image_records,
    _validate_frozen_inputs,
)
from src.optimization.playbook_hpc_agents import (
    HPCRepoPlaybookChecker,
    HPCRepoPlaybookProposalAgents,
    _image_authority,
)
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.repo_playbook import (
    render_concern_playbook,
    validate_repo_checker_result,
    validate_repo_reflector_review,
)


def _playbook() -> RejectPlaybook:
    return RejectPlaybook(
        (
            PlaybookBullet("plan-00001", "The Plan is a placeholder.", 3, 1),
            PlaybookBullet(
                "plan-00002",
                "The Plan may change shared behavior without accounting for consumers.",
                2,
                0,
            ),
        )
    )


def _case(*, instance_id: str = "repo__repo-1", resolved: bool = True) -> GEPACase:
    return GEPACase(
        instance_id=instance_id,
        split="train",
        resolved=resolved,
        issue_description="Preserve shared behavior while fixing the issue.",
        plan="Update the shared helper.",
        repository=RepositoryRef("repo/repo", "abc123", instance_id),
        asi={
            "plan_trajectory": [],
            "code_trajectory": [],
            "generated_patch": "diff",
            "evaluator_result": {"resolved": resolved},
        },
    )


def _image_records(instance_id: str = "repo__repo-1") -> dict[str, dict]:
    return {
        "swebench/sweb.eval.x86_64.repo_1776_repo-1:latest": {
            "instance_id": instance_id,
            "status": "audited",
            "base_commit_verified": True,
            "expected_base_commit": "abc123",
            "sif_path": "/cache/image.sif",
            "sif_sha256": "a" * 64,
            "sif_bytes": 123,
        }
    }


def _repo_output(*levels: int | None) -> dict:
    rows = []
    for index, level in enumerate(levels, start=1):
        triggered = level is not None
        rows.append(
            {
                "rule_number": index,
                "triggered": triggered,
                "level": level,
                "finding": f"Finding {index}" if triggered else None,
                "evidence": (
                    [
                        {
                            "source": "repository",
                            "location": f"src/file.py:{index}",
                            "observation": "The shared path is affected.",
                        }
                    ]
                    if triggered
                    else []
                ),
                "reason": "Evidence supports this result.",
            }
        )
    return {"rule_results": rows}


def test_repo_projection_is_separate_and_hides_internal_metadata() -> None:
    visible = render_concern_playbook(_playbook())
    assert visible.startswith("Review the Plan for these concerns:")
    assert "Concern 1. The Plan is a placeholder." in visible
    assert "plan-00001" not in visible
    assert "helpful" not in visible
    assert "harmful" not in visible
    assert _playbook().render_for_checker().startswith("Reject the plan when:")


def test_repo_checker_level_two_is_the_only_gate() -> None:
    playbook = _playbook()
    advisory = validate_repo_checker_result(_repo_output(0, 1), playbook)
    assert advisory.rejected is False
    assert advisory.to_dict()["advisory_rule_numbers"] == [2]
    assert advisory.to_dict()["blocking_rule_numbers"] == []

    blocking = validate_repo_checker_result(_repo_output(1, 2), playbook)
    assert blocking.rejected is True
    assert blocking.to_dict()["blocking_rule_numbers"] == [2]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("level", 3, "Level 0, 1, or 2"),
        ("finding", None, "requires a finding"),
        ("evidence", [], "requires evidence"),
        ("level", True, "Level 0, 1, or 2"),
    ],
)
def test_repo_checker_rejects_invalid_triggered_contract(
    field: str, value, message: str
) -> None:
    raw = _repo_output(1, None)
    raw["rule_results"][0][field] = value
    with pytest.raises(ValueError, match=message):
        validate_repo_checker_result(raw, _playbook())


def test_repo_checker_rejects_metadata_on_untriggered_rule() -> None:
    raw = _repo_output(None, None)
    raw["rule_results"][0]["level"] = 0
    with pytest.raises(ValueError, match="untriggered"):
        validate_repo_checker_result(raw, _playbook())


def test_repo_adapter_scores_only_the_level_two_gate() -> None:
    class Checker:
        def evaluate_batch(self, batch, playbook):
            assert len(batch) == 2
            assert playbook == _playbook()
            return [(_repo_output(0, 1), []), (_repo_output(None, 2), [])]

    table = {
        "accept_resolved": 1,
        "accept_unresolved": 0,
        "reject_resolved": -1,
        "reject_unresolved": 1,
    }
    second = replace(
        _case(),
        instance_id="repo__repo-2",
        resolved=False,
        repository=RepositoryRef("repo/repo", "abc123", "repo__repo-2"),
    )
    result = RepoPlaybookGEPAAdapter(
        Checker(), proposer=None, score_table=table
    ).evaluate([_case(), second], {"rules": _playbook().serialize()}, True)
    assert result.scores == [1.0, 1.0]
    assert [row["derived_decision"] for row in result.outputs] == [
        "ACCEPT",
        "REJECT",
    ]
    assert result.trajectories is not None
    assert result.trajectories[0]["repository"] == {
        "repo": "repo/repo",
        "base_commit": "abc123",
        "instance_id": "repo__repo-1",
    }


def test_repo_reflector_requires_recovery_and_calibration_per_bullet() -> None:
    review = {
        "instance_id": "repo__repo-1",
        "case_analysis": "The concern affected implementation.",
        "reusable_concerns": [],
        "uncertainty": "Resolved remains a proxy.",
        "bullet_tags": [
            {
                "id": bullet.id,
                "tag": "helpful" if index == 1 else "neutral",
                "attribution": "Case-specific evidence was reviewed.",
                "confidence": "high",
                "observed_recovery": "c2" if index == 1 else "unknown",
                "level_calibration": "appropriate" if index == 1 else "unknown",
            }
            for index, bullet in enumerate(_playbook().bullets, start=1)
        ],
    }
    parsed = validate_repo_reflector_review(
        review,
        instance_id="repo__repo-1",
        playbook=_playbook(),
    )
    assert parsed["bullet_tags"][0]["observed_recovery"] == "c2"
    del review["bullet_tags"][0]["level_calibration"]
    with pytest.raises(ValueError, match="bullet tag"):
        validate_repo_reflector_review(
            review,
            instance_id="repo__repo-1",
            playbook=_playbook(),
        )


def test_repo_reflector_accepts_empty_supported_analysis() -> None:
    review = {
        "instance_id": "repo__repo-1",
        "case_analysis": None,
        "reusable_concerns": [],
        "uncertainty": None,
        "bullet_tags": [
            {
                "id": bullet.id,
                "tag": "neutral",
                "attribution": None,
                "confidence": "low",
                "observed_recovery": "unknown",
                "level_calibration": "unknown",
            }
            for bullet in _playbook().bullets
        ],
    }
    assert validate_repo_reflector_review(
        review,
        instance_id="repo__repo-1",
        playbook=_playbook(),
    )["case_analysis"] is None


def test_repo_reflector_counter_tag_is_independent_of_level_calibration() -> None:
    review = {
        "instance_id": "repo__repo-1",
        "case_analysis": "Outcome evidence and Level calibration disagree.",
        "reusable_concerns": [],
        "uncertainty": None,
        "bullet_tags": [
            {
                "id": _playbook().bullets[0].id,
                "tag": "harmful",
                "attribution": "The concern did not match the observed failure.",
                "confidence": "high",
                "observed_recovery": "c1",
                "level_calibration": "appropriate",
            },
            {
                "id": _playbook().bullets[1].id,
                "tag": "helpful",
                "attribution": "The concern matched the observed failure.",
                "confidence": "high",
                "observed_recovery": "c3",
                "level_calibration": "too_high",
            },
        ],
    }
    parsed = validate_repo_reflector_review(
        review,
        instance_id="repo__repo-1",
        playbook=_playbook(),
    )
    assert parsed["bullet_tags"][0]["tag"] == "harmful"
    assert parsed["bullet_tags"][0]["level_calibration"] == "appropriate"
    assert parsed["bullet_tags"][1]["tag"] == "helpful"
    assert parsed["bullet_tags"][1]["level_calibration"] == "too_high"


def test_repo_image_authority_binds_instance_and_base_commit() -> None:
    authority = _image_authority(_case(), _image_records())
    assert authority["sif_sha256"] == "a" * 64
    drift = _image_records()
    next(iter(drift.values()))["expected_base_commit"] = "future"
    with pytest.raises(ValueError, match="base commit authority mismatch"):
        _image_authority(_case(), drift)
    mismatch = replace(
        _case(),
        repository=RepositoryRef("repo/repo", "abc123", "repo__other-1"),
    )
    with pytest.raises(ValueError, match="repository identity mismatch"):
        _image_authority(mismatch, _image_records())


def test_repo_hpc_checker_manifest_has_repo_but_no_outcome_or_counters(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_repo_concern_playbook\n", encoding="utf-8")
    hpc = HPCConfig(
        submit=True,
        worker_config_path=str(config),
        max_running_array_tasks=0,
        job_name_prefix="repo-playbook-smoke",
    )
    executor = PlaybookHPCExecutor(
        config_path=config,
        run_dir=tmp_path / "run",
        hpc=hpc,
    )
    submitted = []
    executor.runtime = SlurmTaskBatch(
        hpc,
        submitter=lambda path: submitted.append(path) or "123",
    )
    checker = HPCRepoPlaybookChecker(
        executor,
        image_records=_image_records(),
    )
    with pytest.raises(ControllerYield):
        checker.evaluate_batch([_case()], _playbook())
    manifest_path = next(
        (tmp_path / "run/hpc_tasks/repo_checker").glob("*/tasks/*.json")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["repository"]) == {"repo", "base_commit", "instance_id"}
    assert set(manifest["image_authority"]) == {
        "requested_ref",
        "sif_path",
        "sif_sha256",
        "sif_bytes",
    }
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "resolved" not in serialized
    assert "historical_evidence" not in serialized
    assert "helpful" not in serialized
    script = submitted[0].read_text(encoding="utf-8")
    assert "module load tools/Apptainer" in script
    assert "#SBATCH --array=0" in script


def test_repo_checker_imports_only_exact_validated_completed_checkpoint(
    tmp_path: Path,
) -> None:
    source_run = tmp_path / "source-run"
    source_manifest = source_run / "run_manifest.json"
    source_manifest.parent.mkdir(parents=True)
    source_manifest.write_text('{"semantic_sha256":"source"}\n', encoding="utf-8")
    source_manifest_sha = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
    source_batch = source_run / "hpc_tasks/repo_checker/source-batch"
    source_task_path = source_batch / "tasks/task_0000.json"
    source_output_path = source_batch / "outputs/task_0000.json"
    source_task_path.parent.mkdir(parents=True)
    source_output_path.parent.mkdir(parents=True)
    item = {
        "instance_id": "repo__repo-1",
        "validation_rule_count": 2,
        "repository": {
            "repo": "repo/repo",
            "base_commit": "abc123",
            "instance_id": "repo__repo-1",
        },
        "image_authority": {
            "requested_ref": "image",
            "sif_path": "/cache/image.sif",
            "sif_sha256": "a" * 64,
            "sif_bytes": 123,
        },
        "prompt_values": {
            "issue": "issue",
            "plan": "plan",
            "checker_visible_playbook": "playbook",
            "retry_feedback": "",
        },
    }
    source_task = {
        "schema_version": 1,
        "role": "repo_checker",
        "fingerprint": "source-fingerprint",
        "task_index": 0,
        **item,
    }
    source_task_path.write_text(json.dumps(source_task), encoding="utf-8")
    source_output = {
        "schema_version": 1,
        "status": "completed",
        "role": "repo_checker",
        "fingerprint": "source-fingerprint",
        "task_index": 0,
        "instance_id": "repo__repo-1",
        "agent_output": _repo_output(None, None),
        "trajectory": [{"role": "assistant", "content": "done"}],
    }
    source_output_path.write_text(json.dumps(source_output), encoding="utf-8")

    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_repo_concern_playbook\n", encoding="utf-8")
    hpc = HPCConfig(
        submit=True,
        worker_config_path=str(config),
        max_running_array_tasks=0,
    )
    executor = PlaybookHPCExecutor(
        config_path=config,
        run_dir=tmp_path / "target-run",
        hpc=hpc,
        checkpoint_import_run_dir=source_run,
        checkpoint_import_manifest_sha256=source_manifest_sha,
    )
    executor.runtime = SlurmTaskBatch(
        hpc,
        submitter=lambda _path: pytest.fail("imported checkpoint was resubmitted"),
    )

    outputs = executor.run_wave("repo_checker", [item])

    assert outputs[0]["agent_output"] == source_output["agent_output"]
    assert outputs[0]["trajectory"] == source_output["trajectory"]
    provenance = outputs[0]["checkpoint_import"]
    assert provenance["source_run_manifest_sha256"] == source_manifest_sha
    assert provenance["source_output_sha256"] == hashlib.sha256(
        source_output_path.read_bytes()
    ).hexdigest()
    audit_path = next(
        (tmp_path / "target-run/hpc_tasks/repo_checker").glob(
            "*/checkpoint_import.json"
        )
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert [row["instance_id"] for row in audit["imported"]] == ["repo__repo-1"]
    assert json.loads(source_output_path.read_text()) == source_output


def test_repo_reflection_mounts_repo_and_retrospective_evidence_separately(
    tmp_path: Path,
) -> None:
    captured = {}

    class Executor:
        run_dir = tmp_path / "run"

        def run_wave(self, role, items):
            captured["role"] = role
            captured["items"] = items
            return [{"agent_output": {"ok": True}}]

    agents = HPCRepoPlaybookProposalAgents(
        Executor(),  # type: ignore[arg-type]
        image_records=_image_records(),
        maximum_tokens=2048,
    )
    record = {
        "instance_id": "repo__repo-1",
        "issue": "issue",
        "plan": "plan",
        "ground_truth": "BAD",
        "resolved_proxy": False,
        "score": 0,
        "checker_output": _repo_output(1, 2),
        "checker_visible_playbook": render_concern_playbook(_playbook()),
        "internal_playbook": _playbook().serialize(),
        "repository": {
            "repo": "repo/repo",
            "base_commit": "abc123",
            "instance_id": "repo__repo-1",
        },
        "historical_evidence": {
            "plan_trajectory": [{"role": "assistant", "content": "plan"}],
            "code_trajectory": [{"role": "assistant", "content": "code"}],
            "generated_patch": "diff",
            "evaluator_result": {"resolved": False},
        },
    }
    assert agents.reflect_batch([record], rounds=1) == [{"ok": True}]
    assert captured["role"] == "repo_reflector"
    item = captured["items"][0]
    assert item["repository"] == record["repository"]
    assert item["source_access_issue"] == "issue"
    assert set(item["prompt_values"]) == {"internal_playbook", "evidence_path"}
    evidence = Path(item["evidence_dir"])
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["contains_repository"] is False
    assert manifest["contains_repository_reference"] is True
    classification = json.loads((evidence / "classification.json").read_text())
    assert classification["resolved_proxy"] is False
    assert classification["repository"] == record["repository"]


def test_repo_reflector_final_hpc_manifest_preserves_source_access_issue(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_repo_concern_playbook\n", encoding="utf-8")
    hpc = HPCConfig(
        submit=True,
        worker_config_path=str(config),
        max_running_array_tasks=0,
        job_name_prefix="repo-playbook-smoke",
    )
    executor = PlaybookHPCExecutor(
        config_path=config,
        run_dir=tmp_path / "run",
        hpc=hpc,
    )
    executor.runtime = SlurmTaskBatch(hpc, submitter=lambda path: "123")
    agents = HPCRepoPlaybookProposalAgents(
        executor,
        image_records=_image_records(),
        maximum_tokens=2048,
    )
    record = {
        "instance_id": "repo__repo-1",
        "issue": "Read https://example.com/issue for context.",
        "plan": "plan",
        "ground_truth": "BAD",
        "resolved_proxy": False,
        "score": 0,
        "checker_output": _repo_output(1, 2),
        "checker_visible_playbook": render_concern_playbook(_playbook()),
        "internal_playbook": _playbook().serialize(),
        "repository": {
            "repo": "repo/repo",
            "base_commit": "abc123",
            "instance_id": "repo__repo-1",
        },
        "historical_evidence": {
            "plan_trajectory": [{"role": "assistant", "content": "plan"}],
            "code_trajectory": [{"role": "assistant", "content": "code"}],
            "generated_patch": "diff",
            "evaluator_result": {"resolved": False},
        },
    }

    with pytest.raises(ControllerYield):
        agents.reflect_batch([record], rounds=1)

    manifest_path = next(
        (tmp_path / "run/hpc_tasks/repo_reflector").glob("*/tasks/task_0000.json")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_access_issue"] == record["issue"]
    assert "source_access_issue" not in manifest["prompt_values"]
    assert set(manifest["prompt_values"]) == {
        "internal_playbook",
        "evidence_path",
    }


def test_repo_reflector_hpc_manifest_requires_source_access_issue(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_repo_concern_playbook\n", encoding="utf-8")
    hpc = HPCConfig(submit=True, worker_config_path=str(config))
    executor = PlaybookHPCExecutor(
        config_path=config,
        run_dir=tmp_path / "run",
        hpc=hpc,
    )
    with pytest.raises(ValueError, match="non-empty source_access_issue"):
        executor.run_wave(
            "repo_reflector",
            [
                {
                    "instance_id": "repo__repo-1",
                    "prompt_values": {},
                    "validation_playbook": _playbook().serialize(),
                }
            ],
        )


def test_repo_worker_uses_separate_runtime_and_host_validation(
    tmp_path: Path, monkeypatch
) -> None:
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text(
        "checker_system: system\nchecker_instance: instance\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "inputs": {"prompt_bundle": str(prompts)},
                "models": {"checker": {"model": "fake"}},
                "container": {"sif_cache_dir": "/cache"},
                "repo_checker": {"workdir": "/testbed"},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "role": "repo_checker",
                "fingerprint": "abc",
                "task_index": 0,
                "instance_id": "repo__repo-1",
                "validation_rule_count": 2,
                "repository": {
                    "repo": "repo/repo",
                    "base_commit": "abc123",
                    "instance_id": "repo__repo-1",
                },
                "image_authority": {
                    "requested_ref": "image",
                    "sif_path": "/cache/image.sif",
                    "sif_sha256": "a" * 64,
                    "sif_bytes": 123,
                },
                "prompt_values": {
                    "issue": "issue",
                    "plan": "plan",
                    "checker_visible_playbook": "playbook",
                },
            }
        ),
        encoding="utf-8",
    )
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return _repo_output(1, 2), [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(playbook_worker, "run_repository_checker", fake_run)
    output = tmp_path / "output.json"
    attempt = tmp_path / "attempt"
    assert playbook_worker.run_task(
        config_path=config,
        manifest_path=manifest,
        output_path=output,
        attempt_dir=attempt,
    ) == 0
    assert seen["repository"]["base_commit"] == "abc123"
    assert seen["attempt_dir"] == attempt
    assert json.loads(output.read_text())["status"] == "completed"
    assert (attempt / "agent_completion.json").is_file()


def test_repository_runtime_uses_disposable_base_repo_and_artifact_channel(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    image_ref = "swebench/sweb.eval.x86_64.repo_1776_repo-1:latest"
    cache_path = playbook_runtime.ApptainerSifCache(
        cache,
        object(),  # type: ignore[arg-type]
    ).sif_path(image_ref)
    cache_path.write_bytes(b"sif")
    captured = {}

    class FakeEnvironment:
        def __init__(self, **kwargs):
            captured["environment"] = kwargs

        def execute(self, command, **kwargs):
            captured["artifact"] = (command, kwargs)
            return {
                "returncode": 0,
                "stdout": json.dumps(_repo_output(1, 2)),
                "stderr": "diagnostic",
            }

        def cleanup(self):
            captured["cleaned"] = True

    class FakeAgent:
        messages = [{"role": "assistant", "content": "submitted"}]

        def run(self, **kwargs):
            captured["agent_run"] = kwargs
            return "Submitted", "terminal diagnostics"

    monkeypatch.setenv("TEST_API_KEY", "not-a-secret")
    monkeypatch.setattr(
        playbook_runtime,
        "import_minisweagent",
        lambda: (object, object, None),
    )
    monkeypatch.setattr(playbook_runtime, "build_model", lambda *args: object())
    monkeypatch.setattr(playbook_runtime, "ApptainerEnvironment", FakeEnvironment)
    monkeypatch.setattr(
        playbook_runtime,
        "build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    monkeypatch.setattr(
        playbook_runtime,
        "raise_for_permanent_provider_error",
        lambda *args: None,
    )

    def fake_restore(env, base_commit, **kwargs):
        captured["restore"] = (env, base_commit, kwargs)
        return {"after": {"head": {"output": base_commit}}}

    monkeypatch.setattr(
        playbook_runtime,
        "restore_repository_to_base",
        fake_restore,
    )
    output, trajectory = playbook_runtime.run_repository_checker(
        model_config={"model": "fake", "api_key_env": "TEST_API_KEY"},
        repository_config={
            "sif_cache_dir": str(cache),
            "workdir": "/testbed",
            "command_timeout_seconds": 1800,
            "source_access_policy": "conservative_blacklist_v3",
            "prune_future_history": True,
        },
        system="system",
        instance_template="instance",
        repository={
            "repo": "repo/repo",
            "base_commit": "abc123",
            "instance_id": "repo__repo-1",
        },
        image_authority={
            "requested_ref": image_ref,
            "sif_path": str(cache_path),
            "sif_sha256": "a" * 64,
            "sif_bytes": 3,
        },
        attempt_dir=tmp_path / "attempt",
        issue="Read https://example.com/issue for context.",
        plan="plan",
        checker_visible_playbook="playbook",
    )
    assert output == _repo_output(1, 2)
    assert captured["environment"]["network_disabled"] is False
    assert captured["environment"]["isolate_tmp"] is True
    assert captured["environment"]["run_args"] == [
        "--containall",
        "--no-mount",
        "cwd",
    ]
    assert captured["environment"]["masked_container_paths"] == [
        "/opt/miniconda3/pkgs"
    ]
    assert captured["environment"]["source_access_prompt_urls"] == [
        "https://example.com/issue"
    ]
    assert captured["environment"]["source_access_log_path"] == (
        tmp_path / "attempt/source_access.jsonl"
    )
    assert captured["environment"]["source_access_context"] == {
        "instance_id": "repo__repo-1",
        "phase": "repo_checker",
    }
    assert captured["restore"][1] == "abc123"
    assert captured["restore"][2]["prune_future_history"] is True
    assert captured["artifact"] == (
        "cat /tmp/repo_checker.json",
        {"cwd": "/testbed", "timeout": 1800},
    )
    assert trajectory[-2]["role"] == "host_artifact_read"
    assert trajectory[-1]["role"] == "host_source_access_audit"
    assert captured["cleaned"] is True


def test_repo_mode_binds_image_manifest_hash(tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    prompt = tmp_path / "prompt.yaml"
    images = tmp_path / "images.json"
    seed.write_text("seed")
    prompt.write_text("prompt")
    images.write_text('{"records":{}}')
    raw = {
        "mode": "offline_repo_concern_playbook",
        "inputs": {
            "initial_playbook": str(seed),
            "initial_playbook_sha256": hashlib.sha256(seed.read_bytes()).hexdigest(),
            "prompt_bundle": str(prompt),
            "prompt_bundle_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "repo_checker_image_manifest": str(images),
            "repo_checker_image_manifest_sha256": hashlib.sha256(
                images.read_bytes()
            ).hexdigest(),
        },
    }
    _validate_frozen_inputs(tmp_path / "config.yaml", raw)
    raw["inputs"]["repo_checker_image_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="image manifest fingerprint mismatch"):
        _validate_frozen_inputs(tmp_path / "config.yaml", raw)


def test_repo_prompt_freezes_level_and_information_boundaries() -> None:
    path = Path(
        "configs/prompts/offline_gepa_repo_concern_playbook_v1_20260915.yaml"
    )
    prompts = yaml.safe_load(path.read_text(encoding="utf-8"))
    checker = prompts["checker_system"]
    reflector = prompts["reflector_system"]
    curator = prompts["curator_system"]
    checker_text = " ".join(checker.split())
    reflector_text = " ".join(reflector.split())
    curator_text = " ".join(curator.split())
    assert "available at `/testbed`" in checker
    assert "Level 0" in checker and "Level 1" in checker and "Level 2" in checker
    assert "rejects only" in checker
    assert "Do not report a concern absent from the playbook" in checker_text
    assert "high-level developer concern" in reflector_text
    assert "C1/C2/C3 describe a concern's observed recovery" in reflector_text
    assert "independent of Level calibration" in reflector_text
    assert "Calibration does not change helpful/harmful counters" in reflector_text
    assert "null case_analysis" in reflector_text
    assert "Do not store a default Level" in curator_text
    assert "over 64 tokens" in curator_text


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_repo_smoke_binds_distinct_mode_and_frozen_inputs(version: str) -> None:
    path = Path(
        f"configs/gepa_verified_repo_concern_playbook_smoke8_{version}_20260915.yaml"
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    _validate_frozen_inputs(path, raw)
    records = _repo_image_records(path, raw)
    assert raw["mode"] == "offline_repo_concern_playbook"
    assert raw["task"]["semantics"] == "repo_concern_playbook_v1"
    assert raw["container"]["runtime"] == "apptainer"
    assert raw["repo_checker"]["workdir"] == "/testbed"
    assert (
        raw["repo_checker"]["source_access_policy"]
        == "conservative_blacklist_v3"
    )
    assert raw["hpc"]["max_running_array_tasks"] == 0
    assert len(raw["inputs"]["train_instance_ids"]) == 8
    assert len(raw["inputs"]["validation_instance_ids"]) == 4
    assert len(records) == 482


def test_repaired_repo_smoke_has_canonical_import_and_fresh_identity() -> None:
    config_path = Path(
        "configs/gepa_verified_repo_concern_playbook_smoke8_v3_20260915.yaml"
    )
    supervisor_path = Path(
        "configs/gepa_verified_repo_concern_playbook_smoke8_v3_supervisor_20260915.yaml"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))
    assert config["status"] == "ready_not_launched"
    assert config["readiness"] == {
        "runnable": True,
        "launched": False,
        "missing": [],
    }
    assert config["run_id"].endswith("smoke8-v3-20260915")
    assert config["paths"]["run_dir"].endswith("smoke8-v3-20260915")
    assert config["checkpoint_import"] == {
        "source_run_dir": (
            "/scratch/users/twang/vibe-coding-planning/run_state/output/"
            "SWE-bench_Verified/gepa-repo-concern-playbook-runs/"
            "smoke8-v1-20260915"
        ),
        "source_run_manifest_sha256": (
            "e6995ef3602ebee0bedd0be0b3a56712c317f8966f7afdfa15bf115a2b85e2fd"
        ),
        "roles": ["repo_checker"],
    }
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(config_path)
    assert supervisor["session"] == config["run_id"]
