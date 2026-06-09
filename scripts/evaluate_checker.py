"""Evaluate the existing plan checker against PCC or saved PCT results.

The ``--input-results`` mode is checker-only: it reuses saved PCT plans and
resolved labels, starts the corresponding repository container, and invokes
``src.agents.check_agent.run`` without generating code or running an evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents import check_agent  # noqa: E402
from src.config import Config, load_config  # noqa: E402
from src.data.instance_loader import InstanceLoader  # noqa: E402
from src.environment.docker_env import (  # noqa: E402
    DockerEnvWrapper,
    cleanup_docker_image_cache,
)
from src.evaluator.swe_evaluator import derive_image_name  # noqa: E402
from src.pipeline_check import run_instance  # noqa: E402
from src.rules.rule_loader import (  # noqa: E402
    format_rules_for_prompt,
    load_aggregated_rules,
)

logger = logging.getLogger(__name__)

_PLAN_TIMESTAMP_RE = re.compile(r"(\d{8}T\d{6})")
_EVALUATOR_RETRY_RUN_ID = "polybench_evaluator_retry"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _resolve_stored_path(path: str) -> Path:
    stored_path = Path(path)
    return stored_path if stored_path.is_absolute() else REPO_ROOT / stored_path


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(record)
    return records


def _plan_timestamp(plan_path: Path, result: dict[str, Any]) -> str:
    match = _PLAN_TIMESTAMP_RE.search(plan_path.name)
    if match:
        return match.group(1)
    run_id = str(result.get("run_id", ""))
    match = _PLAN_TIMESTAMP_RE.search(run_id)
    return match.group(1) if match else "99999999T999999"


def _valid_polybench_label(
    instance_id: str, test_results: Any
) -> tuple[bool, str]:
    if not isinstance(test_results, dict):
        return False, "missing test_results"
    if not isinstance(test_results.get("resolved"), bool):
        return False, "resolved is not boolean"
    if test_results.get("error_info") not in (None, ""):
        return False, "evaluator error"
    report = test_results.get("report")
    if not isinstance(report, dict):
        return False, "missing evaluator report"
    instance_report = report.get(instance_id)
    if not isinstance(instance_report, dict):
        return False, "missing instance evaluator report"
    if instance_report.get("patch_applied") is not True:
        return False, "patch was not applied"
    return True, ""


def _scan_pct_candidates(
    pct_root: Path, allowed_ids: set[str]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    exclusions: list[dict[str, Any]] = []

    for result_path in sorted(pct_root.glob("*/*/result.json")):
        instance_id = result_path.parent.name
        if instance_id not in allowed_ids:
            continue
        try:
            result_bytes = result_path.read_bytes()
            result = json.loads(result_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            exclusions.append(
                {
                    "instance_id": instance_id,
                    "source_result_path": _portable_path(result_path),
                    "reason": f"unreadable result: {exc}",
                }
            )
            continue

        plans = result.get("plans")
        if not isinstance(plans, list) or not plans:
            continue
        plan_record = next(
            (plan for plan in plans if plan.get("round") == 1), plans[0]
        )
        relative_plan_path = plan_record.get("plan_path")
        if not relative_plan_path:
            continue
        plan_path = result_path.parent / str(relative_plan_path)
        if not plan_path.is_file():
            exclusions.append(
                {
                    "instance_id": instance_id,
                    "source_result_path": _portable_path(result_path),
                    "reason": f"plan file not found: {plan_path}",
                }
            )
            continue
        plan_bytes = plan_path.read_bytes()
        if not plan_bytes.strip():
            continue

        valid, invalid_reason = _valid_polybench_label(
            instance_id, plan_record.get("test_results")
        )
        patch_policy = plan_record.get("patch_policy") or {}
        recovered_from = (
            patch_policy.get("recovered_from")
            if isinstance(patch_policy, dict)
            else None
        )
        pct_source_dir = Path(recovered_from) if recovered_from else result_path.parent
        pct_result_path = pct_source_dir / "result.json"
        pct_run_id = str(result.get("run_id", ""))
        if recovered_from and pct_result_path.is_file():
            try:
                original_result = json.loads(
                    pct_result_path.read_text(encoding="utf-8")
                )
                pct_run_id = str(original_result.get("run_id", pct_run_id))
            except (OSError, json.JSONDecodeError):
                pass
        if pct_run_id == _EVALUATOR_RETRY_RUN_ID:
            pct_run_id = f"run_{_plan_timestamp(plan_path, result)}Z"
        pct_plan_path = pct_source_dir / "plans" / plan_path.name
        candidates.setdefault(instance_id, []).append(
            {
                "instance_id": instance_id,
                "plan": plan_bytes.decode("utf-8"),
                "plan_sha256": _sha256(plan_bytes),
                "plan_generated_at": _plan_timestamp(plan_path, result),
                "resolved": (plan_record.get("test_results") or {}).get(
                    "resolved"
                ),
                "valid_label": valid,
                "invalid_reason": invalid_reason,
                "source_batch": pct_source_dir.parent.name,
                "source_result_path": _portable_path(pct_result_path),
                "source_plan_path": _portable_path(
                    pct_plan_path if pct_plan_path.is_file() else plan_path
                ),
                "label_source_result_path": _portable_path(result_path),
                "result_sha256": _sha256(result_bytes),
                "pct_run_id": pct_run_id,
                "is_evaluator_retry": (
                    result.get("run_id") == _EVALUATOR_RETRY_RUN_ID
                ),
                "recovered_from": str(recovered_from or ""),
            }
        )
    return candidates, exclusions


def _select_earliest_success(
    records: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Select the earliest distinct plan that has a valid evaluation label."""
    by_plan: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (record["plan_sha256"], record["plan_generated_at"])
        by_plan.setdefault(key, []).append(record)

    successful_plans: list[tuple[str, str, dict[str, Any]]] = []
    for (plan_sha256, plan_generated_at), plan_records in by_plan.items():
        valid_records = [r for r in plan_records if r["valid_label"]]
        if not valid_records:
            continue
        # Prefer the original successful PCT result. Evaluator-only recovery is
        # used only when the original label was invalid.
        label_record = min(
            valid_records,
            key=lambda r: (
                r["is_evaluator_retry"],
                r["source_result_path"],
            ),
        )
        successful_plans.append(
            (plan_generated_at, plan_sha256, label_record)
        )

    if not successful_plans:
        return None
    return min(successful_plans, key=lambda item: (item[0], item[1]))[2]


def build_pct_checker_input(
    *,
    config: Config,
    pct_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Build a stable checker input from the earliest successful PCT per case."""
    ordered_ids = list(dict.fromkeys(config.system.instances))
    candidates, scan_exclusions = _scan_pct_candidates(
        pct_root, set(ordered_ids)
    )
    loader = InstanceLoader(
        dataset=config.system.dataset,
        dataset_type=config.system.dataset_type,
        language_filter=config.system.language_filter,
    )
    cases: list[dict[str, Any]] = []
    exclusions = list(scan_exclusions)

    for instance_id in ordered_ids:
        selected = _select_earliest_success(candidates.get(instance_id, []))
        if selected is None:
            reasons = sorted(
                {
                    record["invalid_reason"]
                    for record in candidates.get(instance_id, [])
                    if record["invalid_reason"]
                }
            )
            exclusions.append(
                {
                    "instance_id": instance_id,
                    "reason": (
                        "no valid successful PCT"
                        + (f": {', '.join(reasons)}" if reasons else "")
                    ),
                }
            )
            continue
        try:
            instance_info = loader.load_instance(instance_id)
        except Exception as exc:
            exclusions.append(
                {
                    "instance_id": instance_id,
                    "reason": f"instance metadata load failed: {exc}",
                }
            )
            continue
        issue_description = (
            instance_info.get("problem_statement")
            or instance_info.get("issue_description")
            or ""
        )
        if not issue_description:
            exclusions.append(
                {
                    "instance_id": instance_id,
                    "reason": "missing issue description",
                }
            )
            continue
        cases.append(
            {
                "instance_id": instance_id,
                "dataset": config.system.dataset,
                "issue_description": issue_description,
                "plan": selected["plan"],
                "resolved": selected["resolved"],
                "source_batch": selected["source_batch"],
                "source_result_path": selected["source_result_path"],
                "source_plan_path": selected["source_plan_path"],
                "pct_run_id": selected["pct_run_id"],
                "pct_started_at": selected["plan_generated_at"],
                "plan_generated_at": selected["plan_generated_at"],
                "plan_sha256": selected["plan_sha256"],
                "result_sha256": selected["result_sha256"],
                "label_source_result_path": selected[
                    "label_source_result_path"
                ],
                "label_recovered_from": selected["recovered_from"],
            }
        )

    _write_jsonl(output_path, cases)
    exclusions_path = output_path.with_name("exclusions.json")
    exclusions_path.write_text(
        json.dumps(exclusions, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    cases_sha256 = _sha256(output_path.read_bytes())
    manifest = {
        "schema_version": 1,
        "selection_policy": "earliest-successful-pct-v1",
        "dataset": config.system.dataset,
        "pct_root": _portable_path(pct_root),
        "cases_path": _portable_path(output_path),
        "cases_sha256": cases_sha256,
        "configured_instances": len(ordered_ids),
        "selected_instances": len(cases),
        "excluded_instances": len(ordered_ids) - len(cases),
        "resolved": sum(case["resolved"] is True for case in cases),
        "unresolved": sum(case["resolved"] is False for case in cases),
    }
    output_path.with_name("manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def publish_pct_checker_snapshot(
    *,
    config: Config,
    pct_root: Path,
    snapshot_root: Path,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Publish an immutable, content-addressed checker dataset snapshot."""
    snapshot_root.mkdir(parents=True, exist_ok=True)
    build_dir = Path(
        tempfile.mkdtemp(prefix=".building-", dir=str(snapshot_root))
    )
    try:
        manifest = build_pct_checker_input(
            config=config,
            pct_root=pct_root,
            output_path=build_dir / "cases.jsonl",
        )
        timestamp = created_at or datetime.now(timezone.utc)
        index_path = snapshot_root / "index.json"
        index = {"schema_version": 1, "snapshots": []}
        if index_path.is_file():
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                index = loaded
                index.setdefault("snapshots", [])

        matching_entry = next(
            (
                item
                for item in index["snapshots"]
                if item.get("cases_sha256") == manifest["cases_sha256"]
                and _resolve_stored_path(
                    str(item.get("snapshot_path", ""))
                ).is_dir()
            ),
            None,
        )
        if matching_entry:
            snapshot_dir = _resolve_stored_path(
                str(matching_entry["snapshot_path"])
            )
            existing_manifest = json.loads(
                (snapshot_dir / "manifest.json").read_text(encoding="utf-8")
            )
            shutil.rmtree(build_dir)
            manifest = existing_manifest
        else:
            snapshot_id = (
                f"{timestamp:%Y%m%d}_{manifest['selected_instances']}_"
                f"{manifest['cases_sha256'][:12]}"
            )
            snapshot_dir = snapshot_root / snapshot_id
            if snapshot_dir.exists():
                raise ValueError(
                    f"Snapshot ID collision with different content: {snapshot_dir}"
                )
            manifest.update(
                {
                    "snapshot_id": snapshot_id,
                    "snapshot_path": _portable_path(snapshot_dir),
                    "created_at": timestamp.isoformat(),
                    "cases_path": _portable_path(snapshot_dir / "cases.jsonl"),
                    "immutable": True,
                }
            )
            (build_dir / "manifest.json").write_text(
                json.dumps(
                    manifest, indent=2, ensure_ascii=False, sort_keys=True
                ),
                encoding="utf-8",
            )
            build_dir.rename(snapshot_dir)

        entry = {
            "snapshot_id": manifest["snapshot_id"],
            "snapshot_path": manifest["snapshot_path"],
            "created_at": manifest["created_at"],
            "cases_sha256": manifest["cases_sha256"],
            "selected_instances": manifest["selected_instances"],
            "excluded_instances": manifest["excluded_instances"],
            "resolved": manifest["resolved"],
            "unresolved": manifest["unresolved"],
        }
        snapshots = [
            item
            for item in index["snapshots"]
            if item.get("snapshot_id") != entry["snapshot_id"]
        ]
        snapshots.append(entry)
        snapshots.sort(key=lambda item: (item["created_at"], item["snapshot_id"]))
        index.update(
            {
                "schema_version": 1,
                "selection_policy": manifest["selection_policy"],
                "latest_snapshot_id": manifest["snapshot_id"],
                "latest_cases_path": manifest["cases_path"],
                "snapshots": snapshots,
            }
        )
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return manifest
    finally:
        if build_dir.exists():
            shutil.rmtree(build_dir)


def _compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for result in results:
        check_passed = result["check_result"]["passed"]
        resolved = result["test_results"]["resolved"]
        if check_passed and resolved:
            tp += 1
        elif check_passed:
            fp += 1
        elif resolved:
            fn += 1
        else:
            tn += 1

    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    denominator = math.sqrt(
        (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    )
    mcc = ((tp * tn - fp * fn) / denominator) if denominator else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "total": total,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "balanced_accuracy": (recall + specificity) / 2,
        "mcc": mcc,
        "checker_pass_rate": (tp + fp) / total if total else 0.0,
        "resolved_prevalence": (tp + fn) / total if total else 0.0,
    }


def _collect_violation_stats(
    results: list[dict[str, Any]], filter_fn: Any
) -> dict[str, int]:
    stats: dict[str, int] = {}
    for result in results:
        if not filter_fn(result):
            continue
        for violation in result["check_result"].get("violations", []):
            rule_text = violation.get("rule", "")
            if rule_text:
                stats[rule_text] = stats.get(rule_text, 0) + 1
    return stats


def _save_results(
    output_dir: Path,
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    input_sha256: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = _compute_metrics(results)
    metrics["checker_errors"] = len(errors)
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_jsonl(output_dir / "predictions.jsonl", results)
    _write_jsonl(output_dir / "errors.jsonl", errors)

    false_positives = [
        r["instance_id"]
        for r in results
        if r["check_result"]["passed"] and not r["test_results"]["resolved"]
    ]
    false_negatives = [
        r["instance_id"]
        for r in results
        if not r["check_result"]["passed"] and r["test_results"]["resolved"]
    ]
    summary = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "input_sha256": input_sha256,
        "metrics": metrics,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "violation_frequency": sorted(
            _collect_violation_stats(results, lambda _: True).items(),
            key=lambda item: (-item[1], item[0]),
        ),
    }
    results_path = output_dir / "results.json"
    results_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return results_path


def _load_instance_ids(path: str) -> list[str]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Instance list not found: {path}")
    text = source.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item) for item in data]
        if isinstance(data, dict):
            return [
                str(case.get("instance_id", case))
                for case in data.get("cases", [])
            ]
    except json.JSONDecodeError:
        pass
    return [line.strip() for line in text.splitlines() if line.strip()]


def _run_checker_case(
    case: dict[str, Any],
    config: Config,
    rules_text: str,
    output_dir: Path,
    loader: InstanceLoader,
) -> dict[str, Any]:
    required = ("instance_id", "issue_description", "plan", "resolved")
    missing = [key for key in required if key not in case]
    if missing:
        raise ValueError(f"checker input missing fields: {', '.join(missing)}")
    if not isinstance(case["resolved"], bool):
        raise ValueError("checker input resolved label must be boolean")

    instance_id = str(case["instance_id"])
    instance_info = loader.load_instance(instance_id)
    docker = DockerEnvWrapper(config.docker)
    dataset_type = instance_info.get("dataset_type", "")
    workdir = (
        "/app"
        if dataset_type == "pro" or "dockerhub_tag" in instance_info
        else config.docker.workdir
    )
    instance_dir = output_dir / "instances" / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)

    try:
        docker.start(
            image=derive_image_name(instance_info),
            workdir=workdir,
            mount_source=instance_info.get("repo_path", ""),
            timeout=config.agent.timeout,
            instance_info=instance_info,
        )
        check_result, trajectory = check_agent.run(
            config,
            str(case["plan"]),
            str(case["issue_description"]),
            rules_text,
            docker,
        )
        if check_result.get("_parse_error"):
            raise ValueError(check_result.get("overall_assessment", "parse error"))
        (instance_dir / "check_result.json").write_text(
            json.dumps(check_result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (instance_dir / "trajectory.json").write_text(
            json.dumps(
                {
                    "role": "check",
                    "instance_id": instance_id,
                    "messages": trajectory,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        prediction = {
            "instance_id": instance_id,
            "check_result": check_result,
            "test_results": {"resolved": case["resolved"]},
            "source": {
                key: case.get(key)
                for key in (
                    "source_batch",
                    "source_result_path",
                    "source_plan_path",
                    "plan_sha256",
                )
            },
        }
        _write_json(instance_dir / "prediction.json", prediction)
        return prediction
    finally:
        docker.stop()
        if config.docker.delete_images_after_instance:
            cleanup_docker_image_cache(config.docker.max_cached_images)


def run_checker_only(
    *,
    config: Config,
    input_path: Path,
    output_dir: Path,
    rules_text_override: str | None = None,
    resume: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases = _read_jsonl(input_path)
    if rules_text_override is None:
        rules = load_aggregated_rules(config.checker.rules_path)
        rules_text = format_rules_for_prompt(rules)
    else:
        rules_text = rules_text_override
    loader = InstanceLoader(
        dataset=config.system.dataset,
        dataset_type=config.system.dataset_type,
        language_filter=config.system.language_filter,
    )
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        instance_id = str(case.get("instance_id", "unknown"))
        prediction_path = (
            output_dir / "instances" / instance_id / "prediction.json"
        )
        if resume and prediction_path.is_file():
            try:
                prediction = json.loads(
                    prediction_path.read_text(encoding="utf-8")
                )
                source = prediction.get("source") or {}
                if (
                    prediction.get("instance_id") == instance_id
                    and isinstance(
                        (prediction.get("check_result") or {}).get("passed"),
                        bool,
                    )
                    and (prediction.get("test_results") or {}).get("resolved")
                    is case.get("resolved")
                    and source.get("plan_sha256") == case.get("plan_sha256")
                ):
                    logger.info(
                        "[%d/%d] Resuming completed %s",
                        index,
                        len(cases),
                        instance_id,
                    )
                    results.append(prediction)
                    continue
            except (OSError, json.JSONDecodeError):
                logger.warning("Ignoring invalid prediction cache: %s", prediction_path)
        logger.info("[%d/%d] Checking %s", index, len(cases), instance_id)
        try:
            results.append(
                _run_checker_case(
                    case, config, rules_text, output_dir, loader
                )
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.exception("[%s] Checker-only evaluation failed", instance_id)
            errors.append(
                {
                    "instance_id": instance_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    _save_results(
        output_dir,
        results,
        errors,
        input_sha256=_sha256(input_path.read_bytes()),
    )
    return results, errors


def _run_legacy_pcc(args: argparse.Namespace, config: Config) -> int:
    dataset = args.dataset or "ScaleAI/SWE-bench_Pro"
    configured_instance_ids = config.system.instances
    config = replace(
        config,
        system=replace(
            config.system,
            n=1,
            dataset=dataset,
            instances=[],
            batch_id=f"checker_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}",
            skip_completed_rounds=False,
        ),
        checker=replace(config.checker, enabled=True),
    )
    if args.instance:
        instance_ids = [args.instance]
    elif args.instances:
        instance_ids = _load_instance_ids(args.instances)
    else:
        instance_ids = configured_instance_ids
    if not instance_ids:
        logger.error("No instances specified")
        return 1

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    output_dir = Path(args.output)
    for instance_id in instance_ids:
        try:
            result = run_instance(instance_id, config)
            results.append(result)
        except Exception as exc:
            errors.append(
                {
                    "instance_id": instance_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    _save_results(output_dir, results, errors)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the plan checker using PCC or saved PCT results."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", default="./output/checker_eval/run1")
    parser.add_argument("--instances")
    parser.add_argument("--instance")
    parser.add_argument("--dataset")
    parser.add_argument("--build-input", action="store_true")
    parser.add_argument(
        "--pct-root", default="./output/SWE-PolyBench"
    )
    parser.add_argument("--input-output")
    parser.add_argument(
        "--snapshot-root",
        help="Publish an immutable dataset snapshot under this directory",
    )
    parser.add_argument("--input-results")
    args = parser.parse_args(argv)
    _setup_logging()
    config = load_config(args.config)

    if args.build_input and args.input_results:
        parser.error("--build-input and --input-results are mutually exclusive")
    if args.build_input:
        if bool(args.input_output) == bool(args.snapshot_root):
            parser.error(
                "exactly one of --input-output or --snapshot-root is required "
                "with --build-input"
            )
        if args.snapshot_root:
            manifest = publish_pct_checker_snapshot(
                config=config,
                pct_root=Path(args.pct_root),
                snapshot_root=Path(args.snapshot_root),
            )
        else:
            manifest = build_pct_checker_input(
                config=config,
                pct_root=Path(args.pct_root),
                output_path=Path(args.input_output),
            )
        logger.info("Built checker input: %s", json.dumps(manifest))
        return 0
    if args.input_results:
        run_checker_only(
            config=replace(
                config, checker=replace(config.checker, enabled=True)
            ),
            input_path=Path(args.input_results),
            output_dir=Path(args.output),
        )
        return 0
    return _run_legacy_pcc(args, config)


if __name__ == "__main__":
    sys.exit(main())
