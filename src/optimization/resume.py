"""Project-side state required for reproducible GEPA process restarts."""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pickle
import random
import tempfile
from typing import Any

from gepa.core.state import GEPAState
from gepa.strategies.batch_sampler import EpochShuffledBatchSampler
from gepa.strategies.candidate_selector import ParetoCandidateSelector

from src.optimization.audit import text_sha256
from src.optimization.config import OptimizationConfig
from src.optimization.models import GEPACase

MANIFEST_VERSION = 1
RESUME_STATE_VERSION = 1


class IncompatibleOptimizationRun(ValueError):
    """Raised when a run directory cannot be resumed reproducibly."""


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_fingerprint(path: Path) -> dict[str, str]:
    names = ("manifest.json", "train.jsonl", "validation.jsonl")
    return {name: _file_sha256(path / name) for name in names}


def _source_fingerprint() -> dict[str, dict[str, str]]:
    repo_root = Path(__file__).resolve().parents[2]
    project_root = repo_root / "src" / "optimization"
    gepa_root = repo_root / "third_party" / "gepa" / "src" / "gepa"
    project_files = sorted(project_root.glob("*.py"))
    gepa_files = [
        gepa_root / "api.py",
        gepa_root / "core" / "engine.py",
        gepa_root / "core" / "state.py",
        gepa_root / "strategies" / "batch_sampler.py",
        gepa_root / "strategies" / "candidate_selector.py",
    ]
    return {
        "project_optimization": {
            path.name: _file_sha256(path) for path in project_files
        },
        "vendored_gepa_core": {
            str(path.relative_to(gepa_root)): _file_sha256(path)
            for path in gepa_files
        },
    }


def _semantic_config(
    config: OptimizationConfig,
    *,
    initial_rules: str,
) -> dict[str, Any]:
    search = asdict(config.search)
    search.pop("max_metric_calls")
    search.pop("projection_metric_calls")
    container = asdict(config.container)
    container["sif_cache_dir"] = str(container["sif_cache_dir"])
    return {
        "dataset": _dataset_fingerprint(config.dataset_snapshot),
        "source": _source_fingerprint(),
        "initial_rules_sha256": text_sha256(initial_rules),
        "checker": asdict(config.checker),
        "reflection": asdict(config.reflection),
        "search": search,
        "docker": asdict(config.docker),
        "container": container,
        "prompts": {
            "checker_system_sha256": text_sha256(config.checker_prompt),
            "checker_instance_sha256": text_sha256(
                config.checker_instance_template
            ),
            "reflection_system_sha256": text_sha256(config.reflection_prompt),
            "reflection_instance_sha256": text_sha256(
                config.reflection_instance_template
            ),
        },
        "gepa": {
            "candidate_selection_strategy": "pareto",
            "frontier_type": "instance",
            "batch_sampler": "epoch_shuffled",
            "data_id": "instance_id",
            "cache_evaluation": True,
            "track_best_outputs": True,
            "candidate_components": ["rules"],
        },
    }


def prepare_run_manifest(
    config: OptimizationConfig,
    *,
    initial_rules: str,
) -> bool:
    """Create or validate an immutable logical-run manifest.

    Returns whether this invocation is resuming an existing GEPA state.
    """

    manifest_path = config.run_dir / "run_manifest.json"
    gepa_state_path = config.run_dir / "gepa_state.bin"
    resuming = gepa_state_path.is_file()
    semantic = _semantic_config(config, initial_rules=initial_rules)
    semantic_sha256 = text_sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True)
    )
    requested_budget = config.search.max_metric_calls

    if not manifest_path.exists():
        if resuming:
            raise IncompatibleOptimizationRun(
                "run_dir contains gepa_state.bin but no run_manifest.json; "
                "legacy runs cannot be adopted as reproducible formal runs"
            )
        _atomic_json(
            manifest_path,
            {
                "version": MANIFEST_VERSION,
                "semantic_sha256": semantic_sha256,
                "semantic_config": semantic,
                "initial_max_metric_calls": requested_budget,
                "latest_max_metric_calls": requested_budget,
            },
        )
        return False

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != MANIFEST_VERSION:
        raise IncompatibleOptimizationRun("unsupported run manifest version")
    if manifest.get("semantic_sha256") != semantic_sha256:
        raise IncompatibleOptimizationRun(
            "run configuration or source differs from the existing logical "
            "experiment; use a new run_dir instead of resuming changed code, "
            "data, prompts, models, or search semantics"
        )
    previous_budget = int(manifest["latest_max_metric_calls"])
    if requested_budget < previous_budget:
        raise IncompatibleOptimizationRun(
            "max_metric_calls cannot decrease when resuming a logical run "
            f"({requested_budget} < {previous_budget})"
        )
    if requested_budget > previous_budget:
        manifest["latest_max_metric_calls"] = requested_budget
        _atomic_json(manifest_path, manifest)
    if not resuming and (config.run_dir / "gepa_resume_state.json").exists():
        raise IncompatibleOptimizationRun(
            "gepa_resume_state.json exists without gepa_state.bin; refusing "
            "an inconsistent restart"
        )
    return resuming


def _encode_random_state(state: object) -> str:
    return base64.b64encode(pickle.dumps(state, protocol=4)).decode("ascii")


def _decode_random_state(value: str) -> object:
    return pickle.loads(base64.b64decode(value.encode("ascii")))  # noqa: S301


class ReproducibleSearchState:
    """Own the shared GEPA RNG, sampler state, and cumulative run counters."""

    def __init__(
        self,
        config: OptimizationConfig,
        *,
        resuming: bool,
    ) -> None:
        self.run_dir = config.run_dir
        self.path = config.run_dir / "gepa_resume_state.json"
        self.rng = random.Random(config.search.seed)
        self.sampler = EpochShuffledBatchSampler(
            minibatch_size=config.search.reflection_minibatch_size,
            rng=self.rng,
        )
        self.selector = ParetoCandidateSelector(rng=self.rng)
        self.successful_proposals = 0
        self.reflection_failures: list[dict[str, str]] = []
        self.accepted_candidates = 0
        if resuming:
            self._restore()
        elif self.path.exists():
            raise IncompatibleOptimizationRun(
                "gepa_resume_state.json exists without a resumable GEPA state"
            )

    def _restore(self) -> None:
        if not self.path.is_file():
            raise IncompatibleOptimizationRun(
                "gepa_state.bin exists without gepa_resume_state.json; "
                "reproducible sampling state is unavailable"
            )
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("version") != RESUME_STATE_VERSION:
            raise IncompatibleOptimizationRun("unsupported resume state version")
        official = GEPAState.load(str(self.run_dir))
        if int(value["gepa_state_i"]) != official.i:
            raise IncompatibleOptimizationRun(
                "project resume state is not aligned with gepa_state.bin"
            )
        self.rng.setstate(_decode_random_state(value["random_state"]))
        sampler = value["sampler"]
        self.sampler.shuffled_ids = list(sampler["shuffled_ids"])
        self.sampler.epoch = int(sampler["epoch"])
        self.sampler.id_freqs = Counter(
            {str(key): int(count) for key, count in sampler["id_freqs"].items()}
        )
        self.sampler.last_trainset_size = int(sampler["last_trainset_size"])
        self.successful_proposals = int(value["successful_proposals"])
        self.reflection_failures = list(value["reflection_failures"])
        self.accepted_candidates = int(value["accepted_candidates"])

    def save(
        self,
        *,
        gepa_state_i: int,
        proposer: Any,
        accepted_candidates: int,
    ) -> None:
        self.successful_proposals = int(
            getattr(proposer, "successful_proposals", 0)
        )
        self.reflection_failures = list(getattr(proposer, "failures", []))
        self.accepted_candidates = accepted_candidates
        _atomic_json(
            self.path,
            {
                "version": RESUME_STATE_VERSION,
                "gepa_state_i": gepa_state_i,
                "random_state": _encode_random_state(self.rng.getstate()),
                "sampler": {
                    "shuffled_ids": self.sampler.shuffled_ids,
                    "epoch": self.sampler.epoch,
                    "id_freqs": dict(self.sampler.id_freqs),
                    "last_trainset_size": self.sampler.last_trainset_size,
                },
                "successful_proposals": self.successful_proposals,
                "reflection_failures": self.reflection_failures,
                "accepted_candidates": self.accepted_candidates,
            },
        )


def load_seed_validation_replay(
    run_dir: Path,
    validation: list[GEPACase],
    *,
    initial_rules: str,
) -> dict[str, tuple[dict[str, Any], float]]:
    """Load the original seed validation outputs for GEPA's resume preflight."""

    expected_ids = {case.instance_id for case in validation}
    candidate_sha256 = text_sha256(initial_rules)
    replay: dict[str, tuple[dict[str, Any], float]] = {}
    path = run_dir / "evaluations.jsonl"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            instance_id = str(record.get("instance_id", ""))
            if (
                record.get("candidate_sha256") == candidate_sha256
                and record.get("split") == "validation"
                and instance_id in expected_ids
            ):
                replay[instance_id] = (
                    dict(record["output"]),
                    float(record["score"]),
                )
    if set(replay) != expected_ids:
        missing = sorted(expected_ids - set(replay))
        raise IncompatibleOptimizationRun(
            "cannot replay seed validation for resume; missing instances: "
            + ", ".join(missing)
        )
    return replay
