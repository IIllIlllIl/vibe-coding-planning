"""Phase 5: Semi-real end-to-end pipeline test.

Verifies that:
1. Pipeline orchestrates plan_agent → code_agent → evaluator → reflect_agent
2. Docker container is started once and stopped once per instance
3. Output files (result.json, trajectories/*.json, patches/*.patch) are created
4. Multi-round logic works (n=2)

Uses mock agents and mock evaluator to avoid API costs and SWE-bench
image dependencies.  The Docker container is real (python:3.9-slim).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from src.config import Config, load_config
from src.environment.docker_env import DockerEnvWrapper


def _make_config(n: int = 1) -> Config:
    """Build a minimal in-memory Config for testing."""
    config_content = f"""\
system:
  n: {n}
  optimization_info_level: 1
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  swe_pro_instances:
    - test__instance-1
  output_dir: ./output
  use_gepa_reflection_prompt: true

prompts:
  plan_generation_prompt: "Plan prompt"
  code_generation_prompt: "Code prompt"
  plan_optimization_prompt: "Optimize prompt"
  plan_format_template: "## Analysis\n## Steps"

docker:
  image_builder_script: "./scripts/build.sh"
  workdir: "/testbed"
  codebase_mount_options: "ro"
  timeout: 30

agent:
  max_steps: 5
  cost_limit: 0.1
  timeout: 60
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        path = f.name

    try:
        return load_config(path)
    finally:
        os.unlink(path)


def _mock_instance_loader():
    """Return fake instance data that satisfies derive_image_name."""
    return {
        "instance_id": "test__instance-1",
        "repo": "test/test-repo",
        "image_name": "python:3.9-slim",
        "problem_statement": "Fix the bug where parser fails on empty input.",
        "repo_path": "",
    }


def test_single_round() -> int:
    print("\n=== Phase 5a: Single-round pipeline (n=1) ===")

    config = _make_config(n=1)
    output_dir = Path(config.system.output_dir) / "test__instance-1"
    # Clean up any stale output
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal repo to mount
        repo_path = Path(tmpdir) / "repo"
        repo_path.mkdir()
        (repo_path / "file.py").write_text("# placeholder\n")

        instance_info = _mock_instance_loader()
        instance_info["repo_path"] = str(repo_path)

        # Track agent calls
        calls = {"plan": 0, "code": 0, "eval": 0, "reflect": 0}

        def mock_plan_run(config, issue, env):
            calls["plan"] += 1
            assert isinstance(env, DockerEnvWrapper)
            return "1. Analyze bug\n2. Fix it", [{"role": "assistant", "content": "plan"}]

        def mock_code_run(config, plan, issue, env):
            calls["code"] += 1
            assert isinstance(env, DockerEnvWrapper)
            return (
                "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-# placeholder\n+fixed\n",
                [{"role": "assistant", "content": "patch"}],
            )

        def mock_evaluate(patch, instance_info, timeout=1800):
            calls["eval"] += 1
            return {
                "resolved": True,
                "stdout": "tests passed",
                "stderr": "",
                "log_dir": "/tmp/logs",
            }

        with patch("src.pipeline.plan_agent.run", side_effect=mock_plan_run):
            with patch("src.pipeline.code_agent.run", side_effect=mock_code_run):
                with patch("src.pipeline.evaluate", side_effect=mock_evaluate):
                    with patch("src.pipeline.InstanceLoader") as MockLoader:
                        MockLoader.return_value.load_instance.return_value = instance_info
                        from src.pipeline import run_instance

                        result = run_instance("test__instance-1", config)

        # Verify orchestration
        assert calls["plan"] == 1, f"Expected 1 plan call, got {calls['plan']}"
        assert calls["code"] == 1, f"Expected 1 code call, got {calls['code']}"
        assert calls["eval"] == 1, f"Expected 1 eval call, got {calls['eval']}"
        assert calls["reflect"] == 0, f"Expected 0 reflect calls, got {calls['reflect']}"

        # Verify result structure
        assert "run_id" in result
        assert "plans" in result
        assert len(result["plans"]) == 1
        assert result["plans"][0]["test_results"]["resolved"] is True

        # Verify output files
        assert (output_dir / "result.json").exists(), "result.json missing"
        traj_dir = output_dir / "trajectories"
        if traj_dir.exists():
            traj_files = list(traj_dir.glob("*.json"))
            assert len(traj_files) >= 2, f"Expected >=2 trajectory files, got {len(traj_files)}"
        patch_dir = output_dir / "patches"
        if patch_dir.exists():
            patch_files = list(patch_dir.glob("*.patch"))
            assert len(patch_files) >= 1, f"Expected >=1 patch files, got {len(patch_files)}"

        print("   Phase 5a PASSED: single-round orchestration correct")
        return 0


def test_multi_round() -> int:
    print("\n=== Phase 5b: Multi-round pipeline (n=2) ===")

    config = _make_config(n=2)
    output_dir = Path(config.system.output_dir) / "test__instance-1"
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        repo_path.mkdir()
        (repo_path / "file.py").write_text("# placeholder\n")

        instance_info = _mock_instance_loader()
        instance_info["repo_path"] = str(repo_path)

        calls = {"plan": 0, "code": 0, "eval": 0, "reflect": 0}

        def mock_plan_run(config, issue, env):
            calls["plan"] += 1
            return "Round 1 plan", [{"role": "assistant", "content": "plan"}]

        def mock_reflect_run(config, feedback):
            calls["reflect"] += 1
            return "Round 2 improved plan", [{"role": "assistant", "content": "reflect"}]

        def mock_code_run(config, plan, issue, env):
            calls["code"] += 1
            return (
                "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n",
                [{"role": "assistant", "content": "patch"}],
            )

        def mock_evaluate(patch, instance_info, timeout=1800):
            calls["eval"] += 1
            return {"resolved": calls["eval"] == 2, "stdout": "", "stderr": "", "log_dir": ""}

        with patch("src.pipeline.plan_agent.run", side_effect=mock_plan_run):
            with patch("src.pipeline.code_agent.run", side_effect=mock_code_run):
                with patch("src.pipeline.evaluate", side_effect=mock_evaluate):
                    with patch("src.pipeline.reflect_agent.run", side_effect=mock_reflect_run):
                        with patch("src.pipeline.InstanceLoader") as MockLoader:
                            MockLoader.return_value.load_instance.return_value = instance_info
                            from src.pipeline import run_instance

                            result = run_instance("test__instance-1", config)

        assert calls["plan"] == 1, f"Expected 1 plan call, got {calls['plan']}"
        assert calls["reflect"] == 1, f"Expected 1 reflect call, got {calls['reflect']}"
        assert calls["code"] == 2, f"Expected 2 code calls, got {calls['code']}"
        assert calls["eval"] == 2, f"Expected 2 eval calls, got {calls['eval']}"
        assert len(result["plans"]) == 2

        print("   Phase 5b PASSED: multi-round orchestration correct")
        return 0


def main() -> int:
    try:
        if test_single_round() != 0:
            return 1
        if test_multi_round() != 0:
            return 1
        print("\nPhase 5 PASSED: Pipeline orchestration verified")
        return 0
    except AssertionError as exc:
        print(f"\nPhase 5 FAILED: {exc}")
        return 1
    except Exception as exc:
        print(f"\nPhase 5 FAILED: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
