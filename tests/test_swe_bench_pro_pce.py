from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from src.swe_bench_pro_pce.config import load_swe_bench_pro_pce_config
from src.swe_bench_pro_pce.dataset import load_swe_bench_pro_pce_cases
from src.swe_bench_pro_pce.evaluator import (
    evaluate_swe_bench_pro_apptainer,
    strip_binary_hunks,
)
from src.swe_bench_pro_pce.hpc_executor import SWEBenchProPCEHPCExecutor
from src.swe_bench_pro_pce.repository import materialize_ancestor_only_repository
from src.swe_bench_pro_pce.runner import SWEBenchProPCERunner
from src.swe_verified_pce.dataset import file_sha256


ROOT = Path(__file__).resolve().parents[1]


def _stable(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fixture(tmp_path: Path):
    snapshot = tmp_path / "snapshot"
    assets = snapshot / "assets" / "case-1"
    assets.mkdir(parents=True)
    paths = {}
    for name in ("run_script", "parser", "base_dockerfile", "instance_dockerfile"):
        path = assets / name
        path.write_text(name)
        paths[name] = str(path.relative_to(snapshot))
        paths[f"{name}_sha256"] = file_sha256(path)
    row = {
        "repo": "owner/repo", "instance_id": "case-1", "base_commit": "a" * 40,
        "patch": "GOLD", "test_patch": "TEST", "problem_statement": "Fix it",
        "requirements": "Keep compatibility", "interface": "No new interfaces",
        "fail_to_pass": "['test_a']", "pass_to_pass": "['test_b']",
        "before_repo_set_cmd": "git checkout testsha -- tests/test_a.py",
        "selected_test_files_to_run": '["tests/test_a.py"]',
        "dockerhub_tag": "owner.repo-case",
    }
    wrapper = {
        "instance_id": "case-1",
        "row_sha256": hashlib.sha256(_stable(row).encode()).hexdigest(),
        "issue_description": "Fix it\n\nRequirements:\nKeep compatibility\n\nNew interfaces introduced:\nNo new interfaces",
        "evaluator_assets": paths,
        "source_row": row,
    }
    rows = snapshot / "instances.jsonl"
    rows.write_text(_stable(wrapper) + "\n")
    selection_hash = "b" * 64
    manifest = {
        "dataset": "ScaleAI/SWE-bench_Pro", "revision": "fixed",
        "complete": True, "provisional": False, "instances": 1,
        "instances_file": "instances.jsonl", "instances_sha256": file_sha256(rows),
        "selection_manifest_sha256": selection_hash,
        "official_evaluator_revision": "c" * 40,
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    image = "jefzda/sweap-images:owner.repo-case"
    images = tmp_path / "images.json"
    images.write_text(json.dumps({
        "source_manifest_sha256": file_sha256(snapshot / "manifest.json"),
        "selection_manifest_sha256": selection_hash,
        "agent_history_policy": "future_history_inaccessible_v1",
        "agent_history_implementation_sha256": file_sha256(
            ROOT / "src/swe_bench_pro_pce/repository.py"
        ),
        "records": {image: {
            "instance_id": "case-1", "status": "audited",
            "sif_path": "/cache/case.sif", "sif_bytes": 1,
            "sif_sha256": "d" * 64, "provenance_strength": "retrospective",
            "base_commit_verified": True,
        }},
    }))
    return snapshot, images


def test_pro_loader_separates_agent_and_evaluator_fields(tmp_path: Path) -> None:
    snapshot, images = _fixture(tmp_path)
    cases, _, _ = load_swe_bench_pro_pce_cases(snapshot, images)
    case = cases[0]
    assert case.issue_description.startswith("Fix it\n\nRequirements:")
    visible = json.dumps(case.agent_projection())
    for forbidden in ("GOLD", "TEST", "test_a", "before_repo_set_cmd"):
        assert forbidden not in visible
    assert case.evaluator_input()["patch"] == "GOLD"


def test_pro_loader_requires_verified_base_commit(tmp_path: Path) -> None:
    snapshot, images = _fixture(tmp_path)
    value = json.loads(images.read_text())
    next(iter(value["records"].values()))["base_commit_verified"] = False
    images.write_text(json.dumps(value))
    try:
        load_swe_bench_pro_pce_cases(snapshot, images)
    except ValueError as exc:
        assert "verified base commit" in str(exc)
    else:
        raise AssertionError("unverified Pro base commit was accepted")


def test_pro_loader_rejects_uncontained_future_history(tmp_path: Path) -> None:
    snapshot, images = _fixture(tmp_path)
    value = json.loads(images.read_text())
    value.pop("agent_history_policy")
    images.write_text(json.dumps(value))
    try:
        load_swe_bench_pro_pce_cases(snapshot, images)
    except ValueError as exc:
        assert "future-history containment" in str(exc)
    else:
        raise AssertionError("Pro image without history containment was accepted")


def test_pro_loader_rejects_changed_containment_implementation(tmp_path: Path) -> None:
    snapshot, images = _fixture(tmp_path)
    value = json.loads(images.read_text())
    value["agent_history_implementation_sha256"] = "0" * 64
    images.write_text(json.dumps(value))
    try:
        load_swe_bench_pro_pce_cases(snapshot, images)
    except ValueError as exc:
        assert "implementation identity" in str(exc)
    else:
        raise AssertionError("changed Pro containment implementation was accepted")


def test_pro_binary_patch_policy_matches_official_exclusion() -> None:
    patch = "diff --git a/a b/a\n@@ -1 +1 @@\n-a\n+b\ndiff --git a/x b/x\nGIT binary patch\nabc\n"
    result = strip_binary_hunks(patch)
    assert "a/a" in result
    assert "GIT binary patch" not in result


def test_ancestor_only_repository_removes_future_objects_and_remotes(
    tmp_path: Path,
) -> None:
    import subprocess

    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repository / "value.txt").write_text("base\n")
    git("add", "value.txt")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    (repository / "value.txt").write_text("future\n")
    git("commit", "-qam", "future")
    future = git("rev-parse", "HEAD").stdout.strip()
    git("checkout", "--detach", "-q", base)

    evidence = materialize_ancestor_only_repository(
        repository,
        base,
        evidence_dir=tmp_path / "evidence",
        forbidden_commits=(future,),
    )

    assert evidence["verified"] is True
    assert evidence["policy"] == "future_history_inaccessible_v1"
    assert git("rev-parse", "HEAD").stdout.strip() == base
    assert git("status", "--porcelain=v1").stdout == ""
    assert git("remote").stdout == ""
    unavailable = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "-e", f"{future}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unavailable.returncode != 0
    assert not (repository / ".git" / "objects" / "info" / "alternates").exists()


def test_pro_evaluator_keeps_gold_test_checkout_after_patch(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot, images = _fixture(tmp_path)
    case = load_swe_bench_pro_pce_cases(snapshot, images)[0][0]
    commands = []

    class FakeEnvironment:
        def __init__(self, *args, **kwargs):
            binding = next(
                value for value in kwargs["run_args"] if value.endswith(":/workspace")
            )
            self.official = Path(binding.removesuffix(":/workspace"))

        def execute(self, command, timeout=None):
            commands.append(command)
            if command.startswith("bash /workspace/run_script.sh"):
                (self.official / "stdout.log").write_text("test_a PASSED\ntest_b PASSED\n")
                (self.official / "stderr.log").write_text("")
            if command.startswith("python /workspace/parser.py"):
                (self.official / "output.json").write_text(
                    json.dumps(
                        {
                            "tests": [
                                {"name": "test_a", "status": "PASSED"},
                                {"name": "test_b", "status": "PASSED"},
                            ]
                        }
                    )
                )
            return {"returncode": 0, "output": ""}

        def cleanup(self):
            return None

    monkeypatch.setattr(
        "src.swe_bench_pro_pce.evaluator.ApptainerEnvironment", FakeEnvironment
    )
    monkeypatch.setattr(
        "src.swe_bench_pro_pce.evaluator.restore_repository_to_base",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.swe_bench_pro_pce.evaluator._apply_patch",
        lambda *args, **kwargs: (True, [{"command": "git apply"}]),
    )
    config = SimpleNamespace(sif_cache_dir=tmp_path, writable_tmpfs=True)
    result = evaluate_swe_bench_pro_apptainer(
        "diff --git a/a b/a\n",
        case,
        snapshot=snapshot,
        container=config,
        capacity_window=SimpleNamespace(),
        workdir="/app",
        phase_workdir=tmp_path / "evaluate",
        command_timeout=1800,
    )

    assert result["task_outcome"] == "resolved"
    setup_index = commands.index("git checkout testsha -- tests/test_a.py")
    test_index = next(i for i, value in enumerate(commands) if value.startswith("bash /workspace/run_script.sh"))
    assert setup_index < test_index


def test_pro_executor_emits_pro_worker_and_mode(tmp_path: Path) -> None:
    config = SimpleNamespace(
        hpc=SimpleNamespace(
            worker_config_path="configs/pro.yaml", job_name_prefix="pro",
            partition="batch", cpus_per_task=1, mem="4G", time="00:45:00",
            python_module="lang/Python/3.11", container_module="tools/Apptainer",
            remote_env_file="~/.config/vibe-coding-planning/deepseek.env",
            python_bin="python3",
        )
    )
    from src.swe_verified_pce.hpc_executor import build_array_script
    script = build_array_script(
        config=config, batch_dir=tmp_path, indices=[0], attempt=1,
        worker_module=SWEBenchProPCEHPCExecutor.worker_module,
        label=SWEBenchProPCEHPCExecutor.label,
    )
    assert "-m src.swe_bench_pro_pce.worker" in script
    assert "#SBATCH --time=00:45:00" in script


def test_pro_agent_container_hides_host_without_disabling_network() -> None:
    assert SWEBenchProPCERunner._agent_container_run_args() == ["--containall"]


def test_prepared_pro_config_and_snapshot_are_frozen() -> None:
    config = load_swe_bench_pro_pce_config(
        ROOT / "configs/swe_bench_pro_pce_quick25_v1_20260906.yaml",
        require_api_keys=False,
    )
    assert config.dataset == "ScaleAI/SWE-bench_Pro"
    assert config.dataset_type == "pro"
    assert config.docker.workdir == "/app"
    assert config.hpc.max_task_attempts == 3
    assert config.hpc.time == "00:45:00"
    assert config.dataset_snapshot.name == "quick25-v1-20260904"
    assert config.image_manifest.name == "images.json"
