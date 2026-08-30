"""Run the frozen four-case local Behavioral Checker/Reflection prompt unit."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from src.optimization.audit import text_sha256
from src.optimization.behavioral_adapter import BehavioralGEPAAdapter
from src.optimization.behavioral_models import (
    BehavioralCheckerOutput,
    BehavioralGEPACase,
    BehavioralRepositoryProxy,
)
from src.optimization.behavioral_repository import materialize_repository_proxy
from src.optimization.behavioral_runtime import (
    BehavioralLocalChecker,
    BehavioralLocalReflectionProposer,
    render_pre_p1_context,
    validate_behavioral_checker_output,
)
from src.optimization.config import (
    BehavioralRepositoryConfig,
    OfflineExecutionConfig,
    load_optimization_config,
)


TRAIN_CASE_IDS = (
    "00468b34-359e-43a2-b0bb-30677f1aee5e#first-plan",
    "01bdfe47-aa15-42ed-bb6a-71aedb7e8063#first-plan",
    "02bf0eec-e39b-4d26-876b-22a2af646441#first-plan",
    "039c7932-07b5-4d72-987c-d7fa070361c1#first-plan",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_train_cases(
    *, case_root: Path, frozen_root: Path, repositories_root: Path
) -> tuple[list[BehavioralGEPACase], list[dict[str, Any]]]:
    stage2 = _load(frozen_root / "stage2-first-plan-slice-v1-manifest.json")
    proxies = _load(frozen_root / "temporal-repository-proxy-v1-manifest.json")
    stage2_by_id = {item["case_id"]: item for item in stage2["cases"]}
    proxy_by_id = {item["case_id"]: item for item in proxies["cases"]}
    cases = []
    manifest_rows = []
    for case_id in TRAIN_CASE_IDS:
        stage2_row = stage2_by_id[case_id]
        proxy = proxy_by_id[case_id]
        source_path = case_root / stage2_row["case_path"]
        if _sha256(source_path) != stage2_row["case_sha256"]:
            raise ValueError(f"{case_id}: Stage-2 case hash mismatch")
        source = _load(source_path)
        signal = source["reflection_only"]["behavior_signal"]
        decision = {
            "explicit_approval": "ACCEPT",
            "explicit_rejection": "DO_NOT_ACCEPT",
        }[signal]
        repo = source["selection_provenance"]["repo_id"]
        owner, name = repo.split("/", 1)
        mirror_relpath = f"{owner}/{name}.git"
        mirror = repositories_root / mirror_relpath
        commit = proxy["proxy_commit"]
        tree = proxy["proxy_tree"]
        if not mirror.is_dir():
            raise ValueError(f"{case_id}: local smoke mirror missing")
        cases.append(
            BehavioralGEPACase(
                instance_id=case_id,
                split="train",
                decision=decision,
                confidence="high",
                signal=signal,
                pre_p1_context=tuple(source["checker_visible"]["events"]),
                proposed_plan_p1=source["checker_visible"]["proposed_plan"],
                repository=BehavioralRepositoryProxy(
                    repo=repo,
                    proxy_commit=commit,
                    instance_id=case_id,
                ),
                reflection_evidence=source["reflection_only"],
                audit_provenance={
                    "mirror_relpath": mirror_relpath,
                    "proxy_source": proxy["proxy_source"],
                    "recorded_branch_ref_available": proxy[
                        "recorded_branch_ref_available"
                    ],
                    "time_gap_seconds": proxy["time_gap_seconds"],
                    "repository_state_semantics": proxy["repository_state_semantics"],
                    "proxy_tree": tree,
                    "stage2_case_sha256": stage2_row["case_sha256"],
                },
            )
        )
        manifest_rows.append(
            {
                "case_id": case_id,
                "decision": decision,
                "repo": repo,
                "proxy_commit": commit,
                "proxy_tree": tree,
                "proxy_source": proxy["proxy_source"],
                "stage2_case_sha256": stage2_row["case_sha256"],
                "local_history_semantics": "snapshot_only_shallow_root",
            }
        )
    return cases, manifest_rows


def preflight(
    cases: list[BehavioralGEPACase],
    *,
    repositories_root: Path,
    workspace_root: Path,
) -> None:
    if [case.decision for case in cases].count("ACCEPT") != 2:
        raise ValueError("Stage B fixture must contain two ACCEPT cases")
    if [case.decision for case in cases].count("DO_NOT_ACCEPT") != 2:
        raise ValueError("Stage B fixture must contain two DO_NOT_ACCEPT cases")
    if [case.audit_provenance["proxy_source"] for case in cases].count(
        "recorded_branch"
    ) != 2:
        raise ValueError("Stage B fixture must contain two recorded proxies")
    if [case.audit_provenance["proxy_source"] for case in cases].count(
        "all_reachable_refs"
    ) != 2:
        raise ValueError("Stage B fixture must contain two fallback proxies")
    if len({case.repository.repo for case in cases}) != 4:
        raise ValueError("Stage B fixture must use four repositories")
    for case in cases:
        rendered = render_pre_p1_context(case.pre_p1_context)
        if json.loads(rendered) != list(case.pre_p1_context):
            raise ValueError(f"{case.instance_id}: context rendering changed events")
        with materialize_repository_proxy(
            mirror_path=(repositories_root / case.audit_provenance["mirror_relpath"]),
            proxy_commit=case.repository.proxy_commit,
            workspace_root=workspace_root,
        ) as checkout:
            import subprocess

            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            tree = subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if head != case.repository.proxy_commit:
                raise ValueError(f"{case.instance_id}: checkout commit mismatch")
            if tree != case.audit_provenance["proxy_tree"]:
                raise ValueError(f"{case.instance_id}: checkout tree mismatch")


def load_reused_checker_outputs(
    *,
    source_run: Path,
    cases: list[BehavioralGEPACase],
    guideline: str,
    checker_prompt_sha256: str,
    checker_instance_sha256: str,
) -> dict[str, BehavioralCheckerOutput]:
    """Load immutable Checker evidence for a Reflection-only prompt retry."""
    contract = _load(source_run / "run_contract.json")
    if (
        contract.get("uses_validation") is not False
        or contract.get("calls_gepa_optimize") is not False
    ):
        raise ValueError("source Checker run crossed the Stage B boundary")
    if contract.get("neutral_seed_sha256") != text_sha256(guideline):
        raise ValueError("source Checker run used a different candidate guideline")
    if (
        contract.get("checker_prompt_sha256") != checker_prompt_sha256
        or contract.get("checker_instance_sha256") != checker_instance_sha256
    ):
        raise ValueError("source Checker run used a different Checker prompt")
    outputs = {}
    for case in cases:
        path = source_run / "checker_trajectories" / f"{case.instance_id}.json"
        trajectory = _load(path)
        if trajectory.get("status") != "completed":
            raise ValueError(f"{case.instance_id}: source Checker is not complete")
        if trajectory.get("post_boundary_leakage") != []:
            raise ValueError(f"{case.instance_id}: source Checker contains leakage")
        if trajectory.get("candidate_sha256") != text_sha256(guideline):
            raise ValueError(f"{case.instance_id}: source Checker candidate mismatch")
        if trajectory.get("checkout_commit") != case.repository.proxy_commit:
            raise ValueError(f"{case.instance_id}: source Checker proxy mismatch")
        parsed = validate_behavioral_checker_output(trajectory["output"])
        messages = trajectory.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError(f"{case.instance_id}: source Checker trajectory missing")
        outputs[case.instance_id] = BehavioralCheckerOutput(
            predicted_accept=parsed.predicted_accept,
            decision_reason=parsed.decision_reason,
            repository_evidence=parsed.repository_evidence,
            trajectory=tuple(messages),
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/gepa_behavioral_acceptability_smoke_v1_20260830.yaml"),
    )
    parser.add_argument(
        "--identity",
        default="swe-chat-behavioral-stage-b-prompt-unit-v1-20260830",
    )
    parser.add_argument(
        "--reuse-checker-run",
        type=Path,
        help="Reuse validated Stage B Checker trajectories and run Reflection only.",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("output/SWE-chat/behavioral-gepa-smoke-v1-20260830"),
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path(
            "output/SWE-chat/behavioral-gepa-smoke-v1-20260830/"
            "stage-b-prompt-unit-run-v1"
        ),
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.run_dir.exists() and not args.preflight_only:
        raise FileExistsError(f"Stage B run identity already exists: {args.run_dir}")

    repo_root = Path(__file__).resolve().parents[2]
    frozen_root = (
        repo_root / "configs/frozen_swe_chat_cleaning/"
        "f66cca95b14caaa4177f7ed5eaa424608dadcffa"
    )
    repositories_root = (args.input_root / "repositories").resolve()
    workspace_root = (args.input_root / "workspaces").resolve()
    cases, fixture = load_train_cases(
        case_root=(args.input_root / "stage2-cases").resolve(),
        frozen_root=frozen_root,
        repositories_root=repositories_root,
    )
    preflight(
        cases,
        repositories_root=repositories_root,
        workspace_root=workspace_root,
    )
    if args.preflight_only:
        print(json.dumps({"status": "preflight_passed", "cases": fixture}, indent=2))
        return 0
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("DEEPSEEK_API_KEY is required for Stage B")

    config = load_optimization_config(args.config, require_api_keys=False)
    config = replace(
        config,
        run_dir=args.run_dir.resolve(),
        execution=OfflineExecutionConfig(backend="local"),
        behavioral_repository=BehavioralRepositoryConfig(
            backend="temporal_git_proxy",
            repositories_root=repositories_root,
            workspace_root=workspace_root,
        ),
    )
    args.run_dir.mkdir(parents=True)
    contract = {
        "schema_version": 1,
        "identity": args.identity,
        "uses_validation": False,
        "calls_gepa_optimize": False,
        "fixture": fixture,
        "neutral_seed_sha256": text_sha256(
            config.initial_rules_path.read_text(encoding="utf-8").strip()
        ),
        "checker_prompt_sha256": text_sha256(config.checker_prompt),
        "checker_instance_sha256": text_sha256(config.checker_instance_template),
        "reflection_prompt_sha256": text_sha256(config.reflection_prompt),
        "reflection_instance_sha256": text_sha256(config.reflection_instance_template),
        "checker_model": config.checker.model,
        "reflection_model": config.reflection.model,
    }
    if args.reuse_checker_run is not None:
        source_run = args.reuse_checker_run.resolve()
        contract["checker_outputs_reused_from"] = str(source_run)
        contract["source_run_contract_sha256"] = _sha256(
            source_run / "run_contract.json"
        )
    (args.run_dir / "run_contract.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    guideline = config.initial_rules_path.read_text(encoding="utf-8").strip()
    if args.reuse_checker_run is None:
        checker = BehavioralLocalChecker(config)
    else:
        reused = load_reused_checker_outputs(
            source_run=args.reuse_checker_run.resolve(),
            cases=cases,
            guideline=guideline,
            checker_prompt_sha256=text_sha256(config.checker_prompt),
            checker_instance_sha256=text_sha256(config.checker_instance_template),
        )

        def checker(
            case: BehavioralGEPACase, _guideline: str
        ) -> BehavioralCheckerOutput:
            return reused[case.instance_id]

    adapter = BehavioralGEPAAdapter(checker, run_dir=config.run_dir)
    evaluation = adapter.evaluate(cases, {"rules": guideline}, capture_traces=True)
    reflective = adapter.make_reflective_dataset(
        {"rules": guideline}, evaluation, ["rules"]
    )
    proposer = BehavioralLocalReflectionProposer(config)
    proposal = proposer({"rules": guideline}, reflective, ["rules"])
    result = {
        "schema_version": 1,
        "status": "completed",
        "checker_outputs": evaluation.outputs,
        "scores": evaluation.scores,
        "reflection_bundle": str(proposer.last_bundle),
        "proposal": proposal,
        "proposal_sha256": text_sha256(proposal["rules"]),
        "checker_outputs_reused": args.reuse_checker_run is not None,
    }
    (args.run_dir / "stage_b_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "completed", "run_dir": str(args.run_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
