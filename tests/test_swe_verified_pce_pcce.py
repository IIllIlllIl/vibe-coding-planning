from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from src.agents import plan_agent
from src.environment.source_access import SOURCE_ACCESS_POLICY_VERSION
from src.optimization.hpc.task_batch import TaskFiles
from src.optimization.checker import CheckerOutputContractError
from src.exceptions import AgentTaskError, FatalError
from src.swe_verified_pcce.config import load_swe_verified_pcce_config
from src.swe_verified_pcce.controller import (
    _allow_operational_semantic_migration,
    _load_first_review_seed,
    _review_assignments,
    run_swe_verified_pcce,
)
from src.swe_verified_pcce.dataset import load_pcce_cases
from src.swe_verified_pcce.hpc_executor import (
    _case_dict,
    build_array_script,
    execution_semantic_sha256,
)
from src.swe_verified_pcce.models import PCCECase
from src.swe_verified_pcce.worker import _retry_disposition
from src.swe_verified_pce.dataset import (
    canonical_image_ref,
    file_sha256,
    load_swe_verified_pce_cases,
)
from src.swe_verified_pce.config import load_swe_verified_pce_config
from src.swe_verified_pce.evaluator import (
    _apply_patch,
    _terminal,
    evaluate_swe_verified_apptainer,
)
from src.swe_verified_pce.evaluator_resume import _prepare as prepare_evaluator_resume
from src.swe_verified_pce.hpc_executor import (
    SWEVerifiedPCEHPCExecutor,
    build_array_script as build_pce_array_script,
    recover_exhausted_evaluator_timeout,
)
from src.swe_verified_pce.models import FrozenImage, SWEVerifiedPCECase
from src.swe_verified_pce.runner import checkpoint_identity
from src.swe_verified_pce.plan_replay import _RecoveredPlanExecutor
from src.swe_verified_pce.runner import SWEVerifiedPCERunner
from src.swe_verified_pce.worker import _retry_disposition as pce_retry_disposition
from src.swe_verified_pcce.runner import SWEVerifiedPCCERunner
from scripts.tools.freeze_pcce_rejected_first_reviews import (
    freeze_rejected_first_reviews,
)
from scripts.tools.freeze_swe_verified_pce_selection import freeze_selection
from scripts import resume_swe_verified_pce_evaluator as evaluator_resume_script


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_safe_pce_only_shared_input_failures_block_the_run() -> None:
    error = FatalError("case-local repository preparation failed")
    assert pce_retry_disposition(error, stage="pce_execution") == "retry_same_phase"
    assert pce_retry_disposition(error, stage="config_load") == "block_run"


def test_safe_pce_config_does_not_import_optional_gepa_runtime() -> None:
    result = subprocess.run(
        [
            "python",
            "-c",
            (
                "import sys; import src.swe_verified_pce.config; "
                "assert 'gepa' not in sys.modules"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_safe_pce_submit_preserves_scientific_run_git_identity() -> None:
    script = Path("scripts/hpc_submit_swe_verified_pce.sh").read_text(
        encoding="utf-8"
    )
    assert 'export VIBE_CONTROLLER_GIT_HEAD="$LOCAL_GIT_HEAD"' in script
    assert 'RUN_MANIFEST="$RUN_REL/run_manifest.json"' in script
    assert 'if [[ -f "\\$RUN_MANIFEST" ]]; then' in script
    assert 'export VIBE_PROJECT_GIT_HEAD' in script


def test_recovered_plan_executor_preloads_identity_bound_plan_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    case = SWEVerifiedPCECase(
        instance_id="owner__repo-1",
        row_sha256="a" * 64,
        issue_description="issue",
        repo="owner/repo",
        base_commit="b" * 40,
        version="",
        difficulty="",
        environment_setup_commit="",
        test_patch="tests",
        fail_to_pass=("test_x",),
        pass_to_pass=(),
        gold_patch="gold",
        image=FrozenImage(
            requested_ref="image",
            sif_path="/sif",
            sif_sha256="c" * 64,
            sif_bytes=1,
            provenance_strength="retrospective",
        ),
        source_row={},
    )
    task = TaskFiles(
        0,
        case.instance_id,
        tmp_path / "tasks" / "task_0000.json",
        tmp_path / "outputs" / "task_0000.json",
        tmp_path / "attempts" / "task_0000",
    )
    monkeypatch.setattr(
        SWEVerifiedPCEHPCExecutor,
        "_prepare",
        lambda self, batch_dir, fingerprint, cases: [task],
    )
    executor = object.__new__(_RecoveredPlanExecutor)
    executor.plans = {case.instance_id: "# Plan\nImplement the focused fix."}
    executor._prepare(tmp_path, "d" * 64, [case])
    checkpoint = json.loads((tmp_path / "checkpoints/task_0000/plan.json").read_text())
    assert checkpoint["phase"] == "plan"
    assert checkpoint["payload"] == {
        "plan": "# Plan\nImplement the focused fix.",
        "trajectory": [],
        "source": "frozen_recovered_plan",
    }
    assert checkpoint["checkpoint_identity"] == checkpoint_identity(
        case, execution_fingerprint="d" * 64
    )


def test_recovered_plan_ce2_config_binds_two_plans_and_supervisor() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_recovered_plan_ce2_v1_20260911.yaml",
        require_api_keys=False,
    )
    replay_path = Path(
        "configs/frozen_swe_verified_recovered_plan_ce/"
        "verified-train10-shell-recovery-ce2-v1-20260911/replay.json"
    )
    replay = json.loads(replay_path.read_text())
    assert [row["instance_id"] for row in replay["recovered_plans"]] == list(
        config.instance_ids
    )
    assert replay["image_manifest_sha256"] == file_sha256(config.image_manifest)
    assert all(row["plan_sha256"] for row in replay["recovered_plans"])
    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_verified_recovered_plan_ce2_supervisor_v1_20260911.yaml"
        ).read_text()
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--batch-script") + 1] == (
        "scripts/hpc_submit_swe_verified_plan_ce_replay.sh"
    )
    assert "--require-clean-worktree" in arguments


def test_safe_pce_smoke_uses_direct_plan_prompt_and_retained_code_prompt() -> None:
    safe = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_smoke_v1_20260911.yaml",
        require_api_keys=False,
    )
    retained = load_swe_verified_pce_config(
        "configs/swe_verified_pce_smoke_v1.yaml",
        require_api_keys=False,
    )

    assert "FINAL_PLAN" in safe.plan_prompt
    assert "/tmp" not in safe.plan_prompt
    assert "/opt/miniconda3/envs/testbed/bin/python" in safe.plan_prompt
    assert "Remote Git operations" in safe.plan_prompt
    assert "/opt/miniconda3/envs/testbed/bin/python" in safe.code_prompt
    assert "Remote Git operations" in safe.code_prompt
    assert safe.plan_submission_protocol == "direct_final_plan_v1"
    assert retained.plan_submission_protocol == "legacy_stdout_v1"
    assert safe.code_prompt == retained.code_prompt
    assert safe.code_instance_template == retained.code_instance_template
    assert safe.nrpv_block == retained.nrpv_block
    assert safe.instance_ids == retained.instance_ids
    assert safe.run_dir != retained.run_dir
    assert safe.hpc.worker_config_path.endswith(
        "swe_verified_safe_pce_smoke_v1_20260911.yaml"
    )


def test_safe_pce_planner_v2_targets_human_review_and_exact_submission() -> None:
    prompt = yaml.safe_load(
        Path(
            "configs/prompts/swe_verified_safe_pce_planner_v2_20260912.yaml"
        ).read_text(encoding="utf-8")
    )["prompts"]["plan_system"]

    assert "planning mode with a human developer" in prompt
    assert "developer to review before any implementation begins" in prompt
    assert "has not inspected this repository" in prompt
    assert "Treat `/testbed` as the repository authority" in prompt
    assert "Keep the Plan concise and easy to scan" in prompt
    assert "It may later guide a coding agent" not in prompt
    assert "exactly follows the demonstrated terminal-response structure" in prompt


def test_safe_pce_planner_v4_uses_claude_style_search_and_no_nrpv() -> None:
    prompt = yaml.safe_load(
        Path(
            "configs/prompts/swe_verified_safe_pce_planner_v4_20260913.yaml"
        ).read_text(encoding="utf-8")
    )["prompts"]["plan_system"]
    normalized = " ".join(prompt.split())

    assert "Investigate only far enough" in prompt
    assert "smallest relevant implementation path" in normalized
    assert "reproduction" not in prompt.lower()
    assert "The Plan is ready when" in prompt
    assert "coherent, repository-grounded approach" in normalized
    assert "approve, question, or redirect" in normalized
    assert "Include only details that are material to that decision" in normalized
    assert "It should:" not in prompt
    assert "Before another exploratory command" in normalized
    assert "under `/opt/miniconda3/envs`" in normalized
    assert "For a small, well-localized issue" in normalized
    assert "For a cross-cutting issue" in normalized
    assert "state the uncertainty precisely" in normalized
    assert "Remote Git operations" not in prompt
    assert "{{nrpv_block}}" not in prompt
    assert "Navigation (N)" not in prompt
    assert "Organize the Plan to" in prompt
    assert "instead of following a fixed section template" in normalized


def test_safe_pce_planner_v5_uses_template_and_revised_planning_boundaries() -> None:
    v5 = yaml.safe_load(
        Path(
            "configs/prompts/swe_verified_safe_pce_planner_v5_20260913.yaml"
        ).read_text(encoding="utf-8")
    )["prompts"]["plan_system"]
    normalized = " ".join(v5.split())

    assert "FINAL_PLAN" not in v5
    assert "[[TASK_SPECIFIC_MARKDOWN_PLAN]]" not in v5
    assert "Navigation (N)" not in v5
    assert "Reproduction (R)" not in v5
    assert "Investigate only until" in v5
    assert "smallest implementation path needed to explain the issue" in normalized
    assert "Use the frozen repository and the environment already provided" in v5
    assert (
        "replace the target repository with a remotely obtained version" in normalized
    )
    assert "Remote Git operations" in normalized
    assert "local Git history already present at the frozen base commit" in normalized
    assert "materially affects another behavior or must preserve it" in normalized
    assert "make that responsibility explicit in the Plan" in normalized
    assert "Do not modify repository files while planning" not in v5


def test_safe_pce_config_accepts_template_markdown_protocol(tmp_path: Path) -> None:
    source = Path("configs/swe_verified_safe_pce_audit10_v8_claude_plan_20260913.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["plan"]["submission_protocol"] = "direct_final_markdown_v3"
    path = tmp_path / "template-protocol.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    config = load_swe_verified_pce_config(path, require_api_keys=False)

    assert config.plan_submission_protocol == "direct_final_markdown_v3"


def test_safe_pce_config_accepts_bounded_markdown_protocol(tmp_path: Path) -> None:
    source = Path("configs/swe_verified_safe_pce_audit10_v8_claude_plan_20260913.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["plan"]["submission_protocol"] = "direct_final_markdown_v4"
    path = tmp_path / "bounded-template-protocol.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    config = load_swe_verified_pce_config(path, require_api_keys=False)

    assert config.plan_submission_protocol == "direct_final_markdown_v4"


def test_safe_pce_config_accepts_human_bounded_markdown_protocol(
    tmp_path: Path,
) -> None:
    source = Path("configs/swe_verified_safe_pce_audit10_v8_claude_plan_20260913.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["plan"]["submission_protocol"] = "direct_human_markdown_v5"
    path = tmp_path / "human-bounded-template-protocol.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    config = load_swe_verified_pce_config(path, require_api_keys=False)

    assert config.plan_submission_protocol == "direct_human_markdown_v5"


def test_safe_pce_audit10_v8_binds_flexible_markdown_smoke() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_audit10_v8_claude_plan_20260913.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]

    assert len(config.instance_ids) == 10
    assert config.run_dir.name == "safe-pce-audit10-v8-claude-plan-20260913"
    assert config.plan_submission_protocol == "direct_final_markdown_v2"
    assert "{{nrpv_block}}" not in config.plan_prompt
    assert "Navigation (N)" not in config.plan_prompt
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert contract["comparison_run"] == "safe-pce-audit10-v7-20260913"
    assert contract["launched"] is True

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_audit10_v8_claude_plan_"
            "supervisor_v1_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_audit10_v8_claude_plan_20260913.yaml"
    )


def test_safe_pce_audit10_v9_binds_revised_prompt_diagnostic() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_audit10_v9_claude_plan_20260913.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]

    assert len(config.instance_ids) == 10
    assert config.run_dir.name == "safe-pce-audit10-v9-claude-plan-20260913"
    assert config.plan_submission_protocol == "direct_final_markdown_v3"
    assert "Investigate only until" in config.plan_prompt
    assert "materially affects another behavior" in config.plan_prompt
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert contract["source_audit_disposition"] == (
        "no_training_authority_until_case_level_review"
    )
    assert contract["launched"] is True

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_audit10_v9_claude_plan_"
            "supervisor_v1_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_audit10_v9_claude_plan_20260913.yaml"
    )


def test_safe_pce_anchor_ablation4_v10_changes_only_planning_guidance() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_anchor_ablation4_v10_20260913.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]
    selection = json.loads(config.selection_manifest.read_text(encoding="utf-8"))
    images = json.loads(config.image_manifest.read_text(encoding="utf-8"))

    assert config.instance_ids == (
        "pallets__flask-5014",
        "scikit-learn__scikit-learn-12973",
        "matplotlib__matplotlib-20488",
        "sympy__sympy-12419",
    )
    assert config.run_dir.name == "safe-pce-anchor-ablation4-v10-20260913"
    assert config.plan_submission_protocol == "direct_final_markdown_v3"
    assert "state the uncertainty precisely" in config.plan_prompt
    assert "smallest implementation path" not in config.plan_prompt
    assert "Investigate only until" not in config.plan_prompt
    assert "localized issues" not in config.plan_prompt
    assert "cross-cutting issues" not in config.plan_prompt
    assert "make that responsibility explicit" not in config.plan_prompt
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert images["selection_manifest_sha256"] == file_sha256(config.selection_manifest)
    assert len(images["records"]) == 4
    assert set(config.instance_ids) == {
        record["instance_id"] for record in images["records"].values()
    }
    assert selection["selection_policy"]["outcome_exposed"] is True
    assert contract["comparison_run"] == ("safe-pce-audit10-v9-claude-plan-20260913")
    assert contract["held_constant_known_defects"] == [
        "direct_final_markdown_v3_provider_protocol_residue",
        "conservative_blacklist_v2_with_known_source_audit_gap",
    ]

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_anchor_ablation4_v10_"
            "supervisor_v1_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_anchor_ablation4_v10_20260913.yaml"
    )


def test_safe_pce_terminal10_v11_combines_selected_final_contracts() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_terminal10_v11_20260913.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]

    assert len(config.instance_ids) == 10
    assert config.run_dir.name == "safe-pce-terminal10-v11-20260913"
    assert config.plan_submission_protocol == "direct_final_markdown_v4"
    assert "state the uncertainty precisely" in config.plan_prompt
    assert "smallest implementation path" not in config.plan_prompt
    assert "Investigate only until" not in config.plan_prompt
    assert "localized issues" not in config.plan_prompt
    assert "cross-cutting issues" not in config.plan_prompt
    assert "make that responsibility explicit" not in config.plan_prompt
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_instance_template.encode()).hexdigest()
        == (contract["code_instance_prompt_text_sha256"])
    )
    combined_code_prompt = config.code_prompt + config.code_instance_template
    assert "already provided in the SIF" in combined_code_prompt
    assert "If a command isn't available, you can install it" not in (
        combined_code_prompt
    )
    assert "pip install" not in combined_code_prompt
    assert contract["agent_source_policy"] == SOURCE_ACCESS_POLICY_VERSION
    assert contract["source_audit_index"] == (
        "all_attempts_case_level_summary_plus_event_logs"
    )
    assert contract["status"] == "prepared_not_launched"
    assert contract["launched"] is False

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_terminal10_v11_supervisor_v1_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_terminal10_v11_20260913.yaml"
    )


def test_safe_pce_human_boundary3_v12_freezes_semantic_boundary_rerun() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_human_boundary3_v12_20260913.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]
    images = json.loads(config.image_manifest.read_text(encoding="utf-8"))

    assert config.instance_ids == (
        "django__django-10097",
        "pytest-dev__pytest-10051",
        "django__django-10554",
    )
    assert config.run_dir.name == "safe-pce-human-boundary3-v12-20260913"
    assert config.plan_submission_protocol == "direct_human_markdown_v5"
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:45:00"
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(
            plan_agent.HUMAN_BOUNDED_MARKDOWN_PLAN_ACTION_PROTOCOL.encode()
        ).hexdigest()
        == contract["plan_action_protocol_text_sha256"]
    )
    assert images["selection_manifest_sha256"] == file_sha256(config.selection_manifest)
    image_identity_payload = dict(images)
    image_identity = image_identity_payload.pop("manifest_id")
    assert (
        image_identity
        == hashlib.sha256(
            json.dumps(
                image_identity_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert len(images["records"]) == 3
    assert set(config.instance_ids) == {
        record["instance_id"] for record in images["records"].values()
    }
    assert contract["plan_authority"] == (
        "exact_model_text_between_START_PLAN_and_END_PLAN"
    )
    assert contract["status"] == "launch_authorized"
    assert contract["launched"] is True

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_human_boundary3_v12_"
            "supervisor_v1_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_human_boundary3_v12_20260913.yaml"
    )


def test_safe_pce_formal500_v1_freezes_full_input_and_60_minute_budget(
    tmp_path: Path,
) -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_formal500_v1_20260914.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]
    input_contract_path = REPO_ROOT / contract["input_contract"]
    input_contract = json.loads(input_contract_path.read_text(encoding="utf-8"))
    source_manifest = json.loads(
        (config.dataset_snapshot / "manifest.json").read_text(encoding="utf-8")
    )
    source_rows = [
        json.loads(line)
        for line in (config.dataset_snapshot / "instances.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert config.selection_manifest is None
    assert config.instance_ids == ()
    assert source_manifest["instances"] == len(source_rows) == 500
    assert len({row["instance_id"] for row in source_rows}) == 500
    assert (
        file_sha256(config.dataset_snapshot / "manifest.json")
        == contract["source_manifest_sha256"]
    )
    assert (
        file_sha256(config.dataset_snapshot / "instances.jsonl")
        == contract["instances_sha256"]
    )
    assert file_sha256(input_contract_path) == contract["input_contract_sha256"]
    assert input_contract["source_instances"] == 500
    assert input_contract["execution_universe"]["policy"] == (
        "all_rows_in_frozen_source_order"
    )
    assert input_contract["preparation_census"]["expected_sifs_present"] == 483
    assert input_contract["preparation_census"]["expected_sifs_missing"] == 17
    assert len(input_contract["preparation_census"]["missing_instance_ids"]) == 17
    assert (
        input_contract["preparation_census"]["expected_sifs_present"]
        + input_contract["preparation_census"]["expected_sifs_missing"]
        == 500
    )
    assert set(input_contract["preparation_census"]["missing_instance_ids"]) <= {
        row["instance_id"] for row in source_rows
    }
    assert set(input_contract["preparation_census"]["missing_images"]) == {
        canonical_image_ref(instance_id)
        for instance_id in input_contract["preparation_census"]["missing_instance_ids"]
    }
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "01:00:00"
    assert config.hpc.max_task_attempts == 3
    assert config.plan.timeout == config.code.timeout == 1800
    assert config.plan_submission_protocol == "direct_human_markdown_v5"
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.plan_instance_template.encode()).hexdigest()
        == (contract["plan_instance_prompt_text_sha256"])
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_instance_template.encode()).hexdigest()
        == (contract["code_instance_prompt_text_sha256"])
    )
    assert contract["selected_cases"] == 500
    assert contract["status"] == "prepared_pending_full_sif_audit"
    assert contract["launched"] is False
    assert (
        contract["postprocessing"]["scan_actual_executions_not_only_model_proposals"]
        is True
    )
    assert contract["postprocessing"]["inspect_every_actually_executed_url"] is True
    assert contract["postprocessing"]["preserve_raw_artifacts"] is True

    array_script = build_pce_array_script(
        config=config,
        batch_dir=tmp_path / "formal500-array",
        indices=[0, 499],
        attempt=1,
    )
    assert "#SBATCH --array=0,499" in array_script
    assert "#SBATCH --cpus-per-task=1" in array_script
    assert "#SBATCH --mem=4G" in array_script
    assert "#SBATCH --time=01:00:00" in array_script

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_formal500_v1_supervisor_20260914.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--max-runs") + 1] == "12"
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_formal500_v1_20260914.yaml"
    )


def test_safe_pce_formal482_v1_binds_complete_selection_and_images(
    tmp_path: Path,
) -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_formal482_v1_20260914.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]
    assert config.selection_manifest is not None
    selection = json.loads(config.selection_manifest.read_text(encoding="utf-8"))
    images = json.loads(config.image_manifest.read_text(encoding="utf-8"))
    cases, _, _ = load_swe_verified_pce_cases(
        config.dataset_snapshot,
        config.image_manifest,
    )

    assert len(config.instance_ids) == len(cases) == 482
    assert tuple(case.instance_id for case in cases) == config.instance_ids
    assert list(config.instance_ids) == selection["selected_instance_ids"]
    assert images["summary"] == {
        "audited": 482,
        "base_commit_verified": 482,
        "missing": 0,
        "records": 482,
    }
    assert images["selection_manifest_sha256"] == file_sha256(
        config.selection_manifest
    )
    assert file_sha256(config.selection_manifest) == (
        contract["selection_manifest_sha256"]
    )
    assert file_sha256(config.image_manifest) == contract["image_manifest_sha256"]
    assert images["manifest_id"] == contract["image_manifest_id"]
    assert hashlib.sha256(config.plan_prompt.encode()).hexdigest() == (
        contract["plan_prompt_text_sha256"]
    )
    assert hashlib.sha256(config.plan_instance_template.encode()).hexdigest() == (
        contract["plan_instance_prompt_text_sha256"]
    )
    assert hashlib.sha256(config.code_prompt.encode()).hexdigest() == (
        contract["code_prompt_text_sha256"]
    )
    assert hashlib.sha256(config.code_instance_template.encode()).hexdigest() == (
        contract["code_instance_prompt_text_sha256"]
    )
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "01:00:00"
    assert config.hpc.max_task_attempts == 3
    assert config.plan.timeout == config.code.timeout == 1800
    assert contract["selected_cases"] == contract["budget"]["cases"] == 482
    assert contract["status"] == "launch_authorized"
    assert contract["launched"] is False
    assert all(contract["prelaunch_gate"].values())

    array_script = build_pce_array_script(
        config=config,
        batch_dir=tmp_path / "formal482-array",
        indices=[0, 481],
        attempt=1,
    )
    assert "#SBATCH --array=0,481" in array_script
    assert "#SBATCH --cpus-per-task=1" in array_script
    assert "#SBATCH --mem=4G" in array_script
    assert "#SBATCH --time=01:00:00" in array_script

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_formal482_v1_"
            "supervisor_20260914.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--max-runs") + 1] == "12"
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_formal482_v1_20260914.yaml"
    )

    recovery = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_formal482_v1_"
            "resume1_supervisor_20260914.yaml"
        ).read_text(encoding="utf-8")
    )
    recovery_arguments = recovery["arguments"]
    assert recovery_arguments[
        recovery_arguments.index("--recover-controller-error-type-once") + 1
    ] == "TaskBatchBlocked"
    assert recovery_arguments[recovery_arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_formal482_v1_20260914.yaml"
    )
    assert recovery_arguments[recovery_arguments.index("--state-file") + 1].endswith(
        "formal482-v1-resume1-20260914.json"
    )


def test_safe_pce_rejects_unreviewed_worker_walltime(tmp_path: Path) -> None:
    raw = yaml.safe_load(
        Path("configs/swe_verified_safe_pce_formal500_v1_20260914.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw["hpc"]["time"] = "00:50:00"
    config_path = tmp_path / "unreviewed-walltime.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed 45- or 60-minute walltime"):
        load_swe_verified_pce_config(config_path, require_api_keys=False)


def test_safe_pce_boundary_smoke_freezes_case8_and_git_boundary_probe() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_boundary_smoke_v2_20260911.yaml",
        require_api_keys=False,
    )

    assert config.instance_ids == (
        "astropy__astropy-13033",
        "django__django-10097",
    )
    assert config.plan_submission_protocol == "direct_final_plan_v1"
    assert config.run_dir.name == "safe-boundary-smoke-v2-20260911"
    assert config.hpc.worker_config_path.endswith(
        "swe_verified_safe_pce_boundary_smoke_v2_20260911.yaml"
    )
    assert "/opt/miniconda3/envs/testbed/bin/python" in config.plan_prompt
    assert "/opt/miniconda3/envs/testbed/bin/python" in config.code_prompt

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/"
            "swe_verified_safe_pce_boundary_smoke_supervisor_v3_20260911.yaml"
        ).read_text()
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_boundary_smoke_v2_20260911.yaml"
    )
    assert "--remote-dir" not in arguments
    assert "--remote-dataset-dir" not in arguments
    assert "--remote-run-dir" not in arguments


def test_safe_pce_audit10_freezes_balanced_diverse_development_cases() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_audit10_v3_20260912.yaml",
        require_api_keys=False,
    )
    selection = json.loads(config.selection_manifest.read_text(encoding="utf-8"))

    assert len(config.instance_ids) == 10
    assert len(set(config.instance_ids)) == 10
    assert config.instance_ids == tuple(selection["selected_instance_ids"])
    assert selection["selection_policy"]["selection_basis"] == (
        "distinct_safe_pce_failure_and_reasoning_risk_coverage"
    )
    assert (
        selection["selection_policy"]["historical_outcomes_used_as_runtime_inputs"]
        is False
    )
    assert selection["selection_policy"]["repository_count"] == 9
    wrappers = [
        json.loads(line)
        for line in (config.dataset_snapshot / "instances.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    source = {wrapper["instance_id"]: wrapper for wrapper in wrappers}
    selected_rows = {row["instance_id"]: row for row in selection["selected_rows"]}
    assert set(config.instance_ids) <= set(source)
    assert all(
        source[instance_id]["row_sha256"] == selected_rows[instance_id]["row_sha256"]
        for instance_id in config.instance_ids
    )
    cases, _, images = load_swe_verified_pce_cases(
        config.dataset_snapshot,
        config.image_manifest,
    )
    loaded = {case.instance_id for case in cases}
    assert set(config.instance_ids) <= loaded
    assert len(images["records"]) == 10
    assert all(record["base_commit_verified"] for record in images["records"].values())
    assert images["manifest_id"] == (
        "beaa8feaa1c852f05ebf26b856cafe74f0f6adfa06ed37a3d99aa264676f26b9"
    )
    assert config.plan_submission_protocol == "direct_final_plan_v1"
    assert config.run_dir.name == "safe-pce-audit10-v3-20260912"

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_audit10_supervisor_v1_20260912.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--reclaim-staging" in arguments
    assert "--require-clean-worktree" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_audit10_v3_20260912.yaml"
    )
    assert "--remote-dir" not in arguments
    assert "--remote-dataset-dir" not in arguments
    assert "--remote-run-dir" not in arguments


def test_safe_pce_audit10_v5_prepares_source_boundary_replay() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_audit10_v5_20260912.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    assert len(config.instance_ids) == 10
    assert config.run_dir.name == "safe-pce-audit10-v5-20260912"
    assert raw["experiment_contract"]["agent_source_policy"] == (
        "conservative_blacklist_v1"
    )
    assert raw["experiment_contract"]["launched"] is True

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_audit10_supervisor_v3_20260912.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_audit10_v5_20260912.yaml"
    )


def test_safe_pce_audit10_v6_uses_configured_baseline_timeout() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_audit10_v6_20260912.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    assert len(config.instance_ids) == 10
    assert config.plan.timeout == config.code.timeout == 1800
    assert raw["experiment_contract"]["repository_baseline_command_timeout"] == (
        "inherit_phase_1800_seconds"
    )
    assert raw["experiment_contract"]["launched"] is True

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/"
            "swe_verified_safe_pce_audit10_supervisor_v4_20260912.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_audit10_v6_20260912.yaml"
    )


def test_safe_pce_audit10_v7_freezes_repaired_boundary_smoke() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_audit10_v7_20260913.yaml",
        require_api_keys=False,
    )
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8"))
    contract = raw["experiment_contract"]
    assert len(config.instance_ids) == 10
    assert config.run_dir.name == "safe-pce-audit10-v7-20260913"
    assert contract["agent_source_policy"] == "conservative_blacklist_v2"
    assert contract["shell_command_parser"] == "bashlex_ast"
    assert contract["plan_audience"] == "human_developer_before_implementation"
    assert contract["evaluator_repository_policy"] == (
        "verify_fresh_immutable_sif_without_reset_or_clean"
    )
    assert (
        hashlib.sha256(config.plan_prompt.encode()).hexdigest()
        == contract["plan_prompt_text_sha256"]
    )
    assert (
        hashlib.sha256(config.code_prompt.encode()).hexdigest()
        == contract["code_prompt_text_sha256"]
    )
    assert contract["launched"] is True

    supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_audit10_supervisor_v5_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert "--require-clean-worktree" in arguments
    assert "--reclaim-staging" in arguments
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_safe_pce_audit10_v7_20260913.yaml"
    )

    repair_supervisor = yaml.safe_load(
        Path(
            "configs/swe_verified_safe_pce_audit10_v7_evaluator_repair_"
            "supervisor_v1_20260913.yaml"
        ).read_text(encoding="utf-8")
    )
    repair_arguments = repair_supervisor["arguments"]
    assert repair_arguments[repair_arguments.index("--evaluator-repair-id") + 1] == (
        "safe-pce-audit10-v7-evalfix-v1-20260913"
    )
    assert repair_arguments.count("--evaluator-repair-instance") == 8
    assert "django__django-10554" not in repair_arguments
    assert "sympy__sympy-12419" not in repair_arguments


def test_swe_verified_checker_contract_failure_retries_with_fresh_agent():
    error = CheckerOutputContractError("extra data after submitted JSON")
    assert _retry_disposition(error) == "retry_fresh_agent"


@pytest.mark.parametrize("reason", ["plan_invalid_markdown", "code_empty_patch"])
def test_safe_pce_agent_contract_failure_retries_with_fresh_agent(reason: str):
    error = AgentTaskError("invalid Agent submission", phase="plan", reason=reason)
    assert _retry_disposition(error) == "retry_fresh_agent"


def test_safe_pce_authority_paths_are_relative_to_run_root(tmp_path: Path):
    runner = object.__new__(SWEVerifiedPCERunner)
    runner.config = SimpleNamespace(run_dir=tmp_path / "run")
    retained = tmp_path / "run" / "hpc_tasks" / "pce" / "fp" / "plan.json"

    assert runner._authority_relative_path(retained) == ("hpc_tasks/pce/fp/plan.json")
    with pytest.raises(FatalError, match="outside the canonical run"):
        runner._authority_relative_path(tmp_path / "staging" / "plan.json")


def test_safe_pce_completed_result_preserves_bounded_raw_plan_submission():
    raw_submission = (
        "FINAL_PLAN\n# Plan\n\nChange the parser.\nEND_PLAN\n"
        "Provider trailer.</parameter>"
    )
    result = SWEVerifiedPCERunner._completed_result(
        {
            "plan": "# Plan\n\nChange the parser.\n",
            "plan_sha256": "plan-hash",
            "trajectory": [],
            "raw_plan_submission": raw_submission,
            "raw_plan_submission_sha256": "raw-hash",
            "plan_boundary": {
                "start_marker": "FINAL_PLAN",
                "end_marker": "END_PLAN",
            },
        },
        {
            "patch": "patch",
            "patch_submission": {"patch_sha256": "patch-hash"},
            "workspace_evidence": {},
            "trajectory": [],
        },
        {"evaluator_result": {"terminal_kind": "official_tests_resolved"}},
    )

    assert result["plan"] == "# Plan\n\nChange the parser.\n"
    assert result["raw_plan_submission"] == raw_submission
    assert result["raw_plan_submission_sha256"] == "raw-hash"
    assert result["plan_boundary"] == {
        "start_marker": "FINAL_PLAN",
        "end_marker": "END_PLAN",
    }


def test_safe_pce_indexes_source_access_across_agent_attempts(tmp_path: Path):
    run_dir = tmp_path / "run"
    attempts_dir = run_dir / "attempts" / "task_0000"
    first = attempts_dir / "attempt_01" / "source_access.jsonl"
    second = attempts_dir / "attempt_02" / "source_access.jsonl"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(
        json.dumps(
            {
                "decision": "block",
                "reason": "pip_remote",
                "client": "install",
                "phase": "plan",
                "urls": [],
                "prompt_url_match": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    second.write_text("", encoding="utf-8")
    executor = object.__new__(SWEVerifiedPCEHPCExecutor)
    executor.config = SimpleNamespace(run_dir=run_dir)
    task = SimpleNamespace(index=0, attempts_dir=attempts_dir)

    indexed = executor._attach_source_access_attempt_index(
        [{"task_index": 0, "status": "completed"}], [task]
    )

    audit = indexed[0]["source_access"]
    assert audit["all_attempts_summary"]["blocked_event_count"] == 1
    assert audit["all_attempts_summary"]["manual_review_recommended"] is True
    assert [item["attempt"] for item in audit["attempts"]] == [
        "attempt_01",
        "attempt_02",
    ]


def test_swe_verified_agent_environments_isolate_tmp(tmp_path, monkeypatch):
    pce_observed = {}
    pcce_observed = {}

    class FakePCEEnvironment:
        def __init__(self, **kwargs):
            pce_observed.update(kwargs)

    monkeypatch.setattr(
        "src.swe_verified_pce.runner.ApptainerEnvironment", FakePCEEnvironment
    )
    pce = object.__new__(SWEVerifiedPCERunner)
    pce.config = SimpleNamespace(
        docker=SimpleNamespace(workdir="/testbed"),
        container=SimpleNamespace(sif_cache_dir=tmp_path, writable_tmpfs=True),
    )
    pce.capacity_window = SimpleNamespace()
    pce.source_access_path = tmp_path / "source_access.jsonl"
    source = SimpleNamespace(
        image=SimpleNamespace(requested_ref="image:v1"),
        instance_id="example__repo-1",
        issue_description="See https://docs.example/page",
    )
    pce._environment(source, timeout=10, phase="plan", host_workdir=tmp_path / "plan")

    class FakePCCEEnvironment:
        def __init__(self, **kwargs):
            pcce_observed.update(kwargs)

    monkeypatch.setattr(
        "src.swe_verified_pcce.runner.ApptainerEnvironment", FakePCCEEnvironment
    )
    pcce = object.__new__(SWEVerifiedPCCERunner)
    pcce.config = SimpleNamespace(
        dataset_type="verified",
        pce=SimpleNamespace(
            docker=SimpleNamespace(workdir="/testbed"),
            container=SimpleNamespace(sif_cache_dir=tmp_path, writable_tmpfs=True),
            plan=SimpleNamespace(timeout=10),
        ),
    )
    pcce.capacity = SimpleNamespace()
    assignment = SimpleNamespace(case=SimpleNamespace(source=source))
    pcce._environment(assignment, host_workdir=tmp_path / "checker")

    assert pce_observed["run_args"] == ["--containall", "--no-mount", "cwd"]
    assert pce_observed["isolate_tmp"] is True
    assert pce_observed["masked_container_paths"] == ["/opt/miniconda3/pkgs"]
    assert pce_observed["source_access_prompt_urls"] == ["https://docs.example/page"]
    assert pce_observed["source_access_context"]["phase"] == "plan"
    assert pcce_observed["run_args"] == ["--containall", "--no-mount", "cwd"]
    assert pcce_observed["isolate_tmp"] is True
    assert pcce_observed["masked_container_paths"] == ["/opt/miniconda3/pkgs"]


def test_verified_revision_plan_config_uses_canonical_dataset_without_pce_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_c5_prompt_v2_safe_u8_v1_20260909.yaml",
        require_api_keys=False,
    )
    monkeypatch.setenv(config.pce.plan.api_key_env, "test-placeholder")
    runner = SWEVerifiedPCCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    )

    plan_config = runner._plan_config()

    assert plan_config.system.dataset == "SWE-bench/SWE-bench_Verified"
    assert plan_config.system.dataset_type == "swe_verified"


def test_explicit_operational_migration_allows_only_semantic_code_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_git = "a" * 40
    new_git = "b" * 40
    old_semantic = "c" * 64
    new_semantic = "d" * 64
    config = SimpleNamespace(run_dir=tmp_path)
    existing = {
        "project_git_head": old_git,
        "pcce_semantic_sha256": old_semantic,
        "config_sha256": "frozen",
    }
    proposed = {**existing, "pcce_semantic_sha256": new_semantic}
    monkeypatch.setenv("VIBE_OPERATIONAL_MIGRATION_FROM_GIT_HEAD", old_git)
    monkeypatch.setenv(
        "VIBE_OPERATIONAL_MIGRATION_FROM_PCCE_SEMANTIC_SHA256", old_semantic
    )
    monkeypatch.setenv("VIBE_CONTROLLER_GIT_HEAD", new_git)

    assert _allow_operational_semantic_migration(config, existing, proposed)
    record = json.loads((tmp_path / "operational_code_migrations.jsonl").read_text())
    assert record["scientific_project_git_head"] == old_git
    assert record["controller_project_git_head"] == new_git
    assert record["semantic_inputs_changed"] is False

    with pytest.raises(ValueError, match="non-code identity changes"):
        _allow_operational_semantic_migration(
            config, existing, {**proposed, "config_sha256": "changed"}
        )


def test_operational_migration_preserves_frozen_task_fingerprint_semantic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = "c" * 64
    monkeypatch.setenv("VIBE_OPERATIONAL_MIGRATION_FROM_PCCE_SEMANTIC_SHA256", prior)
    assert execution_semantic_sha256(SimpleNamespace()) == prior


def _stable(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_swe_verified_accepts_issue_first_revision_prompt_source(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    original_path = root / "configs/swe_verified_pcce_quick50_c4_v1_20260902.yaml"
    original = load_swe_verified_pcce_config(original_path, require_api_keys=False)
    payload = yaml.safe_load(original_path.read_text(encoding="utf-8"))
    payload["paths"]["prompt_source_config"] = (
        "configs/pcce_issue_first_revision_prompt_v1_20260903.yaml"
    )
    candidate_path = tmp_path / "swe-verified-pcce-issue-first.yaml"
    candidate_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    candidate = load_swe_verified_pcce_config(
        candidate_path,
        require_api_keys=False,
    )

    assert candidate.checker_prompt == original.checker_prompt
    assert candidate.checker_instance_template == original.checker_instance_template
    assert candidate.plan_revision_prompt != original.plan_revision_prompt
    assert "The original issue is the objective" in candidate.plan_revision_prompt
    assert "Do not optimize for approval" in candidate.plan_revision_prompt
    assert "provided as advisory evidence" in candidate.plan_revision_prompt
    assert "Checker feedback as advisory evidence" in (
        candidate.plan_revision_instance_template
    )


def _row(instance_id: str = "owner__repo-1") -> dict[str, object]:
    return {
        "repo": "owner/repo",
        "instance_id": instance_id,
        "base_commit": "a" * 40,
        "patch": "GOLD",
        "test_patch": "TEST",
        "problem_statement": "Fix it",
        "hints_text": "",
        "created_at": "2024-01-01",
        "version": "1.0",
        "FAIL_TO_PASS": ["test_fail"],
        "PASS_TO_PASS": ["test_pass"],
        "environment_setup_commit": "b" * 40,
        "difficulty": "easy",
    }


def _snapshot(tmp_path: Path) -> tuple[Path, Path, SWEVerifiedPCECase]:
    root = tmp_path / "source"
    root.mkdir()
    row = _row()
    wrapper = {
        "instance_id": row["instance_id"],
        "row_sha256": hashlib.sha256(_stable(row).encode()).hexdigest(),
        "source_row": row,
    }
    instances = root / "instances.jsonl"
    instances.write_text(_stable(wrapper) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "dataset": "SWE-bench/SWE-bench_Verified",
        "revision": "fixed",
        "complete": True,
        "provisional": False,
        "instances": 1,
        "instances_file": instances.name,
        "instances_sha256": file_sha256(instances),
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    image_ref = canonical_image_ref(str(row["instance_id"]))
    images = tmp_path / "images.json"
    images.write_text(
        json.dumps(
            {
                "source_manifest_sha256": file_sha256(root / "manifest.json"),
                "records": {
                    image_ref: {
                        "instance_id": row["instance_id"],
                        "status": "audited",
                        "sif_path": "/cache/test.sif",
                        "sif_sha256": "c" * 64,
                        "sif_bytes": 123,
                        "provenance_strength": "retrospective",
                        "base_commit_verified": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cases, _, _ = load_swe_verified_pce_cases(root, images)
    return root, images, cases[0]


def test_verified_loader_freezes_exact_row_and_separates_agent_fields(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    assert case.fail_to_pass == ("test_fail",)
    assert case.pass_to_pass == ("test_pass",)
    projection = case.agent_projection()
    serialized = json.dumps(projection)
    for forbidden in ("GOLD", "TEST", "test_fail", "test_pass"):
        assert forbidden not in serialized
    evaluator = case.evaluator_input()
    assert evaluator["patch"] == "GOLD"
    assert evaluator["test_patch"] == "TEST"


def test_verified_loader_rejects_unverified_base_commit(tmp_path: Path) -> None:
    root, images, _ = _snapshot(tmp_path)
    payload = json.loads(images.read_text())
    next(iter(payload["records"].values()))["base_commit_verified"] = False
    images.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="verified base commit"):
        load_swe_verified_pce_cases(root, images)


def test_verified_loader_rejects_image_manifest_from_another_source(
    tmp_path: Path,
) -> None:
    root, images, _ = _snapshot(tmp_path)
    payload = json.loads(images.read_text())
    payload["source_manifest_sha256"] = "0" * 64
    images.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="another source snapshot"):
        load_swe_verified_pce_cases(root, images)


def test_pc_manifest_projection_omits_outcome_and_official_tests(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    paired = PCCECase(case, "# Plan", True, "d" * 64)
    payload = _case_dict(paired, include_outcome=False)
    serialized = json.dumps(payload)
    assert payload["baseline_plan"] == "# Plan"
    assert "baseline_resolved" not in payload
    assert "baseline_outcome_sha256" not in payload
    for forbidden in ("GOLD", "TEST", "test_fail", "test_pass"):
        assert forbidden not in serialized


def test_frozen_rejected_review_starts_at_p2_without_outcome_fields(
    tmp_path: Path,
) -> None:
    _, _, source_case = _snapshot(tmp_path)
    case = PCCECase(source_case, "# P1", False, "d" * 64)
    raw = tmp_path / "review_01.jsonl"
    raw_row = {
        "instance_id": case.instance_id,
        "status": "completed",
        "review_index": 1,
        "rejection_count_before_review": 0,
        "rejection_count_after_review": 1,
        "plan": "# P1",
        "plan_source": "frozen_new_pce",
        "checker_output": {
            "should_proceed": False,
            "decision_reason": "unsupported root cause",
            "revision_feedback": "Re-establish the root cause.",
            "repository_evidence": [{"path": "src/a.py", "finding": "fact"}],
            "trajectory": [{"content": "omitted raw evidence"}],
        },
        "baseline_pce_resolved": False,
        "pcce_resolved": False,
    }
    raw.write_text(json.dumps(raw_row) + "\n", encoding="utf-8")
    payload = freeze_rejected_first_reviews(
        raw,
        source_run_id="source-run",
        source_run_manifest_sha256="a" * 64,
    )
    seed = tmp_path / "seed.json"
    seed.write_text(_stable(payload) + "\n", encoding="utf-8")
    config = SimpleNamespace(
        first_review_seed=seed,
        expected_first_review_seed_sha256=file_sha256(seed),
    )

    loaded_payload, records = _load_first_review_seed(config, [case])
    assert loaded_payload == payload
    assert records is not None
    serialized = json.dumps(records)
    assert "baseline_pce_resolved" not in serialized
    assert "pcce_resolved" not in serialized
    assert "trajectory" not in serialized

    assignments = _review_assignments([case], records, 2)
    assert len(assignments) == 1
    assert assignments[0].review_index == 2
    assert assignments[0].rejection_count == 1
    assert assignments[0].input_plan == "# P1"
    assert assignments[0].previous_feedback == "Re-establish the root cause."


def test_controller_does_not_rerun_a_frozen_first_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, source_case = _snapshot(tmp_path)
    case = PCCECase(source_case, "# P1", False, "d" * 64)
    seed_payload = {
        "schema_version": 1,
        "artifact_type": "pcce_rejected_first_review_seed",
        "outcome_independent": True,
        "source_run_id": "source-run",
        "source_run_manifest_sha256": "a" * 64,
        "source_review_sha256": "b" * 64,
        "selected_instance_ids": [case.instance_id],
        "records": [
            {
                "instance_id": case.instance_id,
                "status": "completed",
                "review_index": 1,
                "rejection_count_before_review": 0,
                "rejection_count_after_review": 1,
                "plan": "# P1",
                "checker_output": {
                    "should_proceed": False,
                    "revision_feedback": "Re-establish the root cause.",
                },
            }
        ],
    }
    seed = tmp_path / "seed.json"
    seed.write_text(_stable(seed_payload) + "\n", encoding="utf-8")
    files = {}
    for name in ("config", "images", "selection"):
        path = tmp_path / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        files[name] = path
    guideline = tmp_path / "guideline.md"
    guideline.write_text("guideline\n", encoding="utf-8")
    config = SimpleNamespace(
        first_review_seed=seed,
        expected_first_review_seed_sha256=file_sha256(seed),
        run_dir=tmp_path / "run",
        guideline_path=guideline,
        config_path=files["config"],
        image_manifest=files["images"],
        selection_manifest=files["selection"],
        guideline_label="c4-issue-first",
        execution_mode="from_frozen_first_review",
        max_review_rejections=3,
        hpc=SimpleNamespace(max_task_attempts=3),
    )
    identities = {
        "source_manifest_sha256": "c" * 64,
        "pce_outcomes_sha256": "d" * 64,
        "selection_manifest_sha256": file_sha256(files["selection"]),
    }
    observed_reviews: list[int] = []

    class FakeExecutor:
        def __init__(self, _config) -> None:
            pass

        def run_pc(self, assignments):
            observed_reviews.extend(item.review_index for item in assignments)
            assert [item.review_index for item in assignments] == [2]
            return [
                {
                    "instance_id": case.instance_id,
                    "status": "completed",
                    "review_index": 2,
                    "rejection_count_before_review": 1,
                    "rejection_count_after_review": 1,
                    "plan": "# P2",
                    "checker_output": {
                        "should_proceed": True,
                        "revision_feedback": "",
                    },
                }
            ]

        def run_ce(self, assignments):
            assert len(assignments) == 1
            assert assignments[0].accepted_plan == "# P2"
            return [
                {
                    "instance_id": case.instance_id,
                    "status": "completed",
                    "evaluator_result": {"evaluator_resolved": True},
                }
            ]

    monkeypatch.setattr(
        "src.swe_verified_pcce.controller.load_pcce_cases",
        lambda _config: ([case], identities),
    )
    monkeypatch.setattr(
        "src.swe_verified_pcce.controller.pcce_semantic_sha256",
        lambda _config: "semantic",
    )
    monkeypatch.setattr("src.swe_verified_pcce.controller._git_head", lambda: "head")
    monkeypatch.setattr(
        "src.swe_verified_pcce.controller.SWEVerifiedPCCEHPCExecutor",
        FakeExecutor,
    )

    result = run_swe_verified_pcce(config)

    assert observed_reviews == [2]
    assert result is not None
    assert result["method_outcomes"] == {"resolved": 1}
    persisted = json.loads((config.run_dir / "run_manifest.json").read_text())
    assert persisted["first_review"]["executed_in_this_run"] is False


def test_pcce_loader_pairs_exact_new_pce_plan(tmp_path: Path) -> None:
    source, images, case = _snapshot(tmp_path)
    outcomes = tmp_path / "outcomes.jsonl"
    outcome = {
        "instance_id": case.instance_id,
        "row_sha256": case.row_sha256,
        "status": "completed",
        "pce_status": "completed",
        "plan": "# Exact new plan",
        "evaluator_result": {"evaluator_resolved": True},
    }
    outcomes.write_text(json.dumps(outcome) + "\n")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selected_instance_ids": [case.instance_id],
                "source_manifest_sha256": file_sha256(source / "manifest.json"),
                "image_manifest_sha256": file_sha256(images),
            }
        )
    )
    config = SimpleNamespace(
        source_snapshot=source,
        image_manifest=images,
        pce_outcomes=outcomes,
        selection_manifest=selection,
        instance_ids=(case.instance_id,),
    )
    paired, identities = load_pcce_cases(config)
    assert paired[0].baseline_plan == "# Exact new plan"
    assert paired[0].baseline_resolved is True
    assert identities["pce_outcomes_sha256"] == file_sha256(outcomes)


def test_pcce_loader_keeps_plan_when_pce_evaluation_is_unknown(tmp_path: Path) -> None:
    source, images, case = _snapshot(tmp_path)
    outcomes = tmp_path / "outcomes.jsonl"
    outcome = {
        "instance_id": case.instance_id,
        "row_sha256": case.row_sha256,
        "status": "completed",
        "pce_status": "completed",
        "plan": "# Exact new plan",
        "evaluator_result": {
            "task_outcome": "unknown",
            "evaluator_resolved": None,
        },
    }
    outcomes.write_text(json.dumps(outcome) + "\n")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selected_instance_ids": [case.instance_id],
                "source_manifest_sha256": file_sha256(source / "manifest.json"),
                "image_manifest_sha256": file_sha256(images),
            }
        )
    )
    config = SimpleNamespace(
        source_snapshot=source,
        image_manifest=images,
        pce_outcomes=outcomes,
        selection_manifest=selection,
        instance_ids=(case.instance_id,),
    )

    paired, _ = load_pcce_cases(config)

    assert paired[0].baseline_plan == "# Exact new plan"
    assert paired[0].baseline_resolved is None


def test_pcce_loader_rejects_a_different_frozen_pce_outcome(tmp_path: Path) -> None:
    source, images, case = _snapshot(tmp_path)
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(
        json.dumps(
            {
                "instance_id": case.instance_id,
                "row_sha256": case.row_sha256,
                "status": "completed",
                "pce_status": "completed",
                "plan": "# Exact new plan",
                "evaluator_result": {"evaluator_resolved": True},
            }
        )
        + "\n"
    )
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selected_instance_ids": [case.instance_id],
                "source_manifest_sha256": file_sha256(source / "manifest.json"),
                "image_manifest_sha256": file_sha256(images),
            }
        )
    )
    config = SimpleNamespace(
        source_snapshot=source,
        image_manifest=images,
        pce_outcomes=outcomes,
        selection_manifest=selection,
        instance_ids=(case.instance_id,),
        expected_image_manifest_sha256=file_sha256(images),
        expected_pce_outcomes_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="PCE outcomes differ"):
        load_pcce_cases(config)


def test_pcce_array_script_uses_phase_specific_time(tmp_path: Path) -> None:
    hpc = SimpleNamespace(
        job_name_prefix="verified",
        partition="batch",
        cpus_per_task=1,
        mem="4G",
        time="00:01:00",
        python_module="lang/Python/3.11",
        container_module="tools/Apptainer",
        remote_env_file="~/.config/vibe-coding-planning/deepseek.env",
        python_bin="python3",
        worker_config_path="configs/verified.yaml",
    )
    config = SimpleNamespace(hpc=hpc)
    script = build_array_script(
        config=config,
        batch_dir=tmp_path,
        indices=[0],
        attempt=1,
        phase="ce",
        time_limit="00:45:00",
    )
    assert "#SBATCH --time=00:45:00" in script
    assert "src.swe_verified_pcce.worker" in script

    with pytest.raises(ValueError, match="requires 45 minutes"):
        build_array_script(
            config=config,
            batch_dir=tmp_path,
            indices=[0],
            attempt=1,
            phase="ce",
            time_limit="01:20:00",
        )


@pytest.mark.parametrize(
    ("outcome", "resolved"),
    [("resolved", True), ("unresolved", False), ("unknown", None)],
)
def test_evaluator_terminal_keeps_unknown_separate(
    outcome: str, resolved: bool | None
) -> None:
    result = _terminal(
        outcome=outcome,
        reason="reason",
        evaluator_resolved=resolved,
        evidence={},
    )
    assert result["task_outcome"] == outcome
    assert result["evaluator_resolved"] is resolved


def test_evaluator_patch_commands_keep_only_the_loose_command_timeout() -> None:
    class Environment:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int | None]] = []

        def execute(self, command: str, *, timeout: int | None = None):
            self.calls.append((command, timeout))
            return {"returncode": 0, "output": ""}

    environment = Environment()

    applied, _ = _apply_patch(  # type: ignore[arg-type]
        environment, ".vibe_code.patch", command_timeout=1800
    )

    assert applied is True
    assert environment.calls == [
        ("git apply --check .vibe_code.patch", 1800),
        ("git apply --verbose .vibe_code.patch", 1800),
    ]


def test_verified_evaluator_preserves_prepared_sif_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import swebench.harness.grading as grading
    import swebench.harness.test_spec.test_spec as test_spec_module

    instances = []

    class Environment:
        def __init__(self, **kwargs):
            self.commands = []
            self.cleaned = False
            instances.append(self)

        def execute(self, command: str, *, timeout: int | None = None):
            self.commands.append(command)
            if command == "git rev-parse HEAD":
                return {"returncode": 0, "output": "prepared\n"}
            if command == "git rev-list --parents -n 1 HEAD":
                return {"returncode": 0, "output": "prepared abc\n"}
            if command == "git show -s --format=%s HEAD":
                return {"returncode": 0, "output": "SWE-bench\n"}
            if command.startswith("git status"):
                return {"returncode": 0, "output": ""}
            if command.startswith("git diff --name-status"):
                return {"returncode": 0, "output": "M\ttox.ini\n"}
            if command.startswith("git diff"):
                return {"returncode": 0, "output": ""}
            if command.startswith("git cat-file"):
                return {"returncode": 0, "output": ""}
            if command.startswith("git apply"):
                return {"returncode": 0, "output": ""}
            if command == "/bin/bash .vibe_eval.sh":
                return {"returncode": 0, "output": "test passed"}
            raise AssertionError(command)

        def cleanup(self):
            self.cleaned = True

    monkeypatch.setattr(
        "src.swe_verified_pce.evaluator.ApptainerEnvironment", Environment
    )
    monkeypatch.setattr(
        test_spec_module,
        "make_test_spec",
        lambda *_args, **_kwargs: SimpleNamespace(eval_script="pytest -rA"),
    )
    monkeypatch.setattr(
        grading,
        "get_eval_report",
        lambda **_kwargs: {"owner__repo-1": {"resolved": True}},
    )
    case = SimpleNamespace(
        instance_id="owner__repo-1",
        base_commit="abc",
        image=SimpleNamespace(requested_ref="image:v1"),
        evaluator_input=lambda: {},
    )

    result = evaluate_swe_verified_apptainer(
        "diff --git a/a.py b/a.py\n",
        case,
        container=SimpleNamespace(sif_cache_dir=tmp_path, writable_tmpfs=True),
        capacity_window=SimpleNamespace(),
        workdir="/testbed",
        phase_workdir=tmp_path / "evaluate",
        command_timeout=1800,
        repository_baseline_dir=tmp_path / "baseline",
    )

    assert result["task_outcome"] == "resolved"
    assert not any(command.startswith("git reset") for command in instances[0].commands)
    assert not any(command.startswith("git clean") for command in instances[0].commands)
    evidence = json.loads((tmp_path / "baseline/repository_baseline.json").read_text())
    assert evidence["observed"]["head"]["output"] == "prepared\n"
    assert evidence["observed"]["base_to_head_name_status"]["output"] == (
        "M\ttox.ini\n"
    )
    assert instances[0].cleaned is True


def test_verified_evaluator_resume_reidentifies_preserved_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, case = _snapshot(tmp_path)
    run_dir = tmp_path / "run"
    config = SimpleNamespace(run_dir=run_dir)
    source_fingerprint = "source-fingerprint"
    (run_dir / "run_manifest.json").parent.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"execution_fingerprint": source_fingerprint}), encoding="utf-8"
    )
    source = (
        run_dir / "hpc_tasks" / "pce" / source_fingerprint / "checkpoints" / "task_0000"
    )
    source.mkdir(parents=True)
    source_identity = checkpoint_identity(
        case, execution_fingerprint=source_fingerprint
    )
    for phase, payload in (
        ("plan", {"plan": "# Plan\n", "trajectory": [{"plan": True}]}),
        ("code", {"raw_patch": "patch", "patch": "patch", "trajectory": []}),
    ):
        (source / f"{phase}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checkpoint_identity": source_identity,
                    "phase": phase,
                    "payload": payload,
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "src.swe_verified_pce.evaluator_resume.pce_semantic_sha256",
        lambda _config: "evaluator-semantic",
    )

    batch, repair_fingerprint, tasks, skipped = prepare_evaluator_resume(
        config, [case], repair_id="evalfix-v1"
    )

    assert skipped == []
    assert [task.index for task in tasks] == [0]
    target_identity = checkpoint_identity(
        case, execution_fingerprint=repair_fingerprint
    )
    for phase in ("plan", "code"):
        copied = json.loads(
            (batch / "checkpoints" / "task_0000" / f"{phase}.json").read_text()
        )
        assert copied["checkpoint_identity"] == target_identity
        assert (
            copied["payload"]
            == json.loads((source / f"{phase}.json").read_text())["payload"]
        )
    assert not (batch / "checkpoints" / "task_0000" / "evaluate.json").exists()


def test_verified_evaluator_resume_skips_incomplete_and_rejects_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, case = _snapshot(tmp_path)
    run_dir = tmp_path / "run"
    config = SimpleNamespace(run_dir=run_dir)
    (run_dir / "run_manifest.json").parent.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"execution_fingerprint": "source"}), encoding="utf-8"
    )
    (run_dir / "hpc_tasks" / "pce" / "source").mkdir(parents=True)
    monkeypatch.setattr(
        "src.swe_verified_pce.evaluator_resume.pce_semantic_sha256",
        lambda _config: "evaluator-semantic",
    )

    _, _, tasks, skipped = prepare_evaluator_resume(
        config, [case], repair_id="incomplete"
    )
    assert tasks == []
    assert skipped == [case.instance_id]
    with pytest.raises(ValueError, match="unknown instance_ids"):
        prepare_evaluator_resume(
            config,
            [case],
            repair_id="unknown",
            instance_ids=["missing__case-1"],
        )


def test_verified_evaluator_resume_cli_persists_waiting_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(run_dir=tmp_path / "run")
    monkeypatch.setattr(
        evaluator_resume_script,
        "load_swe_verified_pce_config",
        lambda _path: config,
    )
    monkeypatch.setattr(
        evaluator_resume_script,
        "resume_swe_verified_pce_evaluator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "resume_swe_verified_pce_evaluator.py",
            "--config",
            "config.yaml",
            "--repair-id",
            "repair-v1",
            "--instance-id",
            "owner__repo-1",
        ],
    )

    assert evaluator_resume_script.main() == 0
    status = json.loads(
        (
            config.run_dir / "evaluator_repairs/repair-v1/controller_status.json"
        ).read_text()
    )
    assert status["status"] == "waiting_workers"


def test_tracked_smoke_configs_bind_two_case_selection_and_phase_policies() -> None:
    pce = load_swe_verified_pce_config(
        "configs/swe_verified_pce_smoke_v1.yaml", require_api_keys=False
    )
    pcce = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_smoke_seed_v1.yaml", require_api_keys=False
    )

    assert pce.instance_ids == (
        "astropy__astropy-12907",
        "django__django-10097",
    )
    assert pce.run_dir.name == "current-prompt-v1-20260901"
    assert pce.slurm_evaluator_timeout_outcome == "unknown"
    assert pce.plan.max_steps == 0
    assert pce.plan.cost_limit == 0.0
    assert pce.plan.max_attempts == 3
    assert pce.code.max_steps == 0
    assert pce.code.cost_limit == 0.0
    assert pce.code.max_attempts == 3
    assert pce.hpc.time == "00:45:00"
    assert pcce.instance_ids == pce.instance_ids
    assert pcce.phase_times.first_review == "00:45:00"
    assert pcce.phase_times.revision_review == "00:45:00"
    assert pcce.phase_times.ce == "00:45:00"
    assert pcce.checker.checker.max_steps == 0
    assert pcce.checker.checker.cost_limit == 0.0
    assert pcce.checker.checker.max_attempts == 3

    selection = json.loads(pce.selection_manifest.read_text(encoding="utf-8"))
    contract = selection["pce_smoke_contract"]
    assert contract["case_count"] == 2
    assert contract["worker_resources"] == {
        "cpus_per_task": 1,
        "memory": "4G",
        "walltime": "00:45:00",
        "max_task_attempts": 3,
    }
    assert contract["acceptance"]["completed_terminal_records"] == 2
    assert contract["acceptance"]["operationally_incomplete_allowed"] == 0

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_verified_pce_smoke_supervisor_v1_20260901.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--max-runs") + 1] == "12"
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--slice-time") + 1] == "00:10:00"
    assert arguments[arguments.index("--batch-script") + 1] == (
        "scripts/hpc_submit_swe_verified_pce.sh"
    )
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_pce_smoke_v1.yaml"
    )
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_fpta_mixed24_aion_pilot_has_explicit_low_memory_contract() -> None:
    for repeat in (2, 3):
        path = (
            "configs/swe_verified_safe_pce_fpta_mixed24_expansion_"
            f"repeat{repeat}_aion_v1_20260916.yaml"
        )
        config = load_swe_verified_pce_config(path, require_api_keys=False)
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

        assert len(config.instance_ids) == 24
        assert config.hpc.cpus_per_task == 1
        assert config.hpc.mem == "1750M"
        assert config.hpc.time == "01:00:00"
        assert raw["experiment_contract"]["cluster"] == "aion"
        assert raw["experiment_contract"]["budget"]["worker_memory"] == "1750M"


def test_aion_low_memory_requires_matching_experiment_contract(
    tmp_path: Path,
) -> None:
    source = Path("configs/swe_verified_pce_smoke_v1.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["hpc"]["mem"] = "1750M"
    raw["experiment_contract"] = {
        "cluster": "aion",
        "budget": {"worker_memory": "4G"},
    }
    path = tmp_path / "unreviewed-low-memory.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="explicitly reviews 1750M"):
        load_swe_verified_pce_config(path, require_api_keys=False)


def test_quick_selection_is_deterministic_and_covers_repositories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    rows = []
    for repo, count in (("a/one", 3), ("b/two", 2), ("c/three", 1)):
        for index in range(count):
            row = _row(f"{repo.replace('/', '__')}-{index}")
            row["repo"] = repo
            rows.append(
                {
                    "instance_id": row["instance_id"],
                    "row_sha256": hashlib.sha256(_stable(row).encode()).hexdigest(),
                    "source_row": row,
                }
            )
    instances = root / "instances.jsonl"
    instances.write_text("".join(_stable(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {
        "dataset": "SWE-bench/SWE-bench_Verified",
        "revision": "fixed",
        "complete": True,
        "provisional": False,
        "instances": len(rows),
        "instances_file": instances.name,
        "instances_sha256": file_sha256(instances),
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    arguments = {
        "source_snapshot": root,
        "selection_id": "fixture",
        "seed": "fixed-seed",
        "size": 4,
        "excluded_instance_ids": {"a__one-0"},
    }
    first = freeze_selection(**arguments)
    second = freeze_selection(**arguments)

    assert first == second
    assert len(first["selected_instance_ids"]) == 4
    assert "a__one-0" not in first["selected_instance_ids"]
    assert set(first["repository_distribution"]["selected"]) == {
        "a/one",
        "b/two",
        "c/three",
    }
    assert [case["selection_role"] for case in first["selected_cases"]].count(
        "repository_coverage"
    ) == 3


def test_tracked_quick50_pce_contract_is_frozen() -> None:
    config = load_swe_verified_pce_config(
        "configs/swe_verified_pce_quick50_v1_20260901.yaml",
        require_api_keys=False,
    )
    selection = json.loads(config.selection_manifest.read_text(encoding="utf-8"))
    smoke = json.loads(
        Path(
            "configs/frozen_swe_verified_smoke/swe-verified-development-smoke-v1.json"
        ).read_text(encoding="utf-8")
    )

    assert len(config.instance_ids) == 50
    assert len(set(config.instance_ids)) == 50
    assert selection["source_instance_count"] == 500
    assert selection["eligible_instance_count"] == 498
    assert set(config.instance_ids).isdisjoint(smoke["selected_instance_ids"])
    assert len(selection["repository_distribution"]["selected"]) == 12
    assert selection["selection_policy"] == {
        "membership_substitution_on_acquisition_failure": False,
        "name": "one_per_repository_then_global_sha256_rank",
        "outcome_independent": True,
        "repository_coverage_count": 12,
        "seed": "swe-verified-quick50-repository-coverage-v1-20260901",
        "target_size": 50,
        "uses_plan_or_pce_pcce_outcomes": False,
    }
    assert config.run_dir.name == "current-prompt-quick50-v1-20260901"
    assert config.plan.max_steps == 0
    assert config.plan.cost_limit == 0.0
    assert config.plan.max_attempts == 3
    assert config.code.max_steps == 0
    assert config.code.cost_limit == 0.0
    assert config.code.max_attempts == 3
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:45:00"
    assert config.hpc.max_task_attempts == 3

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_verified_pce_quick50_supervisor_v1_20260901.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--max-runs") + 1] == "24"
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--slice-time") + 1] == "00:10:00"
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_pce_quick50_v1_20260901.yaml"
    )
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_tracked_quick50_seed_pcce_contract_is_frozen() -> None:
    config = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_quick50_seed_v1_20260901.yaml",
        require_api_keys=False,
    )

    assert len(config.instance_ids) == 50
    assert config.guideline_label == "seed"
    assert config.guideline_path.name == "seed.md"
    assert config.max_review_rejections == 3
    assert config.expected_pce_outcomes_sha256 == (
        "1f1e4420ec160d89a144a669d6cfc27ba1b131f35f6b76d59cf496c80650e753"
    )
    assert config.expected_image_manifest_sha256 == (
        "fc7db0f468aaf5366981ba46d9db992aec452b991d1e27ff55dd538bf84f0290"
    )
    assert config.checker.checker.max_steps == 0
    assert config.checker.checker.cost_limit == 0.0
    assert config.checker.checker.agent_timeout_seconds == 0
    assert config.checker.checker.max_attempts == 3
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.max_task_attempts == 3
    assert config.phase_times.first_review == "00:45:00"
    assert config.phase_times.revision_review == "00:45:00"
    assert config.phase_times.ce == "00:45:00"

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_verified_pcce_quick50_seed_supervisor_v1_20260901.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--max-runs") + 1] == "24"
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--slice-time") + 1] == "00:10:00"
    assert arguments[arguments.index("--batch-script") + 1] == (
        "scripts/hpc_submit_swe_verified_pcce.sh"
    )
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_pcce_quick50_seed_v1_20260901.yaml"
    )
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_tracked_quick50_c4_pcce_contract_is_paired_with_seed() -> None:
    seed_raw = yaml.safe_load(
        Path("configs/swe_verified_pcce_quick50_seed_v1_20260901.yaml").read_text(
            encoding="utf-8"
        )
    )
    c4_raw = yaml.safe_load(
        Path("configs/swe_verified_pcce_quick50_c4_v1_20260902.yaml").read_text(
            encoding="utf-8"
        )
    )
    for payload in (seed_raw, c4_raw):
        payload.pop("purpose")
        payload["paths"].pop("guideline")
        payload["paths"].pop("run_dir")
        payload["pcce"].pop("guideline_label")
        payload["hpc"].pop("job_name_prefix")
        payload["hpc"].pop("worker_config_path")
    assert c4_raw == seed_raw

    seed = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_quick50_seed_v1_20260901.yaml",
        require_api_keys=False,
    )
    c4 = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_quick50_c4_v1_20260902.yaml",
        require_api_keys=False,
    )

    assert c4.instance_ids == seed.instance_ids
    assert c4.source_snapshot == seed.source_snapshot
    assert c4.image_manifest == seed.image_manifest
    assert c4.selection_manifest == seed.selection_manifest
    assert c4.pce_outcomes == seed.pce_outcomes
    assert c4.pce.plan == seed.pce.plan
    assert c4.pce.code == seed.pce.code
    assert c4.pce.container == seed.pce.container
    assert c4.checker_prompt == seed.checker_prompt
    assert c4.checker_instance_template == seed.checker_instance_template
    assert c4.plan_revision_prompt == seed.plan_revision_prompt
    assert c4.plan_revision_instance_template == seed.plan_revision_instance_template
    assert c4.checker.container == seed.checker.container
    assert c4.expected_pce_outcomes_sha256 == seed.expected_pce_outcomes_sha256
    assert c4.expected_image_manifest_sha256 == seed.expected_image_manifest_sha256
    assert c4.guideline_label == "behavioral_formal_c4"
    assert c4.guideline_path.name == "c4.md"
    assert file_sha256(c4.guideline_path) == (
        "1e7c68a2c14175dda9a9a8bb16455061c50b9961aafb6eedc030cdbef21e1ebd"
    )
    assert c4.run_dir != seed.run_dir
    assert c4.max_review_rejections == seed.max_review_rejections == 3
    assert c4.checker.checker.max_steps == seed.checker.checker.max_steps == 0
    assert c4.checker.checker.cost_limit == seed.checker.checker.cost_limit == 0.0
    assert c4.checker.checker.agent_timeout_seconds == 0
    assert seed.checker.checker.agent_timeout_seconds == 0
    assert c4.checker.checker.max_attempts == seed.checker.checker.max_attempts == 3
    assert c4.hpc.cpus_per_task == seed.hpc.cpus_per_task == 1
    assert c4.hpc.mem == seed.hpc.mem == "4G"
    assert c4.hpc.max_task_attempts == seed.hpc.max_task_attempts == 3
    assert c4.phase_times == seed.phase_times

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_verified_pcce_quick50_c4_supervisor_v1_20260902.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--max-runs") + 1] == "24"
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--slice-time") + 1] == "00:10:00"
    assert arguments[arguments.index("--batch-script") + 1] == (
        "scripts/hpc_submit_swe_verified_pcce.sh"
    )
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_pcce_quick50_c4_v1_20260902.yaml"
    )
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_tracked_issue_first_c4_pcce_starts_from_frozen_rejections() -> None:
    config = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_quick50_c4_issue_first_v1_20260903.yaml",
        require_api_keys=False,
    )
    seed = json.loads(config.first_review_seed.read_text(encoding="utf-8"))

    assert config.execution_mode == "from_frozen_first_review"
    assert len(config.instance_ids) == 16
    assert tuple(seed["selected_instance_ids"]) == config.instance_ids
    assert seed["outcome_independent"] is True
    assert seed["source_review_sha256"] == (
        "b2cb06ead5561b9f0aecc456aebb044ea4a5fc5e7e866fdfa35910aa0972e1d8"
    )
    assert file_sha256(config.first_review_seed) == (
        config.expected_first_review_seed_sha256
    )
    assert "The original issue is the objective" in config.plan_revision_prompt
    assert "provided as advisory evidence" in config.plan_revision_prompt
    assert config.max_review_rejections == 3
    assert config.phase_times.revision_review == "00:45:00"
    assert config.phase_times.ce == "00:45:00"

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/"
            "swe_verified_pcce_quick50_c4_issue_first_supervisor_v1_20260903.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_pcce_quick50_c4_issue_first_v1_20260903.yaml"
    )
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_tracked_c5_safe_u8_pcce_is_exact_workspace_safe_pce_failure_subset() -> None:
    config_path = Path(
        "configs/swe_verified_pcce_c5_prompt_v2_safe_u8_v1_20260909.yaml"
    )
    config = load_swe_verified_pcce_config(config_path, require_api_keys=False)

    expected_ids = {
        "psf__requests-6028",
        "pydata__xarray-6938",
        "sphinx-doc__sphinx-7440",
        "sphinx-doc__sphinx-8056",
        "matplotlib__matplotlib-26466",
        "django__django-15127",
        "pylint-dev__pylint-6528",
        "sympy__sympy-13798",
    }
    outcomes = [
        json.loads(line)
        for line in config.pce_outcomes.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unresolved_ids = {
        row["instance_id"]
        for row in outcomes
        if row["evaluator_result"]["task_outcome"] == "unresolved"
    }
    exclusions = json.loads(
        Path(
            "configs/frozen_rq2_analysis/"
            "workspace-isolation-exclusions-v1-20260908.json"
        ).read_text(encoding="utf-8")
    )
    excluded_verified = {
        row["instance_id"]
        for row in exclusions["excluded_cases"]
        if row["dataset"] == "SWE-bench Verified"
    }

    assert set(config.instance_ids) == expected_ids
    assert expected_ids == unresolved_ids - excluded_verified
    assert "pylint-dev__pylint-4970" not in config.instance_ids
    assert config.guideline_label == "behavioral_c5_prompt_v2_v1"
    assert file_sha256(config.guideline_path) == (
        "34c2a670918df51642a4a2fe5a3669ee95f25e179604fc9fecee7a81e405a7bc"
    )
    assert "Apply the candidate guideline to the complete submitted Plan" in (
        config.checker_prompt
    )
    assert "re-evaluate the proposed solution" in config.plan_revision_prompt
    assert "from the original task and repository evidence" in (
        config.plan_revision_prompt
    )
    assert config.expected_pce_outcomes_sha256 == (
        "1f1e4420ec160d89a144a669d6cfc27ba1b131f35f6b76d59cf496c80650e753"
    )
    assert config.expected_image_manifest_sha256 == (
        "fc7db0f468aaf5366981ba46d9db992aec452b991d1e27ff55dd538bf84f0290"
    )
    assert config.max_review_rejections == 3
    assert config.checker.checker.max_steps == 0
    assert config.checker.checker.cost_limit == 0.0
    assert config.checker.checker.agent_timeout_seconds == 0
    assert config.checker.checker.max_attempts == 3
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.max_task_attempts == 3
    assert config.phase_times.first_review == "00:45:00"
    assert config.phase_times.revision_review == "00:45:00"
    assert config.phase_times.ce == "00:45:00"

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/"
            "swe_verified_pcce_c5_prompt_v2_safe_u8_supervisor_v1_20260909.yaml"
        ).read_text(encoding="utf-8")
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--batch-script") + 1] == (
        "scripts/hpc_submit_swe_verified_pcce.sh"
    )
    assert arguments[arguments.index("--config") + 1] == str(config_path)
    assert arguments[arguments.index("--max-runs") + 1] == "36"
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_tracked_c5_safe_u8_recovery_preserves_rejected_first_review() -> None:
    config = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_c5_prompt_v2_safe_u8_recovery4_v1_20260909.yaml",
        require_api_keys=False,
    )
    seed = json.loads(config.first_review_seed.read_text(encoding="utf-8"))
    expected_ids = (
        "matplotlib__matplotlib-26466",
        "django__django-15127",
        "pylint-dev__pylint-6528",
        "sympy__sympy-13798",
    )

    assert config.execution_mode == "from_frozen_first_review"
    assert config.instance_ids == expected_ids
    assert tuple(seed["selected_instance_ids"]) == expected_ids
    assert seed["instances"] == 4
    assert seed["source_run_id"] == ("swe-verified-pcce-c5-safe-u8-v1-20260909")
    assert seed["source_review_sha256"] == (
        "10c6edbccc0ebd8766a4092cc18fa7afcf448ad8792cf3d716bafe498b5d6b40"
    )
    assert file_sha256(config.first_review_seed) == (
        config.expected_first_review_seed_sha256
    )
    assert config.guideline_label == "behavioral_c5_prompt_v2_v1"
    assert config.phase_times.revision_review == "00:45:00"
    assert config.phase_times.ce == "00:45:00"


def test_controller_recovers_only_three_evidenced_evaluator_slurm_timeouts(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    batch = tmp_path / "batch"
    manifest = batch / "tasks" / "task_0000.json"
    output = batch / "outputs" / "task_0000.json"
    attempts_dir = batch / "attempts" / "task_0000"
    checkpoint_dir = batch / "checkpoints" / "task_0000"
    manifest.parent.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "case": case.to_dict(),
                "fingerprint": "fp",
                "task_index": 0,
            }
        )
    )
    identity = checkpoint_identity(case, execution_fingerprint="fp")
    for phase, payload in (
        (
            "plan",
            {
                "plan": "# Plan",
                "plan_sha256": hashlib.sha256(b"# Plan").hexdigest(),
                "trajectory": [],
            },
        ),
        (
            "code",
            {
                "raw_patch": "PATCH",
                "patch": "PATCH",
                "trajectory": [],
                "patch_submission": {},
                "workspace_evidence": {},
            },
        ),
    ):
        (checkpoint_dir / f"{phase}.json").write_text(
            json.dumps(
                {
                    "checkpoint_identity": identity,
                    "phase": phase,
                    "payload": payload,
                }
            )
        )
    (checkpoint_dir / "evaluate_started.json").write_text(
        json.dumps({"checkpoint_identity": identity, "phase": "evaluate"})
    )
    for attempt in range(1, 4):
        status = attempts_dir / f"attempt_{attempt:02d}" / "slurm_status.json"
        status.parent.mkdir(parents=True)
        status.write_text(
            json.dumps(
                {
                    "state": "TIMEOUT",
                    "instance_id": case.instance_id,
                    "task_index": 0,
                }
            )
        )
    task = TaskFiles(0, case.instance_id, manifest, output, attempts_dir)

    recovered = recover_exhausted_evaluator_timeout(
        batch_dir=batch,
        task=task,
        fingerprint="fp",
        max_attempts=3,
    )

    assert recovered is not None
    assert recovered["evaluator_result"]["task_outcome"] == "unknown"
    assert recovered["evaluator_result"]["terminal_kind"] == (
        "slurm_evaluator_timeout_after_attempts"
    )
    assert (checkpoint_dir / "evaluate.json").is_file()

    third_status = attempts_dir / "attempt_03" / "slurm_status.json"
    third = json.loads(third_status.read_text())
    third["state"] = "FAILED"
    third_status.write_text(json.dumps(third))
    (checkpoint_dir / "evaluate.json").unlink()

    assert (
        recover_exhausted_evaluator_timeout(
            batch_dir=batch,
            task=task,
            fingerprint="fp",
            max_attempts=3,
        )
        is None
    )


def test_evaluator_timeout_recovery_rejects_a_non_three_attempt_policy(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    batch = tmp_path / "batch"
    manifest = batch / "tasks" / "task_0000.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"case": case.to_dict()}))
    task = TaskFiles(
        0,
        case.instance_id,
        manifest,
        batch / "outputs" / "task_0000.json",
        batch / "attempts" / "task_0000",
    )

    with pytest.raises(ValueError, match="requires three attempts"):
        recover_exhausted_evaluator_timeout(
            batch_dir=batch,
            task=task,
            fingerprint="fp",
            max_attempts=2,
        )
