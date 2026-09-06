"""Reuse the verified PCE phase isolation with the Pro evaluator."""

from __future__ import annotations

from functools import partial
from pathlib import Path
import re

from src.swe_bench_pro_pce.evaluator import evaluate_swe_bench_pro_apptainer
from src.swe_bench_pro_pce.repository import materialize_ancestor_only_repository
from src.swe_verified_pce.runner import SWEVerifiedPCERunner, checkpoint_identity


class SWEBenchProPCERunner(SWEVerifiedPCERunner):
    def __init__(self, config, capacity_window, **kwargs):
        kwargs.setdefault(
            "evaluator",
            partial(evaluate_swe_bench_pro_apptainer, snapshot=config.dataset_snapshot),
        )
        super().__init__(config, capacity_window, **kwargs)

    @staticmethod
    def _agent_container_run_args() -> list[str]:
        # Keep the host project, task manifests, and evaluator-only snapshot
        # outside Plan/Code while retaining the existing network policy.
        return ["--containall"]

    def _restore_agent_repository(
        self,
        env,
        case,
        *,
        phase: str,
        host_workdir: Path,
        evidence_dir: Path,
    ) -> None:
        super()._restore_agent_repository(
            env,
            case,
            phase=phase,
            host_workdir=host_workdir,
            evidence_dir=evidence_dir,
        )
        match = re.search(
            r"^git checkout ([0-9a-f]{40}) -- ",
            case.before_repo_set_cmd,
            flags=re.MULTILINE,
        )
        forbidden = (match.group(1),) if match else ()
        materialize_ancestor_only_repository(
            host_workdir,
            case.base_commit,
            evidence_dir=evidence_dir,
            forbidden_commits=forbidden,
        )


__all__ = ["SWEBenchProPCERunner", "checkpoint_identity"]
