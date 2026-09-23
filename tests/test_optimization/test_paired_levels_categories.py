import json
from pathlib import Path

import pytest
import yaml

from src.environment.apptainer_env import ApptainerEnvironment
from src.optimization.paired_playbook import (
    paired_checker_uses_levels,
    validate_paired_checker_result,
    validate_paired_reflector_review,
)
from src.optimization.playbook import (PlaybookBullet, RejectPlaybook, apply_curator_operations,
    apply_refiner_operations, manage_playbook_length, validate_curator_concern_coverage,
    validate_curator_self_check)
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


def test_structured_pair_reflection_requires_abstraction_and_evidence_role():
    from src.optimization.paired_playbook import validate_paired_reflector_review

    report = {
        "instance_id": "pair-a",
        "pair_analysis": "The Plan difference plausibly explains the outcome split.",
        "side_findings": [
            {"side": "resolved", "plan_concerns": []},
            {"side": "unresolved", "plan_concerns": [{
                "concern": "A related consumer is omitted.",
                "decision_time_support": "The repository has two consumers.",
                "coder_response": "followed",
                "outcome_relation": "The omitted consumer remained unchanged.",
            }]},
        ],
        "reusable_concerns": [{
            "case_mechanism": "One helper has two repository-specific callers.",
            "developer_concern": "The Plan changes shared behavior but covers only one affected consumer.",
            "pair_support": "The unresolved Plan and patch both omit the second consumer.",
            "curation_assessment": {
                "pair_relation": "distinguishes",
                "evidence_role": "outcome_explanatory",
                "decision_time_status": "supported",
                "coder_repairability": "not_repaired",
            },
        }],
        "uncertainty": None,
        "bullet_tags": [{"id": "plan-00001", "tag": "neutral", "attribution": None}],
    }
    assert validate_paired_reflector_review(
        report,
        instance_id="pair-a",
        playbook=book(),
        structured_recovery=True,
        structured_abstraction=True,
    ) == report

    legacy = json.loads(json.dumps(report))
    concern = legacy["reusable_concerns"][0]
    concern["concern"] = concern.pop("developer_concern")
    concern.pop("case_mechanism")
    concern["curation_assessment"].pop("evidence_role")
    with pytest.raises(ValueError, match="reusable concern is invalid"):
        validate_paired_reflector_review(
            legacy,
            instance_id="pair-a",
            playbook=book(),
            structured_recovery=True,
            structured_abstraction=True,
        )

    invalid_role = json.loads(json.dumps(report))
    invalid_role["reusable_concerns"][0]["curation_assessment"]["evidence_role"] = "different"
    with pytest.raises(ValueError, match="evidence role"):
        validate_paired_reflector_review(
            invalid_role,
            instance_id="pair-a",
            playbook=book(),
            structured_recovery=True,
            structured_abstraction=True,
        )

    hindsight = json.loads(json.dumps(report))
    hindsight["reusable_concerns"][0]["curation_assessment"][
        "decision_time_status"
    ] = "hindsight_only"
    with pytest.raises(ValueError, match="decision-time status"):
        validate_paired_reflector_review(
            hindsight,
            instance_id="pair-a",
            playbook=book(),
            structured_recovery=True,
            structured_abstraction=True,
        )

    confidence = json.loads(json.dumps(report))
    confidence["reusable_concerns"][0]["confidence"] = "high"
    with pytest.raises(ValueError, match="reusable concern is invalid"):
        validate_paired_reflector_review(
            confidence,
            instance_id="pair-a",
            playbook=book(),
            structured_recovery=True,
            structured_abstraction=True,
        )


def test_v5_prompt_separates_mechanism_concern_and_pair_evidence_role():
    path = Path(
        "configs/prompts/offline_gepa_paired_levels_curation_assessment_v5_20260923.yaml"
    )
    prompts = yaml.safe_load(path.read_text())
    reflector = " ".join(prompts["reflector_system"].split())
    curator = " ".join(prompts["curator_system"].split())
    instance = prompts["reflector_instance"]

    assert "case mechanism from the developer-facing concern" in reflector
    assert "evidence_role separately says what this pair teaches" in reflector
    assert "Runtime package availability" in reflector
    assert "Apply a portability check" in reflector
    assert "Do not perform an exhaustive generic Plan-quality review" in reflector
    assert "automatically adds, removes, promotes, or defers" in reflector
    assert '"case_mechanism"' in instance
    assert '"developer_concern"' in instance
    assert '"evidence_role"' in instance
    assert "Use case_mechanism to understand the source evidence" in curator
    assert "apply this portability check" in curator
    assert "do not independently justify a new bullet" in curator
    assert "no pair_relation, evidence_role" in curator
    assert '"recommendation"' not in instance
    assert "recommendation" not in reflector


def test_distilled_reflector_contract_separates_curator_signal_from_audit():
    from src.optimization.paired_playbook import validate_paired_reflector_review

    report = {
        "instance_id": "pair-a",
        "pair_analysis": "The unresolved implementation retained the Plan gap.",
        "side_findings": [
            {"side": "resolved", "plan_concerns": []},
            {"side": "unresolved", "plan_concerns": [{
                "concern": "A shared consumer is omitted.",
                "decision_time_support": "The repository exposes two consumers.",
                "coder_response": "followed",
                "outcome_relation": "The implementation retained the omission.",
            }]},
        ],
        "reusable_concerns": [{
            "developer_concern": "The Plan changes shared behavior but omits an affected consumer.",
            "decision_time_basis": "The Plan names one of two repository consumers.",
            "pair_evidence": "The omission persisted only in the unresolved attempt.",
        }],
        "uncertainty": None,
        "bullet_tags": [
            {"id": "plan-00001", "tag": "neutral", "attribution": None}
        ],
    }
    assert validate_paired_reflector_review(
        report,
        instance_id="pair-a",
        playbook=book(),
        structured_recovery=True,
        structured_abstraction=True,
        distilled_curation=True,
    ) == report

    leaked_assessment = json.loads(json.dumps(report))
    leaked_assessment["reusable_concerns"][0]["curation_assessment"] = {}
    with pytest.raises(ValueError, match="reusable concern is invalid"):
        validate_paired_reflector_review(
            leaked_assessment,
            instance_id="pair-a",
            playbook=book(),
            structured_recovery=True,
            structured_abstraction=True,
            distilled_curation=True,
        )


def test_v6_prompt_restores_ace_role_skeleton_and_optional_side_channel():
    path = Path(
        "configs/prompts/offline_gepa_paired_levels_ace_core_v6_20260923.yaml"
    )
    prompts = yaml.safe_load(path.read_text())
    checker = " ".join(prompts["checker_system"].split())
    reflector = " ".join(prompts["reflector_system"].split())
    curator = " ".join(prompts["curator_system"].split())
    curator_instance = " ".join(prompts["curator_instance"].split())

    assert "if implementation follows the current Plan as written" in checker
    assert "side_findings" in reflector and "full audit channel" in reflector
    assert "reusable_concern only" in reflector
    assert "decision-time basis separately" in reflector
    assert "required reflection_index.json" in curator
    assert "optional case_reflections.json" in curator
    assert "A durable change is warranted when paired evidence supports" in curator
    assert "reviewed_concerns" not in curator_instance
    assert "curation_assessment" not in prompts["reflector_instance"]
    assert "risk_analysis" not in path.read_text()


def test_v7_curator_maintains_rules_and_self_checks_readability():
    path = Path(
        "configs/prompts/offline_gepa_paired_levels_ace_core_v7_20260923.yaml"
    )
    prompts = yaml.safe_load(path.read_text())
    curator = " ".join(prompts["curator_system"].split())
    curator_instance = " ".join(prompts["curator_instance"].split())

    assert "ACE rule-utility evidence" in curator
    assert "Prefer REVISE over adding an overlapping rule" in curator
    assert "A bullet states one Plan-stage concern" in curator
    assert "review procedure, repair instruction, test instruction" in curator
    assert "understandable without knowing the source case" in curator
    assert "enough information for a Checker to recognize that concern" in curator
    assert "counter review, and operation self-check" in curator_instance
    assert "supporting_side_findings" in prompts["reflector_instance"]
    assert "supporting_concern_ids" in curator_instance
    assert "numeric deletion thresholds" in curator
    assert "risk_analysis" not in path.read_text()


def test_distilled_fact_links_and_lightweight_curator_self_check(tmp_path):
    from src.optimization.playbook_hpc_agents import HPCPlaybookProposalAgents

    raw_review = {
        "instance_id": "pair-a",
        "pair_analysis": "Both Plans contain the concern; only one Coder compensates.",
        "side_findings": [
            {
                "side": "resolved",
                "plan_concerns": [{
                    "concern": "The Plan omits a shared consumer.",
                    "decision_time_support": "The repository exposes that consumer.",
                    "coder_response": "compensated",
                    "outcome_relation": "The Coder expanded the scope before succeeding.",
                }],
            },
            {
                "side": "unresolved",
                "plan_concerns": [{
                    "concern": "The Plan omits a shared consumer.",
                    "decision_time_support": "The repository exposes that consumer.",
                    "coder_response": "followed",
                    "outcome_relation": "The implementation retained the omission.",
                }],
            },
        ],
        "reusable_concerns": [{
            "developer_concern": "The Plan changes shared behavior without covering all affected consumers.",
            "decision_time_basis": "The repository exposes consumers outside the Plan's scope.",
            "pair_evidence": "The omission was compensated in one attempt and retained in the other.",
            "supporting_side_findings": [
                {"side": "resolved", "finding_number": 1},
                {"side": "unresolved", "finding_number": 1},
            ],
        }],
        "uncertainty": None,
        "bullet_tags": [
            {"id": "plan-00001", "tag": "neutral", "attribution": None}
        ],
    }
    review = validate_paired_reflector_review(
        raw_review,
        instance_id="pair-a",
        playbook=book(),
        structured_recovery=True,
        structured_abstraction=True,
        distilled_curation=True,
        fact_links=True,
    )
    calls = []

    class Executor:
        run_dir = tmp_path

        def run_wave(self, role, items):
            calls.append((role, items))
            return [{"agent_output": {
                "reasoning": "The linked evidence supports one portable rule.",
                "operations": [{
                    "type": "ADD",
                    "content": "The Plan changes shared behavior without covering all affected consumers.",
                    "category": "Scope",
                    "supporting_instance_ids": ["pair-a"],
                }],
                "self_check": {
                    "required_files_read": True,
                    "operation_checks": [{
                        "operation_number": 1,
                        "supporting_concern_ids": ["pair-a:c1"],
                        "one_concern": True,
                        "condition_explicit": True,
                        "source_case_independent": True,
                        "decision_time_wording": True,
                        "plain_language": True,
                    }],
                },
            }}]

    agents = HPCPlaybookProposalAgents(
        Executor(),
        maximum_tokens=2048,
        evidence_contract="distilled_v1",
        require_curator_self_check=True,
    )
    output = agents.curate(book(), [review])
    validate_curator_self_check(output, ["pair-a:c1"])
    item = calls[0][1][0]
    assert item["validation_self_check_concern_ids"] == ["pair-a:c1"]
    evidence = Path(item["evidence_dir"])
    index = json.loads((evidence / "reflection_index.json").read_text())
    assert index[0]["reusable_concerns"][0]["supporting_side_finding_ids"] == [
        "pair-a:r1", "pair-a:u1"
    ]
    assert [row["id"] for row in index[0]["linked_side_findings"]] == [
        "pair-a:r1", "pair-a:u1"
    ]
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["concern_ids"] == ["pair-a:c1"]


def test_fact_links_and_curator_self_check_reject_invalid_references():
    review = {
        "instance_id": "pair-a",
        "pair_analysis": "Analysis",
        "side_findings": [
            {"side": "resolved", "plan_concerns": []},
            {"side": "unresolved", "plan_concerns": []},
        ],
        "reusable_concerns": [{
            "developer_concern": "Concern",
            "decision_time_basis": "Basis",
            "pair_evidence": "Evidence",
            "supporting_side_findings": [
                {"side": "unresolved", "finding_number": 1}
            ],
        }],
        "uncertainty": None,
        "bullet_tags": [
            {"id": "plan-00001", "tag": "neutral", "attribution": None}
        ],
    }
    with pytest.raises(ValueError, match="missing side finding"):
        validate_paired_reflector_review(
            review,
            instance_id="pair-a",
            playbook=book(),
            structured_recovery=True,
            structured_abstraction=True,
            distilled_curation=True,
            fact_links=True,
        )

    output = {
        "reasoning": "Reason",
        "operations": [],
        "self_check": {
            "required_files_read": False,
            "operation_checks": [],
        },
    }
    with pytest.raises(ValueError, match="required evidence"):
        validate_curator_self_check(output, [])


def test_cli_binds_fact_links_to_controller_review_validator():
    from src.optimization.playbook_cli import _paired_review_validator

    validate = _paired_review_validator({
        "reflection": {
            "structured_recovery": True,
            "structured_abstraction": True,
            "distilled_curation": True,
            "fact_links": True,
        }
    })
    report = {
        "instance_id": "pair-a",
        "pair_analysis": "The unresolved Plan omits an affected consumer.",
        "side_findings": [
            {"side": "resolved", "plan_concerns": []},
            {"side": "unresolved", "plan_concerns": [{
                "concern": "An affected consumer is omitted.",
                "decision_time_support": "The repository exposes two consumers.",
                "coder_response": "followed",
                "outcome_relation": "The implementation retained the omission.",
            }]},
        ],
        "reusable_concerns": [{
            "developer_concern": "The Plan changes shared behavior but omits an affected consumer.",
            "decision_time_basis": "The repository exposes two consumers.",
            "pair_evidence": "The unresolved implementation retained the omission.",
            "supporting_side_findings": [
                {"side": "unresolved", "finding_number": 1}
            ],
        }],
        "uncertainty": None,
        "bullet_tags": [
            {"id": "plan-00001", "tag": "neutral", "attribution": None}
        ],
    }

    assert validate(report, instance_id="pair-a", playbook=book()) == report


def test_lightweight_self_check_config_requires_fact_links(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({
            "mode": "offline_paired_repo_concern_playbook",
            "reflection": {
                "structured_recovery": True,
                "structured_abstraction": True,
                "distilled_curation": True,
            },
            "curation": {
                "evidence_contract": "distilled_v1",
                "self_check_contract": "lightweight_v1",
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requires Reflection fact links"):
        run_from_config(config)


def test_v5_linked_two_phase_smoke_reuses_unchanged_parent_checkers():
    prompt_v6 = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_levels_ace_core_v6_20260923.yaml"
    ).read_text())
    prompt_v7 = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_levels_ace_core_v7_20260923.yaml"
    ).read_text())
    assert prompt_v7["checker_system"] == prompt_v6["checker_system"]
    assert prompt_v7["checker_instance"] == prompt_v6["checker_instance"]

    paths = [
        Path("configs/gepa_verified_paired_learning12_smoke_v5_linked_ref_cur_20260923.yaml"),
        Path("configs/gepa_verified_paired_learning12_smoke_v5_linked_manual_20260923.yaml"),
    ]
    replay, manual = [yaml.safe_load(path.read_text()) for path in paths]
    for path, raw in zip(paths, (replay, manual), strict=True):
        _validate_frozen_inputs(path.resolve(), raw)
        assert raw["readiness"] == {
            "runnable": True, "launched": False, "missing": []
        }
        assert raw["reflection"]["fact_links"] is True
        assert raw["curation"]["self_check_contract"] == "lightweight_v1"
        assert raw["curation"]["require_concern_coverage"] is False
        assert raw["checkpoint_import"]["roles"] == ["paired_repo_checker"]
        assert raw["search"]["max_iterations"] == 1
        assert raw["search"]["reflection_minibatch_size"] == 12
        assert raw["models"]["checker"]["thinking"] == "disabled"
        assert raw["hpc"]["mem"] == "4G"
        assert raw["hpc"]["cpus_per_task"] == 1
        assert raw["hpc"]["poll_interval_seconds"] == 60

    assert replay["checkpoint_import"]["source_run_manifest_sha256"] == (
        "4dc5cbc3ca18df2409fd1836c0b1624df08aec661c18eabd2e6cfed4b4220c19"
    )
    assert manual["checkpoint_import"]["source_run_manifest_sha256"] == (
        "4b765c5d34d6a054fed455886ebb0c2c452d02a982626f75b1830207803c628a"
    )
    assert replay["inputs"]["initial_playbook"] != manual["inputs"]["initial_playbook"]
    assert replay["inputs"]["train_instance_ids"] == manual["inputs"]["train_instance_ids"]
    assert replay["inputs"]["validation_instance_ids"] == manual["inputs"]["validation_instance_ids"]

    supervisor = yaml.safe_load(Path(
        "configs/gepa_verified_paired_learning12_smoke_v5_linked_sequence_supervisor_20260923.yaml"
    ).read_text())
    assert [run["name"] for run in supervisor["runs"]] == [
        "controlled-linked-reflector-curator-replay",
        "manual-playbook-linked-full-cycle",
    ]
    assert all("--require-clean-worktree" in run["arguments"] for run in supervisor["runs"])


def test_distilled_curator_index_excludes_side_findings_and_dispositions(tmp_path):
    from src.optimization.playbook_hpc_agents import HPCPlaybookProposalAgents

    calls = []

    class Executor:
        run_dir = tmp_path

        def run_wave(self, role, items):
            calls.append((role, items))
            return [{"agent_output": {"reasoning": "No durable change.", "operations": []}}]

    agents = HPCPlaybookProposalAgents(
        Executor(), maximum_tokens=2048, evidence_contract="distilled_v1"
    )
    review = {
        "instance_id": "pair-a",
        "pair_analysis": "Full retrospective audit.",
        "reusable_concerns": [{
            "developer_concern": "The Plan omits an affected consumer.",
            "decision_time_basis": "The repository exposes that consumer.",
            "pair_evidence": "The omission persisted in one attempt.",
        }],
        "side_findings": [
            {"side": "resolved", "plan_concerns": [{"concern": "Case detail"}]},
            {"side": "unresolved", "plan_concerns": []},
        ],
        "uncertainty": "One implementation departed from its Plan.",
        "bullet_tags": [],
    }
    assert agents.curate(book(), [review]) == {
        "reasoning": "No durable change.", "operations": []
    }
    item = calls[0][1][0]
    assert "validation_concern_ids" not in item
    evidence = Path(item["evidence_dir"])
    index = json.loads((evidence / "reflection_index.json").read_text())
    assert index == [{
        "instance_id": "pair-a",
        "reusable_concerns": [{
            "id": "pair-a:c1",
            "developer_concern": "The Plan omits an affected consumer.",
            "decision_time_basis": "The repository exposes that consumer.",
            "pair_evidence": "The omission persisted in one attempt.",
        }],
        "uncertainty": "One implementation departed from its Plan.",
    }]
    assert "side_findings" not in index[0]
    full = json.loads((evidence / "case_reflections.json").read_text())
    assert full[0]["side_findings"][0]["plan_concerns"]
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["required_files"] == [
        "counted_playbook.json", "reflection_index.json"
    ]
    assert manifest["optional_files"] == ["case_reflections.json"]
    assert "concern_ids" not in manifest

    with pytest.raises(ValueError, match="cannot require finding dispositions"):
        HPCPlaybookProposalAgents(
            Executor(),
            maximum_tokens=2048,
            evidence_contract="distilled_v1",
            require_concern_coverage=True,
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


@pytest.mark.parametrize("distilled", [False, True])
def test_controller_revalidates_structured_paired_reflection(
    tmp_path, monkeypatch, distilled
):
    config = tmp_path / "config.yaml"
    config.write_text("mode: offline_paired_repo_concern_playbook\n", encoding="utf-8")
    executor = PlaybookHPCExecutor(
        config_path=config,
        run_dir=tmp_path / "run",
        hpc=HPCConfig(submit=False),
        paired_reflector_structured_recovery=True,
        paired_reflector_structured_abstraction=True,
        paired_reflector_distilled_curation=distilled,
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
            "pair_analysis": "A shared concern calibrates repairability.",
            "side_findings": [
                {"side": "resolved", "plan_concerns": []},
                {"side": "unresolved", "plan_concerns": []},
            ],
            "reusable_concerns": ([{
                "developer_concern": "The Plan changes shared behavior without covering all affected consumers.",
                "decision_time_basis": "The repository exposes two affected consumers.",
                "pair_evidence": "Only one implementation compensated for the shared omission.",
            }] if distilled else [{
                "case_mechanism": "Two consumers depend on one changed behavior.",
                "developer_concern": "The Plan changes shared behavior without covering all affected consumers.",
                "pair_support": "Only one implementation compensated for the shared omission.",
                "curation_assessment": {
                    "pair_relation": "shared",
                    "evidence_role": "repairability_calibration",
                    "decision_time_status": "supported",
                    "coder_repairability": "mixed_or_unclear",
                },
            }]),
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
    assert args[args.index("--max-runs") + 1] == "8"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == "4G"


def test_learning12_v3_sequence_freezes_replay_then_manual_full_cycle():
    replay_path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v3_ref_cur_20260923.yaml"
    )
    manual_path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v3_manual_full_20260923.yaml"
    )
    replay = yaml.safe_load(replay_path.read_text())
    manual = yaml.safe_load(manual_path.read_text())
    _validate_frozen_inputs(replay_path.resolve(), replay)
    _validate_frozen_inputs(manual_path.resolve(), manual)

    for raw in (replay, manual):
        assert raw["inputs"]["prompt_bundle"].endswith(
            "offline_gepa_paired_levels_curation_assessment_v5_20260923.yaml"
        )
        assert raw["reflection"] == {
            "rounds": 1,
            "structured_recovery": True,
            "structured_abstraction": True,
        }
        assert raw["search"]["max_iterations"] == 1
        assert raw["search"]["reflection_minibatch_size"] == 12
        assert raw["models"]["checker"]["thinking"] == "disabled"
        assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")

    assert replay["checkpoint_import"]["roles"] == ["paired_repo_checker"]
    assert "checkpoint_import" not in manual
    manual_book = RejectPlaybook.parse(
        Path(manual["inputs"]["initial_playbook"]).read_text()
    )
    assert len(manual_book.bullets) == 10
    assert manual_book.bullets[0].text == "The Plan is a placeholder."
    assert all(bullet.helpful == bullet.harmful == 0 for bullet in manual_book.bullets)

    launch = yaml.safe_load(Path(
        "configs/gepa_verified_paired_learning12_smoke_v3_sequence_supervisor_20260923.yaml"
    ).read_text())
    assert launch["program"] == "resume_sequence"
    assert [run["name"] for run in launch["runs"]] == [
        "audited-checker-reflector-curator-replay",
        "manual-playbook-full-cycle",
    ]
    configs = []
    for run in launch["runs"]:
        args = run["arguments"]
        configs.append(args[args.index("--gepa-config") + 1])
        assert args[args.index("--cpus") + 1] == "1"
        assert args[args.index("--mem") + 1] == "4G"
    assert configs == [str(replay_path), str(manual_path)]


def test_learning12_v4_ace_core_sequence_is_launch_ready():
    full_path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v4_ace_core_20260923.yaml"
    )
    replay_path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v4_ace_core_ref_cur_20260923.yaml"
    )
    full = yaml.safe_load(full_path.read_text())
    replay = yaml.safe_load(replay_path.read_text())

    for path, raw in ((full_path, full), (replay_path, replay)):
        _validate_frozen_inputs(path.resolve(), raw)
        assert raw["status"] == "ready_not_launched"
        assert raw["readiness"] == {
            "runnable": True,
            "launched": False,
            "missing": [],
        }
        assert raw["inputs"]["prompt_bundle"].endswith(
            "offline_gepa_paired_levels_ace_core_v6_20260923.yaml"
        )
        assert raw["inputs"]["prompt_bundle_sha256"] == (
            "f5fd68e09c68ce4cfd03a0116ad6950ea4ec96de120d27716218699eab7c0e00"
        )
        assert raw["reflection"] == {
            "rounds": 1,
            "structured_recovery": True,
            "structured_abstraction": True,
            "distilled_curation": True,
        }
        assert raw["curation"] == {
            "evidence_contract": "distilled_v1",
            "require_concern_coverage": False,
        }
        assert raw["search"]["max_iterations"] == 1
        assert raw["search"]["reflection_minibatch_size"] == 12
        assert len(raw["inputs"]["train_instance_ids"]) == 12
        assert len(raw["inputs"]["validation_instance_ids"]) == 4
        assert raw["models"]["checker"]["thinking"] == "disabled"
        assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
        assert raw["hpc"]["agent_time"] == "00:10:00"
        assert raw["hpc"]["poll_interval_seconds"] == 60
        assert raw["hpc"]["max_running_array_tasks"] == 0

    assert full["inputs"]["initial_playbook"].endswith(
        "paired_concern_manual_audit_v1_20260923.json"
    )
    assert "checkpoint_import" not in full
    assert replay["inputs"]["initial_playbook"].endswith(
        "paired_concern_seed_v3_categorized.json"
    )
    assert replay["checkpoint_import"]["roles"] == ["paired_repo_checker"]

    supervisor_path = Path(
        "configs/gepa_verified_paired_learning12_smoke_v4_ace_core_sequence_supervisor_20260923.yaml"
    )
    supervisor = yaml.safe_load(supervisor_path.read_text())
    assert supervisor["program"] == "resume_sequence"
    assert [run["name"] for run in supervisor["runs"]] == [
        "controlled-reflector-curator-replay",
        "manual-playbook-full-cycle",
    ]
    configs = []
    for run in supervisor["runs"]:
        args = run["arguments"]
        configs.append(args[args.index("--gepa-config") + 1])
        assert args[args.index("--poll-interval") + 1] == "60"
        assert args[args.index("--target-iterations") + 1] == "1"
        assert args[args.index("--max-runs") + 1] == "12"
        assert args[args.index("--cpus") + 1] == "1"
        assert args[args.index("--mem") + 1] == "4G"
        assert "--require-clean-worktree" in args
        assert "--reclaim-staging" in args
        assert "--reclaim-workspaces" in args
    assert configs == [str(replay_path), str(full_path)]


def test_v4_smoke_worker_uses_distilled_reflector_and_curator_tasks(
    tmp_path, monkeypatch
):
    from src.optimization import playbook_worker

    config = Path(
        "configs/gepa_verified_paired_learning12_smoke_v4_ace_core_20260923.yaml"
    ).resolve()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    repository = {
        "repo": "org/repo",
        "base_commit": "abc",
        "instance_id": "org__repo-1",
    }
    authority = {
        "requested_ref": "image",
        "sif_path": "/cache/image.sif",
        "sif_sha256": "0" * 64,
        "sif_bytes": 1,
    }
    seen = {}
    reflection = {
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
    }

    def fake_reflector(**kwargs):
        seen["reflector_task"] = kwargs["task"]
        return reflection, []

    monkeypatch.setattr(
        playbook_worker, "run_repository_reflector", fake_reflector
    )
    reflector_manifest = tmp_path / "reflector.json"
    reflector_manifest.write_text(json.dumps({
        "role": "paired_repo_reflector",
        "fingerprint": "test-reflector",
        "task_index": 0,
        "instance_id": "pair-a",
        "validation_playbook": book().serialize(),
        "repository": repository,
        "image_authority": authority,
        "evidence_dir": str(evidence),
        "source_access_issue": "Issue",
        "prompt_values": {"internal_playbook": book().serialize()},
    }))
    assert playbook_worker.run_task(
        config_path=config,
        manifest_path=reflector_manifest,
        output_path=tmp_path / "reflector-output.json",
        attempt_dir=tmp_path / "reflector-attempt",
    ) == 0
    assert seen["reflector_task"] == (
        "Analyze both completed Plan attempts and distill reusable "
        "Plan-review concerns."
    )

    def fake_curator(**kwargs):
        seen["curator_task"] = kwargs["task"]
        return {"reasoning": "No durable change.", "operations": []}, []

    monkeypatch.setattr(playbook_worker, "run_evidence_curator", fake_curator)
    curator_manifest = tmp_path / "curator.json"
    curator_manifest.write_text(json.dumps({
        "role": "curator",
        "fingerprint": "test-curator",
        "task_index": 0,
        "validation_playbook": book().serialize(),
        "evidence_dir": str(evidence),
        "prompt_values": {
            "counted_internal_playbook": book().serialize(),
            "case_count": 1,
        },
    }))
    monkeypatch.setattr(playbook_worker.litellm, "token_counter", lambda **_k: 4)
    assert playbook_worker.run_task(
        config_path=config,
        manifest_path=curator_manifest,
        output_path=tmp_path / "curator-output.json",
        attempt_dir=tmp_path / "curator-attempt",
    ) == 0
    assert seen["curator_task"] == (
        "Curate durable Plan-review concerns from the completed reflections."
    )


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
