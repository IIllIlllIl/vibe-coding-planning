"""Phase 6: Real end-to-end single-instance test (n=1).

Uses:
- Real DeepSeek API for plan_agent and code_agent
- Mock evaluator (returns success — swebench integration not ready)
- Mock instance data with a simple, well-defined bug
- Lightweight Docker image (python:3.9-slim)

This is the core acceptance test: if this passes, the full pipeline
orchestration + LLM integration works end-to-end.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from src.config import load_config
from src.pipeline import run_instance


# A simple bug that a capable model should be able to fix.
# The repo contains a single Python file with a function that has a bug.
MOCK_INSTANCE_ID = "test__e2e-simple-bug"
ISSUE_DESCRIPTION = """\
The function `get_even_numbers(numbers)` in `math_utils.py` is supposed to
return a list of even numbers from the input list. However, when the input
list is empty, it returns `None` instead of an empty list `[]`.

Expected behavior:
- `get_even_numbers([])` should return `[]`
- `get_even_numbers([1, 2, 3, 4])` should return `[2, 4]`

Actual behavior:
- `get_even_numbers([])` returns `None`

Please fix the function so it returns `[]` for empty input.
"""


def _create_mock_repo(repo_path: Path) -> None:
    """Create a minimal Python repo with a bug."""
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "math_utils.py").write_text(
        "def get_even_numbers(numbers):\n"
        "    if not numbers:\n"
        "        return None\n"  # BUG: should be return []
        "    return [n for n in numbers if n % 2 == 0]\n",
        encoding="utf-8",
    )
    (repo_path / "test_math_utils.py").write_text(
        "from math_utils import get_even_numbers\n\n"
        "def test_empty_list():\n"
        "    assert get_even_numbers([]) == []\n\n"
        "def test_mixed_list():\n"
        "    assert get_even_numbers([1, 2, 3, 4]) == [2, 4]\n",
        encoding="utf-8",
    )


def _create_instance_json(mock_dir: Path, repo_path: str) -> None:
    """Write mock instance JSON."""
    data = {
        "instance_id": MOCK_INSTANCE_ID,
        "repo": "test/e2e-repo",
        "image_name": "python:3.9-slim",
        "base_commit": "abc123",
        "problem_statement": ISSUE_DESCRIPTION,
        "repo_path": repo_path,
    }
    mock_dir.mkdir(parents=True, exist_ok=True)
    (mock_dir / f"{MOCK_INSTANCE_ID}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def main() -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 1

    print("=== Phase 6: Real E2E single-instance test (n=1) ===")
    print(f"Instance: {MOCK_INSTANCE_ID}")
    print(f"Model: deepseek-v4-flash")
    print()

    # Build temp config
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        _create_mock_repo(repo_path)

        mock_dir = Path(tmpdir) / "mock_instances"
        _create_instance_json(mock_dir, str(repo_path))

        config_path = Path(tmpdir) / "config.yaml"
        config_content = """\
system:
  n: 1
  optimization_info_level: 1
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  swe_pro_instances:
    - {instance_id}
  output_dir: {output_dir}
  use_gepa_reflection_prompt: false

prompts:
  plan_generation_prompt: |
    You are a software engineer. Analyze the issue and create a concise plan
    to fix the bug. Be specific about which file and function to change.

  code_generation_prompt: |
    You are a code generation assistant. Generate a Git diff patch that fixes
    the bug described in the issue. Use unified diff format.

    Plan:
    {{plan}}

    Issue:
    {{issue_description}}

    Generate a valid unified diff patch.

  plan_optimization_prompt: "Optimize the plan."
  plan_format_template: "## Analysis\n## Steps"

docker:
  image_builder_script: "./scripts/build.sh"
  workdir: "/testbed"
  codebase_mount_options: "ro"
  timeout: 30

agent:
  max_steps: 5
  cost_limit: 0.5
  timeout: 120
"""
        config_path.write_text(
            config_content.format(instance_id=MOCK_INSTANCE_ID, output_dir=f"{tmpdir}/output"),
            encoding="utf-8",
        )

        config = load_config(str(config_path))
        output_dir = Path(config.system.output_dir) / MOCK_INSTANCE_ID

        # Clean stale output
        if output_dir.exists():
            import shutil
            shutil.rmtree(output_dir)

        # Mock evaluator — swebench integration not ready for this test
        def mock_evaluate(patch, instance_info, timeout=1800):
            print(f"   [evaluator] Received patch ({len(patch)} chars)")
            # Check patch looks like a real diff
            has_diff = "diff --git" in patch or ("---" in patch and "+++" in patch)
            return {
                "resolved": has_diff,
                "stdout": "mock evaluation",
                "stderr": "",
                "log_dir": "",
            }

        # Mock instance loader to use our mock data
        from src.data import instance_loader

        original_loader_init = instance_loader.InstanceLoader.__init__

        def patched_init(self, mock_data_dir=None):
            original_loader_init(self, str(mock_dir))

        with patch.object(instance_loader.InstanceLoader, "__init__", patched_init):
            with patch("src.pipeline.evaluate", side_effect=mock_evaluate):
                print("[1/5] Starting pipeline...")
                result = run_instance(MOCK_INSTANCE_ID, config)

        # Verify outputs
        print("[2/5] Checking result structure...")
        assert "run_id" in result, "Missing run_id"
        assert "plans" in result, "Missing plans"
        assert len(result["plans"]) == 1, f"Expected 1 plan, got {len(result['plans'])}"

        plan_record = result["plans"][0]
        print(f"   plan_id: {plan_record['plan_id']}")
        print(f"   generated_by: {plan_record['generated_by']}")

        print("[3/5] Checking output files...")
        assert (output_dir / "result.json").exists(), "result.json missing"
        print("   result.json: OK")

        traj_dir = output_dir / "trajectories"
        if traj_dir.exists():
            traj_files = list(traj_dir.glob("*.json"))
            print(f"   trajectory files: {len(traj_files)}")

        patch_dir = output_dir / "patches"
        if patch_dir.exists():
            patch_files = list(patch_dir.glob("*.patch"))
            print(f"   patch files: {len(patch_files)}")
            if patch_files:
                patch_content = patch_files[0].read_text(encoding="utf-8")
                print(f"   patch preview ({min(200, len(patch_content))} chars):")
                for line in patch_content.splitlines()[:8]:
                    print(f"      {line}")

        # Show plan content
        plan_file = output_dir / "plans" / "plan_test__e2e-simple-bug_r1.txt"
        if plan_file.exists():
            plan_text = plan_file.read_text(encoding="utf-8")
            print("[4/5] Generated plan preview:")
            for line in plan_text.splitlines()[:6]:
                print(f"      {line}")
        else:
            # Plan might be in result.json only
            plan_content = plan_record.get("plan_content", "")
            if plan_content:
                print("[4/5] Generated plan preview:")
                for line in plan_content.splitlines()[:6]:
                    print(f"      {line}")

        print("[5/5] Phase 6 checks complete.")
        print()
        print("Phase 6 PASSED: Real E2E pipeline completed successfully")
        print()
        print("Summary:")
        print(f"  - Plan generated by: {plan_record['generated_by']}")
        print(f"  - Test resolved (mock): {plan_record['test_results']['resolved']}")
        print(f"  - Output dir: {output_dir}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
