#!/usr/bin/env python3
"""Archived targeted recovery for the historical remaining-133 batch.

Planning is the default and performs no Docker, evaluator, or LLM work.
Execution requires the explicit ``--execute`` flag.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import load_config  # noqa: E402
from src.data.instance_loader import InstanceLoader  # noqa: E402
from src.evaluator.polybench_patch_policy import (  # noqa: E402
    apply_polybench_patch_policy,
)
from src.evaluator.swe_evaluator import evaluate  # noqa: E402
from src.output.writer import OutputWriter  # noqa: E402

logger = logging.getLogger(__name__)


def _result_has_error(result_path: Path, message: str) -> bool:
    if not result_path.exists():
        return False
    data = json.loads(result_path.read_text(encoding="utf-8"))
    return any(
        (plan.get("test_results") or {}).get("error_info") == message
        for plan in data.get("plans", [])
    )


def _recover_patch(instance_dir: Path) -> str:
    patch_files = sorted((instance_dir / "patches").glob("*.patch"))
    if patch_files:
        return patch_files[-1].read_text(encoding="utf-8")

    trajectories = sorted((instance_dir / "trajectories").glob("*_code_gen_*.json"))
    if not trajectories:
        raise ValueError("code trajectory not found")
    data = json.loads(trajectories[-1].read_text(encoding="utf-8"))
    for message in reversed(data.get("messages", [])):
        content = str(message.get("content", ""))
        if content.startswith("diff --git "):
            return content
    raise ValueError("git patch not found in code trajectory")


def classify(source_dir: Path, log_dir: Path) -> dict[str, list[str]]:
    manifest = json.loads((source_dir / "sampled_instances.json").read_text())
    groups = {"evaluator_only": [], "full_pipeline": [], "complete": []}
    for instance_id in manifest["instances"]:
        instance_dir = source_dir / instance_id
        result_path = instance_dir / "result.json"
        log_path = log_dir / f"{instance_id}.log"
        log_text = log_path.read_text(errors="replace") if log_path.exists() else ""

        if "PolyBench evaluator submodules are unavailable" in log_text or (
            "poly_bench_evaluation is not installed" in log_text
        ):
            groups["evaluator_only"].append(instance_id)
        elif _result_has_error(result_path, "Code patch apply failed"):
            try:
                patch = _recover_patch(instance_dir)
                apply_polybench_patch_policy(patch)
            except Exception:
                groups["full_pipeline"].append(instance_id)
            else:
                groups["evaluator_only"].append(instance_id)
        elif not result_path.exists():
            try:
                _recover_patch(instance_dir)
            except Exception:
                groups["full_pipeline"].append(instance_id)
            else:
                # A complete code trajectory without result.json means the
                # Agent finished and evaluation failed afterward. Recovering
                # the submitted diff avoids an unnecessary LLM rerun and does
                # not depend on ephemeral batch logs.
                groups["evaluator_only"].append(instance_id)
        else:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            if not data.get("plans"):
                groups["full_pipeline"].append(instance_id)
            else:
                groups["complete"].append(instance_id)
    return groups


def _copy_artifacts(source: Path, target: Path) -> None:
    for name in ("plans", "trajectories"):
        src = source / name
        dst = target / name
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)


def run_evaluator_only(
    *,
    instance_ids: list[str],
    config_path: str,
    source_dir: Path,
    target_batch: str,
) -> None:
    config = load_config(config_path)
    loader = InstanceLoader(
        dataset=config.system.dataset,
        dataset_type=config.system.dataset_type,
        language_filter=config.system.language_filter,
    )
    dataset_short = config.system.dataset.rsplit("/", 1)[-1]

    for instance_id in instance_ids:
        source_instance = source_dir / instance_id
        target_instance = (
            Path(config.system.output_dir)
            / dataset_short
            / target_batch
            / instance_id
        )
        target_instance.mkdir(parents=True, exist_ok=True)
        _copy_artifacts(source_instance, target_instance)
        writer = OutputWriter(target_instance, "polybench_evaluator_retry")

        try:
            info = loader.load_instance(instance_id)
            recovered_patch = _recover_patch(source_instance)
            policy = apply_polybench_patch_policy(
                recovered_patch,
                test_patch=str(info.get("test_patch", "")),
            )
            test_results = evaluate(
                policy.patch,
                info,
                timeout=config.evaluator.timeout,
            )

            plan_files = sorted((source_instance / "plans").glob("*.md"))
            plan_text = (
                plan_files[-1].read_text(encoding="utf-8") if plan_files else ""
            )
            target_plan_files = sorted((target_instance / "plans").glob("*.md"))
            trajectory_files = sorted(
                (target_instance / "trajectories").glob("*_plan_gen_*.json")
            )
            writer.save_round(
                round_num=1,
                plan_id=f"plan_{instance_id}_r1",
                generated_by="plan_agent",
                plan_content=plan_text,
                patch_content=policy.patch,
                test_results=test_results,
                trajectory_path=(
                    str(trajectory_files[-1]) if trajectory_files else ""
                ),
                plan_path=(
                    str(target_plan_files[-1].relative_to(target_instance))
                    if target_plan_files
                    else None
                ),
            )
            writer.plans[-1]["patch_policy"] = {
                "kept_files": list(policy.kept_files),
                "removed_files": list(policy.removed_files),
                "test_overlap_files": list(policy.test_overlap_files),
                "recovered_from": str(source_instance),
            }
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.exception("[%s] Evaluator-only recovery failed", instance_id)
            writer.record_error(
                instance_id=instance_id,
                error_type=type(exc).__name__,
                message=str(exc),
            )
        finally:
            writer.finalize(
                instances=[instance_id],
                dataset=config.system.dataset,
                model=config.system.model,
                parameter_n=1,
                optimization_info_level=config.system.optimization_info_level,
            )


def run_full_pipeline(
    *,
    instance_ids: list[str],
    config_path: str,
    target_batch: str,
) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8"
    ) as manifest:
        json.dump({"instances": instance_ids}, manifest)
        manifest.flush()
        subprocess.run(
            [
                "bash",
                "scripts/run_batch.sh",
                "--config",
                config_path,
                "--instances",
                manifest.name,
                "--batch-id",
                target_batch,
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="output/SWE-PolyBench/polybench-remaining133-pct",
    )
    parser.add_argument("--logs", default="logs/batch")
    parser.add_argument(
        "--config", default="configs/polybench_remaining133_pct.yaml"
    )
    parser.add_argument(
        "--phase",
        choices=("plan", "evaluator", "full", "all"),
        default="plan",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--evaluator-batch", default="polybench-retry-evaluator"
    )
    parser.add_argument(
        "--full-batch", default="polybench-retry-full"
    )
    args = parser.parse_args()

    if not Path(args.source).is_absolute():
        args.source = str(REPO_ROOT / args.source)
    if not Path(args.logs).is_absolute():
        args.logs = str(REPO_ROOT / args.logs)

    groups = classify(Path(args.source), Path(args.logs))
    print(json.dumps({key: len(value) for key, value in groups.items()}, indent=2))
    if args.phase == "plan":
        print(json.dumps(groups, indent=2))
        return 0
    if not args.execute:
        parser.error("--execute is required for evaluator/full/all phases")

    if args.phase in ("evaluator", "all"):
        run_evaluator_only(
            instance_ids=groups["evaluator_only"],
            config_path=args.config,
            source_dir=Path(args.source),
            target_batch=args.evaluator_batch,
        )
    if args.phase in ("full", "all"):
        run_full_pipeline(
            instance_ids=groups["full_pipeline"],
            config_path=args.config,
            target_batch=args.full_batch,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
