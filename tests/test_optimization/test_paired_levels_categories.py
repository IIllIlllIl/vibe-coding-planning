import json
from pathlib import Path

import pytest
import yaml

from src.environment.apptainer_env import ApptainerEnvironment
from src.optimization.paired_playbook import validate_paired_checker_result, paired_checker_uses_levels
from src.optimization.playbook import (PlaybookBullet, RejectPlaybook, apply_curator_operations,
    apply_refiner_operations, manage_playbook_length, validate_curator_concern_coverage)
from src.optimization.playbook_adapter import GlobalPlaybookCounters
from src.optimization.playbook_cli import run_from_config, _validate_frozen_inputs
from src.optimization.hpc.config import HPCConfig
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.repo_playbook import render_concern_playbook


def book():
    return RejectPlaybook((PlaybookBullet("plan-00001", "Concern A", 3, 1, category="Scope"),))


def result(level):
    return {"rule_results": [{"rule_number": 1, "level": level,
        "finding": "Finding" if level else None,
        "evidence": [{"source": "plan", "location": None, "observation": "Evidence"}] if level else [],
        "reason": "Reason"}]}


@pytest.mark.parametrize("level,reject", [(0, False), (1, False), (2, True)])
def test_level_gate(level, reject):
    parsed = validate_paired_checker_result(result(level), book(), levels=True)
    assert parsed.rejected is reject
    assert parsed.to_dict()["rule_results"][0]["level"] == level
    assert parsed.to_dict()["warning_rule_numbers"] == ([1] if level == 1 else [])


@pytest.mark.parametrize("level", [True, False, None, "2", -1, 3, 1.0])
def test_invalid_level_rejected(level):
    with pytest.raises(ValueError):
        validate_paired_checker_result(result(level), book(), levels=True)


def test_output_protocols_do_not_mix():
    with pytest.raises(ValueError):
        validate_paired_checker_result(result(2), book())
    old = result(2)
    old["rule_results"][0].pop("level")
    old["rule_results"][0]["triggered"] = True
    with pytest.raises(ValueError):
        validate_paired_checker_result(old, book(), levels=True)
    assert validate_paired_checker_result(old, book()).rejected
    assert not paired_checker_uses_levels({})
    with pytest.raises(ValueError):
        paired_checker_uses_levels({"repo_checker": {"output_contract": "typo"}})


def test_warning_requires_evidence_and_zero_can_record_repairable_finding():
    warning = result(1)
    warning["rule_results"][0]["evidence"] = []
    with pytest.raises(ValueError):
        validate_paired_checker_result(warning, book(), levels=True)
    zero = result(0)
    zero["rule_results"][0]["finding"] = "Minor Plan concern"
    zero["rule_results"][0]["evidence"] = [
        {"source": "plan", "location": None, "observation": "The Plan misspells a path."}
    ]
    parsed = validate_paired_checker_result(zero, book(), levels=True)
    assert not parsed.rejected
    assert parsed.to_dict()["rule_results"][0]["finding"] == "Minor Plan concern"
    zero["rule_results"][0]["evidence"] = []
    with pytest.raises(ValueError):
        validate_paired_checker_result(zero, book(), levels=True)


def test_curator_risk_analysis_is_not_required_or_used():
    operation_without_risk = {
        "type": "ADD", "content": "Another concern", "category": "Scope",
        "supporting_instance_ids": ["pair-a"],
    }
    without_risk = apply_curator_operations(book(), {
        "reasoning": "Evidence supports this concern.",
        "operations": [operation_without_risk],
    })
    with_risk = apply_curator_operations(book(), {
        "reasoning": "Evidence supports this concern.",
        "operations": [{**operation_without_risk, "risk_analysis": "Legacy audit note"}],
    })
    assert without_risk == with_risk


def test_categories_preserve_order_and_hide_metadata():
    b = RejectPlaybook((*book().bullets, PlaybookBullet("plan-00002", "Concern B", category="Compatibility"),
                       PlaybookBullet("plan-00003", "Concern C", category="Scope")))
    assert RejectPlaybook.parse(b.serialize()) == b
    text = render_concern_playbook(b)
    assert "## Scope" in text and "## Compatibility" in text
    assert text.startswith("Reject the plan when:")
    assert text.index("Rule 2.") < text.index("Rule 3.")
    assert "plan-00001" not in text and "helpful" not in text
    assert "category" not in PlaybookBullet("plan-00001", "Old").to_dict()


def operation(kind="ADD", **kwargs):
    return {"type": kind, "supporting_instance_ids": ["pair-a"], "risk_analysis": "Risk", **kwargs}


def test_category_required_for_curation_and_preserved_by_refiner():
    with pytest.raises(ValueError, match="category"):
        apply_curator_operations(book(), {"reasoning": "why", "operations": [operation(content="New")]})
    revised = apply_curator_operations(book(), {"reasoning": "why", "operations": [
        operation("REVISE", target_id="plan-00001", content="New", category="Scope")]})
    assert revised.bullets[0].category == "Scope"
    assert revised.bullets[0].helpful == 0  # Existing revision counter semantics.
    refined = apply_refiner_operations(book(), {"reasoning": "why", "operations": [
        {"type": "REWORD", "target_id": "plan-00001", "content": "Short"}]})
    assert refined.bullets[0].category == "Scope"
    assert refined.bullets[0].helpful == 3


def test_refiner_does_not_merge_across_categories():
    b = RejectPlaybook((*book().bullets, PlaybookBullet("plan-00002", "B", category="Other")))
    with pytest.raises(ValueError, match="categories"):
        apply_refiner_operations(b, {"reasoning": "why", "operations": [
            {"type": "MERGE", "target_ids": ["plan-00001", "plan-00002"], "content": "Merged"}]})


def test_category_counts_towards_total_budget():
    rendered = render_concern_playbook(book())
    _, report = manage_playbook_length(book(), token_counter=len, semantic_refiner=lambda b: b,
        maximum_tokens=len(rendered), visible_renderer=render_concern_playbook)
    assert report["tokens_before"] == len(rendered)


def test_counters_identity_unchanged():
    ledger = GlobalPlaybookCounters(None)
    ledger.hydrate(book())
    moved = RejectPlaybook((PlaybookBullet("plan-00002", "Concern A", category="Other"),))
    assert ledger.hydrate(moved).bullets[0].helpful == 3


def test_read_only_bind_does_not_change_tmp_or_other_environments():
    env = object.__new__(ApptainerEnvironment)
    env._host_workdir = Path("/phase/repo")
    env._cwd = "/testbed"
    env._run_args = ["--bind", "/phase/repo:/testbed", "--bind", "/phase/tmp:/tmp"]
    env.make_repository_read_only()
    env.make_repository_read_only()
    assert env._run_args == ["--bind", "/phase/repo:/testbed:ro", "--bind", "/phase/tmp:/tmp"]


def test_development_config_and_seed_are_not_launchable():
    path = Path("configs/gepa_verified_paired_levels_categorized_dev_v1_20260921.yaml")
    raw = yaml.safe_load(path.read_text())
    _validate_frozen_inputs(path.resolve(), raw)
    assert paired_checker_uses_levels(raw)
    assert raw["models"]["checker"]["thinking"] == "disabled"
    assert "thinking" not in raw["models"]["reflector"]
    assert "thinking" not in raw["models"]["curator"]
    assert raw["hpc"]["submit"] is False
    seed = RejectPlaybook.parse(Path(raw["inputs"]["initial_playbook"]).read_text())
    assert seed.bullets[0].text == "The Plan is a placeholder."
    assert seed.bullets[0].category == "Plan substance"
    assert render_concern_playbook(seed) == (
        "Reject the plan when:\n\n## Plan substance\n\n"
        "Rule 1. The Plan is a placeholder."
    )
    with pytest.raises(ValueError, match="Draft"):
        run_from_config(path)


def test_lightweight_prompts_keep_calibration_separate_from_tags():
    raw = yaml.safe_load(Path(
        "configs/gepa_verified_paired_levels_categorized_dev_v1_20260921.yaml"
    ).read_text())
    prompts = yaml.safe_load(Path(raw["inputs"]["prompt_bundle"]).read_text())
    contract = yaml.safe_load(Path(raw["inputs"]["repo_checker_contract"]).read_text())
    checker = " ".join((prompts["checker_system"] + contract["checker_contract_appendix"]).split())
    reflector = " ".join(prompts["reflector_system"].split())
    curator = " ".join(prompts["curator_system"].split())
    assert "quick, lightweight review" in checker
    assert "not a trial implementation or test execution" in checker
    assert "reflection" not in checker.casefold()
    assert '"level": 0' not in checker
    assert "missing low-level detail" not in checker
    assert "Do not map Levels directly to tags" in reflector
    assert "neutral tag may still include useful Level-calibration analysis" in reflector
    assert "rule's wording" in reflector
    assert "No operation type or number of additions is preferred" in curator


def test_formal_paired_levels_inputs_and_review_boundary():
    from src.optimization.paired_dataset import load_paired_snapshot

    path = Path(
        "configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_20260921.yaml"
    )
    raw = yaml.safe_load(path.read_text())
    _validate_frozen_inputs(path.resolve(), raw)
    train, validation = load_paired_snapshot(raw["inputs"]["dataset_snapshot"])
    selected_ids = raw["inputs"]["train_instance_ids"]
    assert len(selected_ids) == len(set(selected_ids)) == 143
    assert set(selected_ids) == {case.instance_id for case in train} - {
        "pair-8738412ddd171f1772a98f43"
    }
    assert len(validation) == 36
    assert {case.task_id for case in train if case.instance_id in selected_ids}.isdisjoint(
        {case.task_id for case in validation}
    )
    assert raw["search"]["max_iterations"] == 8
    assert raw["search"]["reflection_minibatch_size"] == 24
    assert raw["models"]["checker"]["thinking"] == "disabled"
    assert raw["repo_checker"]["review_only"] is True
    assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
    prompt = yaml.safe_load(Path(raw["inputs"]["prompt_bundle"]).read_text())[
        "checker_system"
    ]
    assert "Use repository\ninspection to understand existing code and tests" in prompt
    assert "including in temporary\nfiles or memory" in prompt
    assert "Small, read-only probes" not in prompt
    launch = yaml.safe_load(Path(
        "configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_supervisor_20260921.yaml"
    ).read_text())
    args = launch["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert args[args.index("--target-iterations") + 1] == "8"
    assert args[args.index("--ulhpc-config") + 1] == "configs/ulhpc_submit.yaml"
    for flag in ("--reclaim-staging", "--reclaim-workspaces", "--require-clean-worktree"):
        assert flag in args


def test_neutral_reflection_can_report_calibration_without_new_concerns():
    from src.optimization.paired_playbook import validate_paired_reflector_review

    report = {
        "instance_id": "pair-a",
        "pair_analysis": "Both findings warrant warnings, not a pause.",
        "reusable_concerns": [],
        "uncertainty": "Implementation sampling may explain the outcome difference.",
        "bullet_tags": [{
            "id": "plan-00001", "tag": "neutral",
            "attribution": "The broad wording may encourage over-severe findings.",
            "confidence": "medium",
        }],
    }
    normalized = validate_paired_reflector_review(
        report, instance_id="pair-a", playbook=book()
    )
    assert normalized["bullet_tags"] == [{
        "id": "plan-00001", "tag": "neutral",
        "attribution": "The broad wording may encourage over-severe findings.",
    }]
    assert "confidence" not in normalized["bullet_tags"][0]


def test_paired_reflection_concerns_need_no_confidence_and_strip_legacy_field():
    from src.optimization.paired_playbook import validate_paired_reflector_review

    report = {
        "instance_id": "pair-a",
        "pair_analysis": "The Code Agent repaired the missing state update.",
        "reusable_concerns": [{
            "concern": "A related state update is absent.",
            "pair_support": "One Code Agent repaired it; the other did not.",
        }],
        "uncertainty": None,
        "bullet_tags": [{
            "id": "plan-00001", "tag": "neutral", "attribution": None,
        }],
    }
    expected = validate_paired_reflector_review(
        report, instance_id="pair-a", playbook=book()
    )
    assert expected == report
    legacy = json.loads(json.dumps(report))
    legacy["reusable_concerns"][0]["confidence"] = "low"
    legacy["bullet_tags"][0]["confidence"] = "high"
    assert validate_paired_reflector_review(
        legacy, instance_id="pair-a", playbook=book()
    ) == expected


def test_structured_pair_reflection_keeps_coder_compensation():
    from src.optimization.paired_playbook import validate_paired_reflector_review

    report = {
        "instance_id": "pair-a", "pair_analysis": "Both plans omit the state update.",
        "side_findings": [
            {"side": "resolved", "plan_concerns": [{
                "concern": "Related state update is missing.",
                "decision_time_support": "The plan changes one branch only.",
                "coder_response": "compensated",
                "outcome_relation": "Coder added the update and resolved.",
            }]},
            {"side": "unresolved", "plan_concerns": [{
                "concern": "Related state update is missing.",
                "decision_time_support": "The plan changes one branch only.",
                "coder_response": "followed",
                "outcome_relation": "Coder omitted it and did not resolve.",
            }]},
        ],
        "reusable_concerns": [{
            "concern": "A branch change leaves related state inconsistent.",
            "pair_support": "The gap is shared, but only one Coder compensated.",
            "curation_assessment": {
                "pair_relation": "shared",
                "decision_time_status": "supported",
                "coder_repairability": "mixed_or_unclear",
                "recommendation": "promote",
                "reason": "The repository exposes a reusable state-consistency concern.",
            },
        }],
        "uncertainty": None,
        "bullet_tags": [{"id": "plan-00001", "tag": "neutral", "attribution": None}],
    }
    assert validate_paired_reflector_review(
        report, instance_id="pair-a", playbook=book(), structured_recovery=True
    ) == report
    without_sides = {key: value for key, value in report.items() if key != "side_findings"}
    with pytest.raises(ValueError):
        validate_paired_reflector_review(
            without_sides, instance_id="pair-a", playbook=book(), structured_recovery=True
        )
    invalid = json.loads(json.dumps(report))
    invalid["reusable_concerns"][0]["curation_assessment"]["pair_relation"] = "winner"
    with pytest.raises(ValueError, match="pair relation"):
        validate_paired_reflector_review(
            invalid, instance_id="pair-a", playbook=book(), structured_recovery=True
        )
    hindsight = json.loads(json.dumps(report))
    hindsight["reusable_concerns"][0]["curation_assessment"].update(
        decision_time_status="hindsight_only", recommendation="promote"
    )
    with pytest.raises(ValueError, match="hindsight-only concern must be deferred"):
        validate_paired_reflector_review(
            hindsight, instance_id="pair-a", playbook=book(), structured_recovery=True
        )


def test_curator_coverage_requires_every_side_and_reusable_finding(tmp_path):
    from src.optimization.playbook_hpc_agents import HPCPlaybookProposalAgents

    calls = []

    class Executor:
        run_dir = tmp_path

        def run_wave(self, role, items):
            calls.append((role, items))
            return [{"agent_output": {"reasoning": "No durable change.", "operations": [],
                "reviewed_concerns": [
                    {"id": item, "disposition": "DEFERRED", "operation_numbers": [],
                     "reason": "Shared gap is not yet a durable bullet."}
                    for item in items[0]["validation_concern_ids"]
                ]}}]

    agents = HPCPlaybookProposalAgents(
        Executor(), maximum_tokens=2048, require_concern_coverage=True
    )
    review = {
        "instance_id": "pair-a", "pair_analysis": "Both sides have a gap.",
        "reusable_concerns": [{"concern": "Shared gap", "pair_support": "One Coder repairs it."}],
        "side_findings": [
            {"side": "resolved", "plan_concerns": [{"concern": "Gap", "coder_response": "compensated"}]},
            {"side": "unresolved", "plan_concerns": [{"concern": "Gap", "coder_response": "followed"}]},
        ],
        "uncertainty": None, "bullet_tags": [],
    }
    output = agents.curate(book(), [review])
    ids = ["pair-a:c1", "pair-a:r1", "pair-a:u1"]
    assert calls[0][1][0]["validation_concern_ids"] == ids
    index = json.loads((Path(calls[0][1][0]["evidence_dir"]) / "reflection_index.json").read_text())
    assert index[0]["pair_analysis"] == "Both sides have a gap."
    assert index[0]["side_findings"][0]["plan_concerns"][0]["id"] == "pair-a:r1"
    validate_curator_concern_coverage(output, ids)
    with pytest.raises(ValueError, match="every reflected concern"):
        validate_curator_concern_coverage(
            {**output, "reviewed_concerns": output["reviewed_concerns"][:-1]}, ids
        )


def test_controller_revalidates_structured_paired_reflection(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_paired_repo_concern_playbook\n", encoding="utf-8")
    executor = PlaybookHPCExecutor(
        config_path=config,
        run_dir=tmp_path / "run",
        hpc=HPCConfig(submit=False),
        paired_reflector_structured_recovery=True,
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    item = {
        "instance_id": "pair-a",
        "prompt_values": {},
        "validation_playbook": book().serialize(),
        "evidence_dir": str(evidence),
        "source_access_issue": "Issue",
        "repository": {"instance_id": "pair-a", "repo": "org/repo", "base_commit": "abc"},
        "image_authority": {"sif_sha256": "0" * 64},
    }
    monkeypatch.setattr(
        "src.optimization.playbook_runtime.require_prepared_repository_history",
        lambda *_args: None,
    )
    batch = executor.batch_dir_for("paired_repo_reflector", [item])
    fingerprint = batch.name
    output = {
        "schema_version": 1,
        "status": "completed",
        "role": "paired_repo_reflector",
        "fingerprint": fingerprint,
        "task_index": 0,
        "instance_id": "pair-a",
        "agent_output": {
            "instance_id": "pair-a",
            "pair_analysis": None,
            "side_findings": [
                {"side": "resolved", "plan_concerns": []},
                {"side": "unresolved", "plan_concerns": []},
            ],
            "reusable_concerns": [],
            "uncertainty": None,
            "bullet_tags": [
                {"id": "plan-00001", "tag": "neutral", "attribution": None}
            ],
        },
        "trajectory": [],
    }
    output_path = batch / "outputs/task_0000.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text(json.dumps(output), encoding="utf-8")
    assert executor.run_wave("paired_repo_reflector", [item])[0]["status"] == "completed"


def test_learning12_smoke_is_frozen_and_launch_ready():
    path = Path("configs/gepa_verified_paired_learning12_smoke_v1_20260922.yaml")
    raw = yaml.safe_load(path.read_text())
    _validate_frozen_inputs(path.resolve(), raw)
    assert raw["readiness"] == {
        "runnable": True,
        "launched": False,
        "missing": [],
    }
    assert (raw["search"]["max_iterations"], raw["search"]["reflection_minibatch_size"]) == (1, 12)
    assert raw["reflection"]["structured_recovery"] is True
    assert raw["curation"]["require_concern_coverage"] is True
    selection = json.loads(Path(raw["inputs"]["selection"]).read_text())
    clean = json.loads(Path(
        "configs/frozen_swe_verified_plan_pairs/20260922_operationally_clean138_v1/selection.json"
    ).read_text())
    assert set(selection["train_instance_ids"]).issubset(clean["train_instance_ids"])
    assert raw["inputs"]["train_instance_ids"] == selection["train_instance_ids"]
    assert raw["inputs"]["validation_instance_ids"] == selection["validation_instance_ids"]


def test_learning12_curator_recovery_imports_only_completed_agent_evidence():
    path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v1_curator_recovery_20260922.yaml"
    )
    raw = yaml.safe_load(path.read_text())
    _validate_frozen_inputs(path.resolve(), raw)
    assert raw["checkpoint_import"] == {
        "source_run_dir": Path(
            "/scratch/users/twang/vibe-coding-planning/run_state/output/"
            "SWE-bench_Verified/gepa-paired-repo-concern-playbook-runs/"
            "learning12-smoke-v1-20260922"
        ).as_posix(),
        "source_run_manifest_sha256": (
            "4dc5cbc3ca18df2409fd1836c0b1624df08aec661c18eabd2e6cfed4b4220c19"
        ),
        "roles": ["paired_repo_checker", "paired_repo_reflector"],
    }
    assert "curator" not in raw["checkpoint_import"]["roles"]
    assert raw["paths"]["run_dir"].endswith(
        "learning12-smoke-v1-curator-recovery-20260922"
    )


def test_learning12_reflector_curator_replay_uses_v4_and_reuses_only_checkers():
    path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v2_ref_cur_20260922.yaml"
    )
    raw = yaml.safe_load(path.read_text())
    _validate_frozen_inputs(path.resolve(), raw)
    assert raw["inputs"]["prompt_bundle"].endswith(
        "offline_gepa_paired_levels_curation_assessment_v4_20260922.yaml"
    )
    assert raw["inputs"]["prompt_bundle_sha256"] == (
        "c727aa8d41cfa2549454b3ecb266c174c21b94e87b68f65a21fa6bc36b3afaec"
    )
    assert raw["checkpoint_import"]["roles"] == ["paired_repo_checker"]
    assert raw["reflection"]["structured_recovery"] is True
    assert raw["curation"]["require_concern_coverage"] is True
    assert raw["readiness"] == {
        "runnable": True,
        "launched": False,
        "missing": [],
    }
    launch = yaml.safe_load(
        Path(
            "configs/gepa_verified_paired_learning12_smoke_v2_ref_cur_supervisor_20260922.yaml"
        ).read_text()
    )
    args = launch["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert args[args.index("--target-iterations") + 1] == "1"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == "4G"


def test_lightweight_smoke_selection_resources_and_launch_contract():
    from src.optimization.paired_dataset import load_paired_snapshot

    path = Path("configs/gepa_verified_paired_levels_smoke8_v1_20260921.yaml")
    raw = yaml.safe_load(path.read_text())
    _validate_frozen_inputs(path.resolve(), raw)
    assert raw["status"] == "ready_not_launched"
    assert raw["readiness"]["launched"] is False
    assert raw["search"]["max_iterations"] == 1
    assert raw["search"]["reflection_minibatch_size"] == 4
    assert raw["search"]["skip_perfect_score"] is False
    assert raw["search"]["max_metric_calls"] == 32
    assert raw["reflection"]["rounds"] == 1
    assert raw["models"]["checker"]["thinking"] == "disabled"
    assert "thinking" not in raw["models"]["reflector"]
    assert "thinking" not in raw["models"]["curator"]
    assert paired_checker_uses_levels(raw)
    assert raw["repo_checker"]["review_only"] is True
    assert raw["repo_checker"]["history_policy"] == "base_ancestor_bundle_v1"
    assert "checkpoint_import" not in raw
    assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
    assert raw["hpc"]["agent_time"] == "00:35:00"
    assert raw["hpc"]["max_task_attempts"] == 3
    splits = load_paired_snapshot(raw["inputs"]["dataset_snapshot"])
    selected = []
    for cases, key in zip(splits, ("train_instance_ids", "validation_instance_ids")):
        ids = raw["inputs"][key]
        rows = [c for c in cases if c.instance_id in ids]
        assert len(rows) == len(ids) == 4
        assert len({c.repository.repo for c in rows}) == 4
        assert len({c.task_id for c in rows}) == 4
        selected.append({c.task_id for c in rows})
    assert selected[0].isdisjoint(selected[1])
    launch = yaml.safe_load(Path(
        "configs/gepa_verified_paired_levels_smoke8_v1_supervisor_20260921.yaml"
    ).read_text())
    args = launch["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert args[args.index("--target-iterations") + 1] == "1"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == "4G"
    assert args[args.index("--ulhpc-config") + 1] == "configs/ulhpc_submit.yaml"
    for flag in ("--reclaim-staging", "--reclaim-workspaces", "--require-clean-worktree"):
        assert flag in args
