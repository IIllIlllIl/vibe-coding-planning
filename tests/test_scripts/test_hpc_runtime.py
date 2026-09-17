from pathlib import Path

import pytest

from scripts.hpc_runtime import (
    HpcLayout,
    reclaim_disposable_phase_workspaces,
    reclaim_submission_workdirs,
)


def test_layout_uses_canonical_scratch_root() -> None:
    layout = HpcLayout.for_user("twang")
    assert layout.run_state == Path(
        "/scratch/users/twang/vibe-coding-planning/run_state"
    )
    assert layout.sif_cache == Path(
        "/scratch/users/twang/vibe-coding-planning/shared/sif-cache"
    )


def test_reclaim_removes_only_staged_workdirs(tmp_path: Path) -> None:
    root = tmp_path / "hpc_runs" / "one-experiment"
    workdir = root / ".ulhpc_submit" / "runs" / "submission-1" / "workdir"
    workdir.mkdir(parents=True)
    (workdir / "code.py").write_text("pass\n", encoding="utf-8")
    log = workdir.parent / "slurm.log"
    log.write_text("retained\n", encoding="utf-8")
    result = reclaim_submission_workdirs(str(root))
    assert result["removed"] == 1
    assert not workdir.exists()
    assert log.read_text(encoding="utf-8") == "retained\n"


@pytest.mark.parametrize(
    "target",
    ["/", "/scratch", "~/hpc_runs"],
)
def test_reclaim_refuses_broad_roots(target: str) -> None:
    with pytest.raises(ValueError, match="refusing broad staging root"):
        reclaim_submission_workdirs(target)


def test_reclaim_dry_run_does_not_delete(tmp_path: Path) -> None:
    root = tmp_path / "hpc_runs" / "one-experiment"
    workdir = root / ".ulhpc_submit" / "runs" / "submission-1" / "workdir"
    workdir.mkdir(parents=True)
    result = reclaim_submission_workdirs(str(root), dry_run=True)
    assert result["removed"] == 0
    assert workdir.is_dir()


def test_reclaim_refuses_symlink_at_exact_workdir_location(tmp_path: Path) -> None:
    root = tmp_path / "hpc_runs" / "one-experiment"
    run = root / ".ulhpc_submit" / "runs" / "submission-1"
    run.mkdir(parents=True)
    retained = tmp_path / "retained"
    retained.mkdir()
    (run / "workdir").symlink_to(retained, target_is_directory=True)
    with pytest.raises(ValueError, match="unexpected staged workdir type"):
        reclaim_submission_workdirs(str(root))
    assert retained.is_dir()


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / "run-state" / "one-experiment"
    root.mkdir(parents=True)
    (root / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    (root / "hpc_tasks").mkdir()
    return root


def test_reclaim_removes_only_disposable_phase_workspaces(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    attempt = (
        root
        / "hpc_tasks/pce/fingerprint/attempts/task_0000/attempt_01"
    )
    workspace = attempt / "workspaces/history_preclean"
    workspace.mkdir(parents=True)
    (workspace / "repository.py").write_text("pass\n", encoding="utf-8")
    checkpoint = root / "hpc_tasks/pce/fingerprint/checkpoints/task_0000/plan.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    failure = attempt / "failure.json"
    failure.write_text("{}\n", encoding="utf-8")

    result = reclaim_disposable_phase_workspaces(str(root))

    assert result["matched"] == 1
    assert result["removed"] == 1
    assert not (attempt / "workspaces").exists()
    assert checkpoint.is_file()
    assert failure.is_file()


def test_reclaim_workspace_dry_run_is_non_mutating(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    workspace = (
        root
        / "hpc_tasks/pce/fingerprint/attempts/task_0000/attempt_01/workspaces"
    )
    workspace.mkdir(parents=True)

    result = reclaim_disposable_phase_workspaces(str(root), dry_run=True)

    assert result["matched"] == 1
    assert result["removed"] == 0
    assert workspace.is_dir()


def test_reclaim_workspace_unlinks_without_following_symlink(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    retained = tmp_path / "job-local-workspace"
    retained.mkdir()
    workspace = (
        root
        / "hpc_tasks/pce/fingerprint/attempts/task_0000/attempt_01/workspaces"
    )
    workspace.parent.mkdir(parents=True)
    workspace.symlink_to(retained, target_is_directory=True)

    result = reclaim_disposable_phase_workspaces(str(root))

    assert result["removed"] == 1
    assert not workspace.exists()
    assert retained.is_dir()


def test_reclaim_workspace_requires_exact_run_authority(tmp_path: Path) -> None:
    root = tmp_path / "not-a-run"
    (root / "hpc_tasks").mkdir(parents=True)
    with pytest.raises(ValueError, match="run_manifest.json"):
        reclaim_disposable_phase_workspaces(str(root))
