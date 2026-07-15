"""Integration test skeleton for the full plan-code-test pipeline.

This test requires a fully configured environment with:
- Docker daemon running
- mini-swe-agent installed
- swebench installed
- SWE-bench Pro Docker images built (Phase 2; Phase 1 Verified images
  are pulled from Docker Hub on first use)
- DEEPSEEK_API_KEY environment variable set

Run with: pytest tests/test_integration.py -v -s --run-integration
"""

import os
import pytest


# Skip all tests in this file unless RUN_INTEGRATION=1 is set in the environment.
# This requires Docker, mini-swe-agent, swebench, SWE-bench Pro images, and
# DEEPSEEK_API_KEY. Historical prerequisites are archived under docs/archive/integration/.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to enable real integration tests "
    "(requires Docker + mini-swe-agent + swebench + DEEPSEEK_API_KEY)",
)


@pytest.fixture
def integration_config_path(tmp_path):
    """Create a minimal config.yaml for integration testing."""
    config_content = """\
system:
  n: 1
  optimization_info_level: 1
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  dataset: SWE-bench/SWE-bench_Verified
  instances:
    - astropy__astropy-12907
  output_dir: ./output
  batch_id: integration_test

prompts:
  plan_generation_prompt: |
    You are a software engineer. Design a plan to fix the issue.
    Do NOT modify files. The codebase is read-only.

  code_generation_prompt: |
    You are a code generation assistant. Generate a Git diff patch.

  reflection_prompt_template: |
    Previous plan:
    ```
    {prompt_template}
    ```
    Feedback:
    {inputs_outputs_feedback}
    Produce an improved plan within a single ``` fence.

docker:
  image_builder_script: "./scripts/build_docker_images.sh"
  workdir: "/testbed"
  codebase_mount_options: "ro"
  timeout: 30

agent:
  max_steps: 10
  cost_limit: 1.0
  timeout: 120
"""
    config_path = tmp_path / "integration_config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


def test_environment_ready():
    """Verify the integration environment is properly configured."""
    assert os.environ.get("DEEPSEEK_API_KEY"), (
        "DEEPSEEK_API_KEY must be set for integration tests"
    )

    try:
        import minisweagent  # noqa: F401
    except ImportError:
        pytest.fail("mini-swe-agent is not installed. Run: pip install mini-swe-agent~=1.0")

    try:
        import swebench  # noqa: F401
    except ImportError:
        pytest.fail("swebench is not installed. Run: pip install swebench>=4.1.0")


def test_single_round_smoke(integration_config_path):
    """Smoke test: run one round of plan-code-test on a real instance.

    This test will:
    1. Load a real SWE-bench instance (Verified by default)
    2. Start a Docker container
    3. Generate a plan via plan_agent (calling DeepSeek API)
    4. Generate a patch via code_agent (calling DeepSeek API)
    5. Evaluate the patch via swebench
    6. Save results and trajectories

    Expected: the pipeline completes without fatal errors, and output files
    (result.json, trajectories/*.json) exist in the output directory.
    """
    from src.config import load_config
    from src.pipeline import run_instance
    from pathlib import Path

    config = load_config(integration_config_path)
    instance_id = config.system.instances[0]

    result = run_instance(instance_id, config)

    # Assert result structure
    assert "run_id" in result
    assert "plans" in result
    assert len(result["plans"]) >= 1

    # Check that output files exist (output is stratified by dataset short
    # name AND batch_id: output/<dataset>/<batch_id>/<instance>/).
    batch_id = config.system.batch_id
    output_dir = Path(f"./output/SWE-bench_Verified/{batch_id}/{instance_id}")
    if output_dir.exists():
        result_file = output_dir / "result.json"
        assert result_file.exists(), "result.json should exist after pipeline run"

        traj_dir = output_dir / "trajectories"
        if traj_dir.exists():
            traj_files = list(traj_dir.glob("*.json"))
            assert len(traj_files) >= 2, (
                "Expected at least 2 trajectory files (plan + code)"
            )

        patch_dir = output_dir / "patches"
        if patch_dir.exists():
            patch_files = list(patch_dir.glob("*.patch"))
            assert len(patch_files) >= 1, "Expected at least 1 patch file"
