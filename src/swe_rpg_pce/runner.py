"""Bind RPG workdir/evaluator while reusing every established P/C/E phase."""
from dataclasses import replace
from functools import partial
import re

from src.environment.repository_history import RepositoryHistoryCache
from src.exceptions import FatalError
from src.swe_verified_pce.runner import SWEVerifiedPCERunner
from src.swe_rpg_pce.evaluator import evaluate_swe_rpg_apptainer


class SWERPGPCERunner(SWEVerifiedPCERunner):
    def __init__(self, config, capacity_window, **kwargs):
        kwargs.setdefault("evaluator", partial(evaluate_swe_rpg_apptainer, snapshot=config.dataset_snapshot))
        super().__init__(config, capacity_window, **kwargs)

    def _ensure_repository_history(self, case):
        cache = RepositoryHistoryCache(self.config.container.sif_cache_dir.parent / "repository-history-cache-v1")
        prepared = cache.validate(sif_sha256=case.image.sif_sha256, base_commit=case.base_commit)
        if prepared is None:
            raise FatalError(f"missing prepared base-ancestor history: {case.instance_id}")
        return prepared

    def run(self, case):
        original = self.config
        workdir = case.working_directory
        # Only the fixed repository-path literal is bound per case. No Plan,
        # candidate, trajectory, task description or patch is transformed.
        self.config = replace(original, docker=replace(original.docker, workdir=workdir),
            **{field: re.sub(r"/testbed(?=/|\b)", workdir, getattr(original, field))
               for field in ("plan_prompt", "plan_instance_template", "code_prompt", "code_instance_template")})
        try:
            return super().run(case)
        finally:
            self.config = original
