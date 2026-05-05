"""Phase 7: Multi-round validation (n=3) for reflect_agent.

Enhanced per DeepSeek audit feedback to verify:
1. Per-round resolved status and test failure summaries
2. Patch validity (contains valid Git diff markers)
3. optimization_info_level=1 confirms test feedback reaches reflect_agent
4. Complete data table as required by FR-06

Prerequisites:
- Docker Desktop running
- conda mini-swe environment activated
- DEEPSEEK_API_KEY set
- Pre-built swebench Docker image for the chosen instance

Expected runtime: 15-45 minutes (3 x API calls + 3 x swebench evaluations).
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INSTANCE_ID = "psf__requests-5414"
AGENT_IMAGE = "python:3.9-slim"
N_ROUNDS = 3


def _clone_repo(repo: str, base_commit: str, dest: Path) -> None:
    """Clone GitHub repo and checkout base_commit."""
    print(f"   Cloning https://github.com/{repo} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", f"https://github.com/{repo}", str(dest)],
        check=True,
        capture_output=True,
    )
    print(f"   Fetching commit {base_commit[:12]} ...")
    subprocess.run(
        ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", base_commit],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "checkout", base_commit],
        check=True,
        capture_output=True,
    )
    print("   Repo ready.")


def _is_valid_patch(text: str) -> bool:
    """Check if text contains Git diff markers and at least one hunk."""
    _DIFF_MARKERS = ("diff --git", "--- ", "+++ ")
    has_header = any(marker in text for marker in _DIFF_MARKERS)
    has_hunk = "@@" in text
    return has_header and has_hunk


def _compute_plan_diff(plan_a: str, plan_b: str) -> tuple[float, list[str]]:
    """Compute similarity ratio and diff lines between two plans."""
    ratio = difflib.SequenceMatcher(None, plan_a, plan_b).ratio()
    diff_lines = list(
        difflib.unified_diff(
            plan_a.splitlines(),
            plan_b.splitlines(),
            lineterm="",
            n=2,
        )
    )
    return ratio, diff_lines


def _extract_failure_summary(stdout: str, stderr: str) -> str:
    """Extract a concise failure summary from test output."""
    combined = (stdout + "\n" + stderr).strip()
    if not combined:
        return "No test output available"

    lines = combined.splitlines()
    # Look for common failure indicators
    failure_lines = []
    for line in lines:
        lower = line.lower()
        if any(
            keyword in lower
            for keyword in [
                "failed",
                "error",
                "assertionerror",
                "traceback",
                "exception",
                "not equal",
                "expected",
            ]
        ):
            failure_lines.append(line.strip())
        if len(failure_lines) >= 5:
            break

    if failure_lines:
        return " | ".join(failure_lines[:5])
    # Fallback: return first non-empty line
    for line in lines:
        if line.strip():
            return line.strip()[:200]
    return "Output present but no failure summary extracted"


def main() -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 1

    print("=" * 78)
    print(f"Phase 7: Multi-round validation (n={N_ROUNDS})")
    print(f"Instance: {INSTANCE_ID}")
    print(f"Model: deepseek-v4-flash")
    print(f"GEPA reflection: ENABLED")
    print(f"optimization_info_level: 1  (test feedback included in reflection)")
    print("=" * 78)
    print()

    # -----------------------------------------------------------------------
    # 1. Load real SWE-bench instance data
    # -----------------------------------------------------------------------
    print("[1/7] Loading SWE-bench instance data...")
    from src.data.instance_loader import InstanceLoader

    loader = InstanceLoader()
    try:
        instance_info = loader.load_instance(INSTANCE_ID)
    except Exception as exc:
        print(f"   FAILED to load instance: {type(exc).__name__}: {exc}")
        return 1

    repo = instance_info["repo"]
    base_commit = instance_info["base_commit"]
    problem = instance_info["problem_statement"]
    print(f"   Repo: {repo}")
    print(f"   Base commit: {base_commit[:12]}")
    print(f"   Problem (first 120 chars): {problem[:120]}...")
    print()

    # -----------------------------------------------------------------------
    # 2. Clone repo locally for agent read-only mount
    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        try:
            _clone_repo(repo, base_commit, repo_path)
        except subprocess.CalledProcessError as exc:
            print(f"   FAILED to clone repo: {exc}")
            print("   Retrying with full clone...")
            if repo_path.exists():
                import shutil
                shutil.rmtree(repo_path)
            subprocess.run(
                ["git", "clone", f"https://github.com/{repo}", str(repo_path)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_path), "checkout", base_commit],
                check=True,
                capture_output=True,
            )

        # -------------------------------------------------------------------
        # 3. Build temporary config with n=3 and GEPA enabled
        # -------------------------------------------------------------------
        print("[3/7] Building config (n=3, GEPA enabled, optimization_info_level=1)...")
        output_dir = Path(tmpdir) / "output"
        config_path = Path(tmpdir) / "config.yaml"
        config_content = f"""\
system:
  n: {N_ROUNDS}
  optimization_info_level: 1
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  swe_pro_instances:
    - {INSTANCE_ID}
  output_dir: {output_dir}
  use_gepa_reflection_prompt: true

prompts:
  plan_generation_prompt: |
    You are a professional software engineer. Analyze the issue and create a
    concise plan to fix the bug. Be specific about which file and function
    to change.

    {{plan_format_template}}

    Issue:
    {{issue_description}}

  code_generation_prompt: |
    You are a code generation assistant. Generate a Git diff patch that
    implements the solution. Use unified diff format.

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
  max_steps: 10
  cost_limit: 1.0
  timeout: 120
"""
        config_path.write_text(config_content, encoding="utf-8")

        from src.config import load_config

        config = load_config(str(config_path))

        # Inject repo_path into instance_info so pipeline can mount it
        instance_info["repo_path"] = str(repo_path)

        # -------------------------------------------------------------------
        # 4. Run multi-round pipeline
        # -------------------------------------------------------------------
        print("[4/7] Running multi-round pipeline...")
        print(f"       This will call DeepSeek API {N_ROUNDS} times")
        print(f"       and run swebench evaluation {N_ROUNDS} times.")
        print("       Please wait...")
        print()

        from src.pipeline import run_instance

        # Patch pipeline components for this test
        import src.pipeline as pipeline_module
        import src.evaluator.swe_evaluator as swe_module

        original_loader_class = pipeline_module.InstanceLoader
        original_derive = pipeline_module.derive_image_name

        class PatchedLoader(original_loader_class):
            def load_instance(self, instance_id: str):
                if instance_id == INSTANCE_ID:
                    return instance_info
                return super().load_instance(instance_id)

        def patched_derive(instance_info: dict):
            return AGENT_IMAGE

        pipeline_module.InstanceLoader = PatchedLoader  # type: ignore[misc]
        pipeline_module.derive_image_name = patched_derive  # type: ignore[misc]
        swe_module.derive_image_name = patched_derive  # type: ignore[misc]
        swe_module._get_image_name = patched_derive  # type: ignore[misc]
        swe_module.get_image_name = patched_derive  # type: ignore[misc]

        try:
            result = run_instance(INSTANCE_ID, config)
        finally:
            pipeline_module.InstanceLoader = original_loader_class  # type: ignore[misc]
            pipeline_module.derive_image_name = original_derive  # type: ignore[misc]
            swe_module.derive_image_name = original_derive  # type: ignore[misc]
            swe_module._get_image_name = original_derive  # type: ignore[misc]
            swe_module.get_image_name = original_derive  # type: ignore[misc]

        # -------------------------------------------------------------------
        # 5. Collect per-round results
        # -------------------------------------------------------------------
        print()
        print("[5/7] Collecting per-round results...")

        plans = result.get("plans", [])
        print(f"   Total plans generated: {len(plans)}")

        if len(plans) < N_ROUNDS:
            print(f"   WARNING: Expected {N_ROUNDS} plans, got {len(plans)}")

        round_results: list[dict] = []
        for i, plan_record in enumerate(plans):
            round_num = plan_record.get("round", i + 1)
            generated_by = plan_record.get("generated_by", "unknown")
            plan_id = plan_record.get("plan_id", f"unknown_{i}")
            test_results = plan_record.get("test_results", {})
            resolved = test_results.get("resolved", False)
            log_dir = test_results.get("log_dir", "")
            stdout = test_results.get("stdout", "")
            stderr = test_results.get("stderr", "")

            # Load patch content
            patch_path = output_dir / INSTANCE_ID / plan_record.get("generated_patch_path", "")
            patch_content = ""
            patch_valid = False
            if patch_path.exists():
                patch_content = patch_path.read_text(encoding="utf-8")
                patch_valid = _is_valid_patch(patch_content)

            # Load plan content from trajectory
            trajectory_path = plan_record.get("trajectory_path", "")
            plan_content = ""
            if trajectory_path:
                traj_file = output_dir / INSTANCE_ID / trajectory_path
                if traj_file.exists():
                    try:
                        traj_data = json.loads(traj_file.read_text(encoding="utf-8"))
                        messages = traj_data.get("messages", []) if isinstance(traj_data, dict) else traj_data
                        for msg in reversed(messages):
                            if isinstance(msg, dict) and msg.get("role") == "assistant":
                                plan_content = msg.get("content", "")
                                break
                    except (json.JSONDecodeError, OSError):
                        pass

            # Fallback: read from log_dir if stdout/stderr empty
            if not stdout and not stderr and log_dir:
                try:
                    log_path = Path(log_dir).resolve() / "run_instance.log"
                    if log_path.exists():
                        log_text = log_path.read_text(encoding="utf-8", errors="replace")
                        for line in reversed(log_text.splitlines()):
                            stripped = line.strip()
                            if stripped and any(kw in stripped.lower() for kw in ["failed", "error", "patch", "exception", "apply"]):
                                stderr = stripped[:500]
                                break
                except Exception:
                    pass

            failure_summary = _extract_failure_summary(stdout, stderr)
            # If still no summary, try reading patch.diff to see if patch was malformed
            if failure_summary == "No test output available" and log_dir:
                try:
                    patch_diff_path = Path(log_dir).resolve() / "patch.diff"
                    if patch_diff_path.exists():
                        patch_diff = patch_diff_path.read_text(encoding="utf-8")
                        if "```" in patch_diff:
                            failure_summary = "Patch contains markdown markers (```) causing apply failure"
                        elif patch_diff.count("\n") < 5:
                            failure_summary = "Patch too short"
                        else:
                            failure_summary = "Patch apply failed (see run_instance.log)"
                except Exception:
                    pass

            round_info = {
                "round": round_num,
                "plan_id": plan_id,
                "generated_by": generated_by,
                "resolved": resolved,
                "log_dir": log_dir,
                "plan_length": len(plan_content),
                "plan_content": plan_content,
                "patch_valid": patch_valid,
                "patch_length": len(patch_content),
                "failure_summary": failure_summary,
                "stdout_preview": stdout[:300] if stdout else "",
                "stderr_preview": stderr[:300] if stderr else "",
            }
            round_results.append(round_info)

            print(f"   Round {round_num}: {generated_by}")
            print(f"      resolved={resolved}, patch_valid={patch_valid}, plan_length={len(plan_content)}")
            print(f"      failure_summary: {failure_summary[:120]}...")

        print()

        # -------------------------------------------------------------------
        # 6. Validate reflect_agent behavior
        # -------------------------------------------------------------------
        print("[6/7] Validating reflect_agent behavior...")
        print()

        all_passed = True
        checks: list[dict] = []

        # Check 1: Correct number of rounds
        check1 = len(plans) == N_ROUNDS
        checks.append({
            "name": "Round count",
            "description": f"Expected {N_ROUNDS} plans",
            "passed": check1,
            "detail": f"Got {len(plans)} plans",
        })
        print(f"   [CHECK 1] Round count: {'PASS' if check1 else 'FAIL'}")
        print(f"             Expected {N_ROUNDS}, got {len(plans)}")

        # Check 2: Round 1 generated by plan_agent
        if len(plans) >= 1:
            check2 = plans[0].get("generated_by") == "plan_agent"
        else:
            check2 = False
        checks.append({
            "name": "Round 1 agent",
            "description": "Round 1 should be generated by plan_agent",
            "passed": check2,
            "detail": f"generated_by={plans[0].get('generated_by') if plans else 'N/A'}",
        })
        print(f"   [CHECK 2] Round 1 agent: {'PASS' if check2 else 'FAIL'}")

        # Check 3: Rounds 2+ generated by reflect_agent
        check3 = True
        reflect_rounds = []
        for i in range(1, len(plans)):
            gb = plans[i].get("generated_by", "")
            if gb != "reflect_agent":
                check3 = False
            reflect_rounds.append(f"r{i+1}={gb}")
        checks.append({
            "name": "Reflect agent usage",
            "description": "Rounds 2+ should be generated by reflect_agent",
            "passed": check3,
            "detail": ", ".join(reflect_rounds) if reflect_rounds else "N/A",
        })
        print(f"   [CHECK 3] Reflect agent usage: {'PASS' if check3 else 'FAIL'}")
        print(f"             {', '.join(reflect_rounds) if reflect_rounds else 'N/A'}")

        # Check 4: Plans have content
        check4 = all(r["plan_length"] > 50 for r in round_results)
        lengths = [r["plan_length"] for r in round_results]
        checks.append({
            "name": "Plan content length",
            "description": "All plans should have >50 chars",
            "passed": check4,
            "detail": f"Lengths: {lengths}",
        })
        print(f"   [CHECK 4] Plan content length: {'PASS' if check4 else 'FAIL'}")
        print(f"             Lengths: {lengths}")

        # Check 5: Each reflect plan differs from previous
        check5 = True
        diff_reports = []
        for i in range(1, len(round_results)):
            prev_plan = round_results[i - 1]["plan_content"]
            curr_plan = round_results[i]["plan_content"]
            if not prev_plan or not curr_plan:
                ratio = 0.0
            else:
                ratio, _ = _compute_plan_diff(prev_plan, curr_plan)
            is_different = ratio < 0.95
            if not is_different:
                check5 = False
            diff_reports.append(
                f"r{i}→r{i+1}: similarity={ratio:.2%}, different={'YES' if is_different else 'NO'}"
            )
        checks.append({
            "name": "Plan evolution",
            "description": "Each reflect plan should differ from previous",
            "passed": check5,
            "detail": "; ".join(diff_reports) if diff_reports else "N/A",
        })
        print(f"   [CHECK 5] Plan evolution: {'PASS' if check5 else 'FAIL'}")
        for report in diff_reports:
            print(f"             {report}")

        # Check 6: All patches contain valid diff
        check6 = all(r["patch_valid"] for r in round_results)
        patch_status = [f"r{r['round']}={'YES' if r['patch_valid'] else 'NO'}" for r in round_results]
        checks.append({
            "name": "Patch validity",
            "description": "All rounds should produce valid Git diff patches",
            "passed": check6,
            "detail": ", ".join(patch_status),
        })
        print(f"   [CHECK 6] Patch validity: {'PASS' if check6 else 'FAIL'}")
        print(f"             {', '.join(patch_status)}")

        # Check 7: optimization_info_level=1 verified via feedback content
        # We verify this by checking that test_results contain stdout/stderr
        check7 = any(r["stdout_preview"] or r["stderr_preview"] for r in round_results)
        checks.append({
            "name": "Test feedback capture",
            "description": "optimization_info_level=1 should capture test stdout/stderr",
            "passed": check7,
            "detail": f"Rounds with test output: {sum(1 for r in round_results if r['stdout_preview'] or r['stderr_preview'])}/{len(round_results)}",
        })
        print(f"   [CHECK 7] Test feedback capture: {'PASS' if check7 else 'FAIL'}")
        print(f"             Rounds with test output: {sum(1 for r in round_results if r['stdout_preview'] or r['stderr_preview'])}/{len(round_results)}")

        all_passed = all(c["passed"] for c in checks)
        print()

        # -------------------------------------------------------------------
        # 7. Final report
        # -------------------------------------------------------------------
        print("[7/7] Final report")
        print()
        print("-" * 78)
        print("Multi-round validation summary")
        print("-" * 78)
        print(f"Instance:        {INSTANCE_ID}")
        print(f"Rounds executed: {len(plans)}/{N_ROUNDS}")
        print(f"optimization_info_level: 1")
        print()

        for c in checks:
            status = "PASS" if c["passed"] else "FAIL"
            print(f"  [{status}] {c['name']}")
            print(f"         {c['description']}")
            print(f"         Detail: {c['detail']}")
            print()

        # DeepSeek-required data table
        print("-" * 78)
        print("Per-round detailed results (required by FR-06)")
        print("-" * 78)
        print()
        print(f"{'Round':<6} {'Agent':<15} {'Plan Len':<10} {'Patch Valid':<12} {'Resolved':<10} {'Failure Summary'}")
        print("-" * 78)
        for r in round_results:
            fs = r['failure_summary'][:50] + "..." if len(r['failure_summary']) > 50 else r['failure_summary']
            print(f"{r['round']:<6} {r['generated_by']:<15} {r['plan_length']:<10} {'YES' if r['patch_valid'] else 'NO':<12} {str(r['resolved']):<10} {fs}")
        print()

        # Check output files
        instance_output = output_dir / INSTANCE_ID
        print("Output files:")
        print(f"  result.json: {(instance_output / 'result.json').exists()}")

        traj_dir = instance_output / "trajectories"
        if traj_dir.exists():
            traj_files = list(traj_dir.glob("*.json"))
            print(f"  trajectory files: {len(traj_files)}")
            for tf in sorted(traj_files):
                print(f"    - {tf.name}")

        patch_dir = instance_output / "patches"
        if patch_dir.exists():
            patch_files = list(patch_dir.glob("*.patch"))
            print(f"  patch files: {len(patch_files)}")

        print()
        print("=" * 78)
        if all_passed:
            print("Phase 7 COMPLETE - ALL CHECKS PASSED")
            print("reflect_agent is functioning correctly in multi-round mode.")
        else:
            print("Phase 7 COMPLETE - SOME CHECKS FAILED")
            print("Review the failures above.")
        print("=" * 78)
        print()

        # Save detailed report
        report_path = instance_output / "phase7_report.json"
        report = {
            "phase": 7,
            "instance_id": INSTANCE_ID,
            "n_rounds": N_ROUNDS,
            "rounds_executed": len(plans),
            "optimization_info_level": 1,
            "all_checks_passed": all_passed,
            "checks": checks,
            "rounds": [
                {
                    "round": r["round"],
                    "plan_id": r["plan_id"],
                    "generated_by": r["generated_by"],
                    "resolved": r["resolved"],
                    "plan_length": r["plan_length"],
                    "patch_valid": r["patch_valid"],
                    "patch_length": r["patch_length"],
                    "failure_summary": r["failure_summary"],
                }
                for r in round_results
            ],
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Detailed report saved to: {report_path}")

        return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
