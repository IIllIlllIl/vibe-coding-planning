from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from src.swe_verified_pcce.config import load_swe_bench_pro_pcce_config
from src.swe_verified_pcce.dataset import load_pcce_cases
from src.swe_verified_pcce.hpc_executor import _case_dict, build_array_script
from src.swe_verified_pcce.models import CEAssignment, PCReviewAssignment, PCCECheckerCase
from src.swe_verified_pcce.runner import SWEVerifiedPCCERunner
from src.optimization.models import CheckerOutput


CONFIG = Path("configs/swe_bench_pro_pcce_quick25_c4_issue_first_v1_20260907.yaml")
SMOKE_CONFIG = Path("configs/swe_bench_pro_pcce_smoke_c4_issue_first_v2_20260907.yaml")


def _config():
    return load_swe_bench_pro_pcce_config(CONFIG, require_api_keys=False)


def test_pro_pcce_frozen_inputs_pair_exactly_and_hide_evaluator_fields() -> None:
    config = _config()
    assert config.pce.docker.workdir == "/app"
    assert config.checker.docker.workdir == "/app"
    cases, identities = load_pcce_cases(config)
    assert len(cases) == 25
    assert sum(case.baseline_resolved is True for case in cases) == 24
    assert identities["pce_outcomes_sha256"] == config.expected_pce_outcomes_sha256

    checker_payload = PCCECheckerCase(cases[0].source, cases[0].baseline_plan, {}).checker_payload()
    assert checker_payload["repository"]["dataset_type"] == "pro"
    assert set(checker_payload) == {"instance_id", "issue_description", "repository", "plan"}
    manifest_case = _case_dict(cases[0], include_outcome=False)
    source = manifest_case["source"]
    assert source["gold_patch"] == source["test_patch"] == ""
    assert source["fail_to_pass"] == source["pass_to_pass"] == []
    assert source["source_row"] == {}
    assert "baseline_resolved" not in manifest_case


def test_verified_pcce_checker_workdir_is_unchanged() -> None:
    from src.swe_verified_pcce.config import load_swe_verified_pcce_config

    config = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_quick50_c4_issue_first_v1_20260903.yaml",
        require_api_keys=False,
    )
    assert config.pce.docker.workdir == "/testbed"
    assert config.checker.docker.workdir == "/testbed"


def test_pro_prompt_binding_only_changes_official_repository_path() -> None:
    verified = yaml.safe_load(
        Path("configs/pcce_issue_first_revision_prompt_v1_20260903.yaml").read_text()
    )["prompts"]
    pro = yaml.safe_load(
        Path("configs/pcce_issue_first_revision_prompt_pro_v1_20260907.yaml").read_text()
    )["prompts"]
    assert {key: value.replace("/app", "/testbed") for key, value in pro.items()} == verified


def test_pro_revision_plan_config_preserves_pro_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    monkeypatch.setenv(config.pce.plan.api_key_env, "test-placeholder")
    runner = SWEVerifiedPCCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    )

    plan_config = runner._plan_config()

    assert plan_config.system.dataset == "ScaleAI/SWE-bench_Pro"
    assert plan_config.system.dataset_type == "pro"


def test_pro_v2_prompt_binding_only_changes_official_repository_path() -> None:
    verified = yaml.safe_load(
        Path("configs/pcce_issue_first_revision_prompt_v2_20260908.yaml").read_text()
    )["prompts"]
    pro = yaml.safe_load(
        Path("configs/pcce_issue_first_revision_prompt_pro_v2_20260908.yaml").read_text()
    )["prompts"]
    assert {key: value.replace("/app", "/testbed") for key, value in pro.items()} == verified


def test_pro_pcce_uses_official_workspace_policy_and_rejects_history(tmp_path: Path) -> None:
    config = _config()
    case = load_pcce_cases(config)[0][0]
    runner = SWEVerifiedPCCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    )

    class Environment:
        def execute(self, command: str, timeout: int):
            assert timeout == 120
            if command == "git rev-parse HEAD":
                return {"returncode": 0, "output": case.source.base_commit + "\n"}
            return {"returncode": 0, "output": ""}

    assignment = PCReviewAssignment(case, 1, 0, case.baseline_plan, "")
    runner._initialize_repository(
        Environment(), assignment, phase="checker", evidence_dir=tmp_path / "baseline"
    )
    evidence = (tmp_path / "baseline" / "repository_baseline.json").read_text()
    assert "official_sif_workspace_v1" in evidence
    assert "git reset" not in evidence

    contamination = runner._history_contamination(
        [[{"role": "assistant", "command": "git log --oneline -5"}]], phase="pc"
    )
    assert contamination is not None
    assert contamination["history_access_detected"] is True


def test_pro_pcce_ce_dispatches_to_pro_pce_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(_config(), run_dir=tmp_path / "run")
    case = load_pcce_cases(config)[0][0]
    accepted = config.run_dir / "reviews" / "accepted.json"
    accepted.parent.mkdir(parents=True, exist_ok=True)
    accepted.write_text("{}")
    observed = {}

    class FakeProRunner:
        def __init__(self, passed_config, capacity, **kwargs):
            observed.update(config=passed_config, capacity=capacity, kwargs=kwargs)

        def run(self, source):
            observed["source"] = source
            return {"evaluator_result": {"evaluator_resolved": True}}

    monkeypatch.setattr("src.swe_verified_pcce.runner._verify_sif", lambda *args: None)
    monkeypatch.setattr("src.swe_verified_pcce.runner.SWEBenchProPCERunner", FakeProRunner)
    monkeypatch.setattr(
        "src.swe_verified_pcce.runner.checkpoint_identity", lambda *args, **kwargs: "id"
    )
    runner = SWEVerifiedPCCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    )
    result = runner.run_ce(CEAssignment(case, accepted, case.baseline_plan), fingerprint="fp")
    assert observed["source"] is case.source
    assert result["pcce_status"] == "completed"


def test_pro_checker_history_access_becomes_incomplete_not_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(_config(), run_dir=tmp_path / "run")
    case = load_pcce_cases(config)[0][0]
    monkeypatch.setattr("src.swe_verified_pcce.runner._verify_sif", lambda *args: None)

    class FakeChecker:
        def __init__(self, *args):
            pass

        def __call__(self, *args, **kwargs):
            output = CheckerOutput(
                True,
                "approve",
                (),
                ({"role": "assistant", "command": "git log --oneline -1"},),
                "",
            )
            kwargs["completion_callback"](output)
            return output

    monkeypatch.setattr("src.swe_verified_pcce.runner.DockerChecker", FakeChecker)
    runner = SWEVerifiedPCCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    )
    result = runner.run_pc(
        PCReviewAssignment(case, 1, 0, case.baseline_plan, ""),
        fingerprint="fp",
        guideline="guideline",
    )
    assert result["pc_status"] == "operationally_incomplete"
    assert result["terminal_reason"] == "agent_git_history_access_detected"
    assert "checker_output" not in result


def test_pro_pcce_worker_script_and_supervisor_keep_frozen_limits(tmp_path: Path) -> None:
    config = _config()
    script = build_array_script(
        config=config,
        batch_dir=tmp_path,
        indices=[0, 1],
        attempt=1,
        phase="pc",
    )
    assert "#SBATCH --cpus-per-task=1" in script
    assert "#SBATCH --mem=4G" in script
    assert "#SBATCH --time=00:45:00" in script
    assert "src.swe_verified_pcce.worker" in script
    assert "--array=0,1" in script and "%" not in script.split("--array=", 1)[1].splitlines()[0]

    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_bench_pro_pcce_quick25_c4_issue_first_supervisor_v1_20260907.yaml"
        ).read_text()
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--slice-time") + 1] == "00:10:00"
    assert "scripts/hpc_submit_swe_bench_pro_pcce.sh" in arguments
    assert "--require-clean-worktree" in arguments


def test_pro_pcce_smoke_is_three_repository_development_subset() -> None:
    config = load_swe_bench_pro_pcce_config(SMOKE_CONFIG, require_api_keys=False)
    cases, _ = load_pcce_cases(config)
    assert len(cases) == 3
    assert {case.source.repo for case in cases} == {
        "ansible/ansible",
        "internetarchive/openlibrary",
        "qutebrowser/qutebrowser",
    }
    assert sum(case.baseline_resolved is False for case in cases) == 1
    supervisor = yaml.safe_load(
        Path(
            "configs/archive/supervisor_launches/swe_bench_pro_pcce_smoke_c4_issue_first_supervisor_v2_20260907.yaml"
        ).read_text()
    )
    assert "--require-clean-worktree" in supervisor["arguments"]
    assert supervisor["arguments"][supervisor["arguments"].index("--max-runs") + 1] == "16"
