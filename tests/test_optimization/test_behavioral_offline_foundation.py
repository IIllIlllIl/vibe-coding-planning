"""No-LLM tests for the Behavioral Offline information boundary."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from src.optimization.behavioral_adapter import BehavioralGEPAAdapter
from src.optimization.behavioral_dataset import load_behavioral_snapshot
from src.optimization.behavioral_hpc_executor import (
    HPCSlurmBehavioralCheckerExecutor,
    build_behavioral_checker_array_script,
)
from src.optimization.behavioral_models import BehavioralCheckerOutput
from src.optimization.behavioral_repository import materialize_repository_proxy
from src.optimization.behavioral_runner import run_behavioral_optimization
from src.optimization.behavioral_runtime import (
    behavioral_shell_command_timeout,
    render_pre_p1_context,
    validate_behavioral_checker_output,
    validate_behavioral_reflection_analysis,
)
from src.optimization.config import load_optimization_config
from src.optimization.offline_hpc_reflection import HPCOfflineReflectionProposer
from src.optimization.reflection import EvidenceBundleWriter


def _record(instance_id: str, split: str, decision: str) -> dict:
    return {
        "instance_id": instance_id,
        "split": split,
        "task_semantics": "behavioral_plan_acceptability_v1",
        "checker_input": {
            "pre_p1_context": [
                {"turn_type": "user_prompt", "content": "fix the parser"},
                {
                    "turn_type": "tool_result",
                    "content": "observed content before P1",
                },
            ],
            "proposed_plan_p1": "Inspect the parser and add a regression test.",
            "repository_proxy": {
                "repo": "org/repo",
                "proxy_commit": "a" * 40,
                "instance_id": instance_id,
                "state_semantics": "approximate_pre_session_proxy",
                "conflict_authority": "pre_p1_observed_tool_results",
            },
        },
        "supervision": {
            "decision": decision,
            "confidence": "high",
            "signal": (
                "explicit_approval" if decision == "ACCEPT" else "explicit_rejection"
            ),
        },
        "reflection_evidence": {
            "decision_result": {"content": "post-boundary approval result"},
            "subsequent_events": [{"content": "developer reaction"}],
            "later_plan_count": 1,
        },
        "audit_provenance": {
            "mirror_relpath": "org/repo.git",
            "proxy_source": "recorded_branch",
            "time_gap_seconds": 12,
            "raw_case_sha256": "b" * 64,
        },
    }


def _snapshot(root: Path) -> Path:
    root.mkdir()
    train = [_record("train-case", "train", "ACCEPT")]
    validation = [_record("validation-case", "validation", "DO_NOT_ACCEPT")]
    for name, rows in (("train.jsonl", train), ("validation.jsonl", validation)):
        (root / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "task_semantics": "behavioral_plan_acceptability_v1",
                "train_instances": 1,
                "validation_instances": 1,
            }
        ),
        encoding="utf-8",
    )
    return root


def _balanced_snapshot(root: Path) -> Path:
    root.mkdir()
    for split in ("train", "validation"):
        rows = [
            _record(
                f"{split}-case-{index}",
                split,
                "ACCEPT" if index % 2 == 0 else "DO_NOT_ACCEPT",
            )
            for index in range(4)
        ]
        (root / f"{split}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "task_semantics": "behavioral_plan_acceptability_v1",
                "train_instances": 4,
                "validation_instances": 4,
            }
        ),
        encoding="utf-8",
    )
    return root


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_behavioral_snapshot_projects_only_pre_boundary_information(tmp_path):
    train, validation = load_behavioral_snapshot(_snapshot(tmp_path / "snapshot"))

    case = train[0]
    payload = case.checker_payload()
    serialized = json.dumps(payload, sort_keys=True)
    worker = json.dumps(case.worker_payload("/operational/mirrors"), sort_keys=True)

    assert set(payload) == {
        "pre_p1_context",
        "proposed_plan_p1",
        "repository_proxy",
    }
    assert "developer reaction" not in serialized
    assert "post-boundary approval result" not in serialized
    assert "ACCEPT" not in serialized
    assert "raw_case_sha256" not in serialized
    assert "ACCEPT" not in worker
    assert "reflection_evidence" not in worker
    assert len(validation) == 1


def test_behavioral_context_rendering_preserves_complete_original_order() -> None:
    events = [
        {"turn_number": 0, "turn_type": "user_prompt", "content": "first"},
        {"turn_number": 1, "turn_type": "tool_use", "content": "second"},
        {"turn_number": 2, "turn_type": "tool_result", "content": "third"},
    ]

    rendered = render_pre_p1_context(events)

    assert json.loads(rendered) == events
    assert rendered.index("first") < rendered.index("second") < rendered.index("third")


def test_behavioral_checker_output_contract_is_exact() -> None:
    output = validate_behavioral_checker_output(
        {
            "predicted_accept": False,
            "decision_reason": "The plan lacks decision-time support.",
            "repository_evidence": [
                {"path": "src/a.py", "symbol": "parse", "finding": "Observed"}
            ],
        }
    )
    assert output.predicted_accept is False
    assert output.decision_reason == "The plan lacks decision-time support."

    with pytest.raises(ValueError, match="unexpected or missing keys"):
        validate_behavioral_checker_output(
            {
                "predicted_accept": False,
                "decision_reason": "reason",
                "repository_evidence": [],
                "observed_accept": False,
            }
        )


def test_behavioral_shell_commands_use_the_short_environment_timeout() -> None:
    config = load_optimization_config(
        Path("configs/gepa_behavioral_acceptability_smoke_v1_20260830.yaml")
    )

    assert config.checker.timeout == 1800
    assert config.reflection.timeout == 1800
    assert behavioral_shell_command_timeout(config) == 30


def test_behavioral_snapshot_rejects_checker_boundary_leakage(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    leaking = _record("leak", "train", "ACCEPT")
    leaking["checker_input"]["developer_reaction"] = "approved"
    (snapshot / "train.jsonl").write_text(json.dumps(leaking) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checker_input boundary"):
        load_behavioral_snapshot(snapshot)


def test_behavioral_adapter_scores_behavior_and_reflects_later_evidence(tmp_path):
    train, _ = load_behavioral_snapshot(_snapshot(tmp_path / "snapshot"))
    observed: list[dict] = []

    def checker(case, guideline):
        observed.append(case.checker_payload())
        assert guideline == "neutral guideline"
        return BehavioralCheckerOutput(
            predicted_accept=True,
            decision_reason="behavioral decision",
            repository_evidence=(),
        )

    result = BehavioralGEPAAdapter(checker).evaluate(
        train,
        {"rules": "neutral guideline"},
        capture_traces=True,
    )

    assert result.scores == [1.0]
    assert result.outputs[0]["predicted_accept"] is True
    assert "predicted_resolved" not in result.outputs[0]
    assert result.trajectories[0]["observed_decision"] == "ACCEPT"
    assert result.trajectories[0]["observed_accept"] is True
    assert "expected_decision" not in result.trajectories[0]
    assert "expected_accept" not in result.trajectories[0]
    assert "developer reaction" in json.dumps(result.trajectories[0])
    assert "developer reaction" not in json.dumps(observed[0])
    assert "mirror_relpath" not in result.trajectories[0]["repository_proxy_provenance"]


def test_behavioral_reflection_bundle_has_controlled_post_boundary_evidence(
    tmp_path,
):
    record = {
        "instance_id": "case-one",
        "observed_decision": "DO_NOT_ACCEPT",
        "observed_accept": False,
        "score": 0.0,
        "checker_output": {"predicted_accept": True, "trajectory": []},
        "reflection_evidence": {"developer_reaction": "revise P1"},
        "repository_proxy_provenance": {"time_gap_seconds": 12},
    }

    bundle = EvidenceBundleWriter(tmp_path, mode="behavioral_acceptability").write(
        [record]
    )

    case_dir = bundle / "case-one"
    assert (
        json.loads(
            (case_dir / "behavioral_supervision.json").read_text(encoding="utf-8")
        )["observed_decision"]
        == "DO_NOT_ACCEPT"
    )
    supervision = json.loads(
        (case_dir / "behavioral_supervision.json").read_text(encoding="utf-8")
    )
    assert supervision["observed_accept"] is False
    assert "expected_decision" not in supervision
    assert "expected_accept" not in supervision
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cases"][0]["observed_decision"] == "DO_NOT_ACCEPT"
    assert "revise P1" in (case_dir / "post_boundary_evidence.json").read_text(
        encoding="utf-8"
    )
    assert not (case_dir / "generated.patch").exists()


def test_temporal_proxy_materialization_is_detached_clean_and_disposable(tmp_path):
    source = tmp_path / "source"
    mirror = tmp_path / "mirror.git"
    workspaces = tmp_path / "workspaces"
    _run("git", "init", str(source))
    (source / "tracked.txt").write_text("before session\n", encoding="utf-8")
    _run("git", "add", "tracked.txt", cwd=source)
    _run(
        "git",
        "-c",
        "user.name=Behavioral Test",
        "-c",
        "user.email=behavioral@example.invalid",
        "commit",
        "-m",
        "base",
        cwd=source,
    )
    commit = _run("git", "rev-parse", "HEAD", cwd=source)
    _run("git", "clone", "--mirror", str(source), str(mirror))

    with materialize_repository_proxy(
        mirror_path=mirror,
        proxy_commit=commit,
        workspace_root=workspaces,
    ) as checkout:
        assert _run("git", "rev-parse", "HEAD", cwd=checkout) == commit
        assert _run("git", "status", "--porcelain", cwd=checkout) == ""
        assert _run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=checkout) == "HEAD"
        temporary_parent = checkout.parent

    assert not temporary_parent.exists()
    assert _run("git", "rev-parse", "HEAD", cwd=source) == commit


def test_behavioral_seed_is_exactly_neutral() -> None:
    seed = (
        Path(__file__).parents[2]
        / "configs"
        / "gepa_behavioral_acceptability_neutral_seed.md"
    )
    assert seed.read_text(encoding="utf-8").strip() == (
        "Evaluate whether the proposed plan should be accepted for implementation "
        "based on the information available at the time of the decision."
    )


def test_behavioral_smoke_contract_freezes_prompts_and_budgets() -> None:
    path = (
        Path(__file__).parents[2]
        / "configs"
        / "gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = load_optimization_config(path, require_api_keys=False)

    assert raw["smoke_contract"]["status"] == "stage_c_launchable"
    assert raw["smoke_contract"]["launch_authorized"] is True
    assert raw["smoke_contract"]["fixture"] == {
        "total_cases": 8,
        "train_cases": 4,
        "validation_cases": 4,
        "cases_per_split_by_decision": {
            "ACCEPT": 2,
            "DO_NOT_ACCEPT": 2,
        },
        "cases_per_split_by_proxy_source": {
            "recorded_branch": 2,
            "all_reachable_refs": 2,
        },
        "no_session_repository_or_dedup_group_overlap_between_splits": True,
        "development_exposed_cases_may_enter_formal_train_only": True,
    }
    assert config.task.semantics == "behavioral_plan_acceptability_v1"
    assert config.checker.model == config.reflection.model == "deepseek-v4-flash"
    assert config.checker.temperature == 0.0
    assert config.reflection.temperature == 0.7
    assert config.search.max_iterations == 1
    assert config.search.reflection_minibatch_size == 4
    assert config.search.train_case_repetitions == 1
    assert config.search.projection_metric_calls == 16
    assert config.search.max_metric_calls == 20
    assert config.search.primary_metric == "accuracy"
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:35:00"
    assert config.hpc.max_task_attempts == 3
    assert config.container.runtime == "none"


def test_behavioral_smoke_prompts_preserve_information_responsibilities() -> None:
    path = (
        Path(__file__).parents[2]
        / "configs"
        / "gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    config = load_optimization_config(path, require_api_keys=False)
    checker = config.checker_prompt
    checker_instance = config.checker_instance_template
    reflection = config.reflection_prompt
    reflection_instance = config.reflection_instance_template

    assert "sole source of plan-review methods" in checker
    assert "predicted_accept" in checker
    assert "predicted_resolved" not in checker
    assert checker.count("approximate pre-session proxy") == 1
    assert (
        "When it conflicts with repository observations recorded in the "
        "pre-decision session context, treat the recorded observation as "
        "authoritative."
    ) in " ".join(checker.split())
    assert "proxy age" not in checker.lower()
    assert "fallback" not in checker.lower()
    assert "{{pre_p1_context}}" in checker_instance
    assert "{{proposed_plan_p1}}" in checker_instance
    assert "{{repository_proxy_commit}}" in checker_instance
    assert "{{candidate_guideline}}" in checker_instance

    combined_reflection = reflection + reflection_instance
    assert "observed developer decision" in reflection
    assert "Behavioral evidence attribution" in reflection
    assert "observed_decision" in reflection_instance
    assert "observed_accept" in reflection_instance
    assert "expected_decision" not in combined_reflection
    assert "expected_accept" not in combined_reflection
    assert "expected_behavior_change" not in combined_reflection
    assert "intended_behavior_change" in reflection
    assert "post-boundary facts as decision-time evidence" in reflection
    assert "Do not cat" in reflection
    assert "one case and one field at a time" in " ".join(reflection.split())
    assert "predicted_resolved" not in combined_reflection


def test_behavioral_formal_8it_contract_is_frozen_but_not_launch_authorized() -> None:
    root = Path(__file__).parents[2]
    config_path = (
        root / "configs/gepa_behavioral_acceptability_formal_8it_v1_20260830.yaml"
    )
    supervisor_path = (
        root / "configs/behavioral_gepa_formal_8it_supervisor_v1_20260830.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    smoke = yaml.safe_load(
        (
            root / "configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml"
        ).read_text(encoding="utf-8")
    )
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))
    config = load_optimization_config(config_path, require_api_keys=False)

    contract = raw["experiment_contract"]
    assert contract["status"] == "superseded_before_launch"
    assert contract["launch_authorized"] is False
    assert contract["dataset"] == {
        "split_manifest_content_sha256": (
            "c7d423dbbd8965ec534e7a37d3c993f78bb0167708f8946ed092f8285dc54b94"
        ),
        "train_cases": 84,
        "validation_cases": 47,
        "train_repositories": 8,
        "validation_repositories": 29,
        "repository_or_duplicate_component_overlap": 0,
        "development_exposed_cases_in_train_only": True,
        "validation_is_candidate_selection_data_not_untouched_holdout": True,
    }
    assert config.search.max_iterations == 8
    assert config.search.reflection_minibatch_size == 8
    assert config.search.projection_metric_calls == 551
    assert config.search.max_metric_calls == 700
    assert config.search.primary_metric == "accuracy"
    assert config.search.train_case_repetitions == 1
    assert config.container.runtime == "none"
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:35:00"
    assert raw["prompts"] == smoke["prompts"]
    assert raw["checker"] == smoke["checker"]
    assert raw["reflection"] == smoke["reflection"]
    assert "--submit" not in supervisor["arguments"]


def test_behavioral_formal_v2_launch_contract_has_no_prompt_drift() -> None:
    root = Path(__file__).parents[2]
    config_path = (
        root / "configs/gepa_behavioral_acceptability_formal_8it_v2_20260830.yaml"
    )
    supervisor_path = (
        root / "configs/behavioral_gepa_formal_8it_supervisor_v2_20260830.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    smoke = yaml.safe_load(
        (
            root / "configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml"
        ).read_text(encoding="utf-8")
    )
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))
    config = load_optimization_config(config_path, require_api_keys=False)

    contract = raw["experiment_contract"]
    assert contract["status"] == "launch_authorized"
    assert contract["launch_authorized"] is True
    assert contract["dataset"]["snapshot_manifest_content_sha256"] == (
        "ff18d5dacd5cd9e0d7dba9ead504cd4a8a16a9e03f3c771606b5b5e516e3e21c"
    )
    assert contract["dataset"]["checker_media_projection"] == (
        "omit-base64-media-preserve-descriptor-v1"
    )
    assert contract["dataset"]["development_fixture_cases_in_train_only"] is True
    assert contract["dataset"]["validation_operational_preflight"] == {
        "case_id": "cf4618ba-7f51-4a75-bef9-5fa32a9f003b#first-plan",
        "purpose": "unlabeled_context_and_runtime_smoke_only",
        "observed_decision_exposed_to_checker": False,
        "post_boundary_evidence_exposed_to_checker": False,
        "reflection_or_gepa_optimization_used": False,
        "prompt_or_seed_changed_from_result": False,
    }
    assert "media-projected" in str(config.dataset_snapshot)
    assert config.search.max_iterations == 8
    assert config.search.reflection_minibatch_size == 8
    assert config.search.projection_metric_calls == 551
    assert raw["prompts"] == smoke["prompts"]
    assert raw["checker"] == smoke["checker"]
    assert raw["reflection"] == smoke["reflection"]
    assert "--submit" in supervisor["arguments"]


def test_behavioral_reflection_analysis_requires_new_vocabulary_and_all_cases() -> None:
    review = {
        "instance_id": "case-1",
        "classification_outcome": "correct_accept",
        "decision_time_evidence": "The plan covers the stated request.",
        "current_guideline_effect": "The guideline preserved scope checking.",
        "checker_behavior": "The Checker accepted.",
        "behavioral_evidence_attribution": "The matched approval supports the label.",
        "diagnosis": "Preserve this behavior.",
        "proposed_guideline_effect": "No change for this case.",
        "risk_to_correct_cases": "None identified.",
        "evidence_used": ["checker_output.json", "behavioral_supervision.json"],
    }
    change = {
        "operation": "preserve_scope_check",
        "description": "Preserve explicit scope checking.",
        "causal_rationale": "It supported the observed decision.",
        "intended_behavior_change": "None for already correct cases.",
        "risk_to_correct_cases": "None identified.",
        "supporting_instance_ids": ["case-1"],
    }
    validate_behavioral_reflection_analysis(
        {"case_reviews": [review], "guideline_changes": [change]}, ["case-1"]
    )

    legacy_change = dict(change)
    legacy_change["expected_behavior_change"] = legacy_change.pop(
        "intended_behavior_change"
    )
    with pytest.raises(ValueError, match="guideline change"):
        validate_behavioral_reflection_analysis(
            {"case_reviews": [review], "guideline_changes": [legacy_change]},
            ["case-1"],
        )
    with pytest.raises(ValueError, match="every case exactly once"):
        validate_behavioral_reflection_analysis(
            {"case_reviews": [review], "guideline_changes": [change]},
            ["case-1", "case-2"],
        )


def test_behavioral_hpc_manifests_exclude_supervision_and_post_boundary(
    tmp_path,
) -> None:
    train, _ = load_behavioral_snapshot(_snapshot(tmp_path / "snapshot"))
    config_path = (
        Path(__file__).parents[2]
        / "configs/gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    config = load_optimization_config(config_path, require_api_keys=False)
    config = replace(config, run_dir=tmp_path / "run")
    executor = HPCSlurmBehavioralCheckerExecutor(config)
    batch_dir = tmp_path / "batch"
    tasks = executor._prepare(
        batch_dir,
        fingerprint="f" * 64,
        batch=train,
        rules="neutral",
        capture_traces=True,
    )

    manifest = json.loads(tasks[0].manifest_path.read_text(encoding="utf-8"))
    serialized = json.dumps(manifest, sort_keys=True)
    assert manifest["mode"] == "behavioral_checker"
    assert "observed_decision" not in serialized
    assert "observed_accept" not in serialized
    assert "reflection_evidence" not in serialized
    assert "developer reaction" not in serialized
    assert "score" not in serialized
    batch_manifest = json.loads(
        (batch_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert batch_manifest["contains_observed_decision"] is False
    assert batch_manifest["contains_post_boundary_evidence"] is False


def test_behavioral_hpc_scripts_use_no_container_and_one_worker_per_task(
    tmp_path,
) -> None:
    config_path = (
        Path(__file__).parents[2]
        / "configs/gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    config = replace(
        load_optimization_config(config_path, require_api_keys=False),
        run_dir=tmp_path / "run",
    )
    checker = build_behavioral_checker_array_script(
        config=config,
        batch_dir=tmp_path / "checker",
        task_indices=[0, 1, 2, 3],
        attempt=1,
    )
    reflection = HPCOfflineReflectionProposer(config)._script(
        tmp_path / "reflection", 1
    )

    for script in (checker, reflection):
        assert "#SBATCH --cpus-per-task=1" in script
        assert "#SBATCH --mem=4G" in script
        assert "#SBATCH --time=00:35:00" in script
        assert "Apptainer" not in script
        assert "module load" not in script
        assert "%" not in next(
            line for line in script.splitlines() if line.startswith("#SBATCH --array")
        )
    assert "src.optimization.behavioral_checker_worker" in checker
    assert "src.optimization.behavioral_reflection_worker" in reflection


def test_behavioral_runner_completes_one_real_gepa_iteration_without_llm(
    tmp_path,
) -> None:
    config_path = (
        Path(__file__).parents[2]
        / "configs/gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    config = load_optimization_config(config_path, require_api_keys=False)
    config = replace(
        config,
        dataset_snapshot=_balanced_snapshot(tmp_path / "snapshot"),
        run_dir=tmp_path / "run",
        execution=replace(config.execution, backend="local"),
    )

    def checker(case, _guideline):
        return BehavioralCheckerOutput(
            predicted_accept=case.accepted,
            decision_reason="deterministic no-LLM fixture decision",
            repository_evidence=(),
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components_to_update):
            assert components_to_update == ["rules"]
            assert len(reflective_dataset["rules"]) == 4
            self.successful_proposals += 1
            return {"rules": candidate["rules"] + "\nPreserve decision-time scope."}

    result = run_behavioral_optimization(
        config,
        checker=checker,
        proposer=Proposer(),
    )

    assert result is not None
    assert (config.run_dir / "behavioral_candidate_metrics.json").is_file()
    assert (
        json.loads(
            (config.run_dir / "controller_status.json").read_text(encoding="utf-8")
        )["status"]
        == "completed"
    )
