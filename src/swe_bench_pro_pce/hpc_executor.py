"""Slurm transport specialization for Pro PCE."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from src.swe_bench_pro_pce.models import SWEBenchProPCECase
from src.swe_bench_pro_pce.runner import SWEBenchProPCERunner
from src.swe_verified_pce.hpc_executor import (
    SWEVerifiedPCEHPCExecutor,
    execution_fingerprint,
    pce_semantic_sha256,
)


class SWEBenchProPCEHPCExecutor(SWEVerifiedPCEHPCExecutor):
    mode = "swe_bench_pro_pce"
    worker_module = "src.swe_bench_pro_pce.worker"
    label = "SWE-bench Pro PCE"
    case_from_dict = staticmethod(SWEBenchProPCECase.from_dict)
    runner_class = SWEBenchProPCERunner

    def semantic_sha256(self) -> str:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads(
            (self.config.dataset_snapshot / "manifest.json").read_text(encoding="utf-8")
        )
        return pce_semantic_sha256(
            self.config,
            additional_sources=tuple(
                sorted((root / "src" / "swe_bench_pro_pce").glob("*.py"))
            ),
            third_party_identity={
                "swe_bench_pro_evaluator_git_revision": manifest.get(
                    "official_evaluator_revision"
                )
            },
        )
    def execution_fingerprint(self, cases: Sequence[SWEBenchProPCECase]) -> str:
        return execution_fingerprint(
            self.config, cases, semantic_sha256=self.semantic_sha256()
        )
