"""Minimal third-party GEPA wiring for reject-playbook optimization.

Concrete model agents and prompts are deliberately injected.  This keeps the
flow testable before the prompt contract and formal experiment YAML are frozen.
"""

from __future__ import annotations

from pathlib import Path
import fcntl
import hashlib
import json
import os
from typing import Any, Callable

import gepa
from gepa.core.state import GEPAState
from gepa.utils import MaxCandidateProposalsStopper

from src.optimization.dataset import GEPACaseLoader, load_snapshot
from src.optimization.playbook import RejectPlaybook
from src.optimization.playbook_adapter import PlaybookGEPAAdapter
from src.exceptions import ControllerYield
from src.optimization.callbacks import ProgressCallback
from src.optimization.resume import ReproducibleSearchState
from types import SimpleNamespace


def run_playbook_search(**kwargs: Any) -> Any:
    """Hold the established single-controller lock for one controller slice."""
    run_dir = Path(kwargs["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "controller.lock").open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Playbook GEPA controller is active") from exc
        try:
            return _run_playbook_search_unlocked(**kwargs)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_playbook_search_unlocked(
    *,
    dataset_snapshot: Path,
    initial_playbook_path: Path,
    run_dir: Path,
    adapter: PlaybookGEPAAdapter,
    max_metric_calls: int,
    max_iterations: int | None,
    seed: int,
    skip_perfect_score: bool = True,
    optimize_fn: Callable[..., Any] = gepa.optimize,
    train_instance_ids: list[str] | None = None,
    validation_instance_ids: list[str] | None = None,
    reflection_minibatch_size: int = 2,
    runtime_config_path: Path | None = None,
    prompt_bundle_path: Path | None = None,
) -> Any:
    """Run the new flow without selecting or constructing any LLM agent."""
    if max_metric_calls < 1:
        raise ValueError("max_metric_calls must be positive")
    if max_iterations is not None and max_iterations < 1:
        raise ValueError("max_iterations must be positive when set")
    initial = RejectPlaybook.parse(
        initial_playbook_path.read_text(encoding="utf-8")
    )
    train, validation = load_snapshot(dataset_snapshot)
    if train_instance_ids is not None:
        by_id = {case.instance_id: case for case in train}
        if set(train_instance_ids) - set(by_id):
            raise ValueError("smoke train IDs are absent from the train split")
        train = [by_id[item] for item in train_instance_ids]
    if validation_instance_ids is not None:
        by_id = {case.instance_id: case for case in validation}
        if set(validation_instance_ids) - set(by_id):
            raise ValueError("smoke validation IDs are absent from validation split")
        validation = [by_id[item] for item in validation_instance_ids]
    run_dir.mkdir(parents=True, exist_ok=True)
    semantic = {
        "schema_version": 1,
        "dataset": {
            name: hashlib.sha256((dataset_snapshot / name).read_bytes()).hexdigest()
            for name in ("manifest.json", "train.jsonl", "validation.jsonl")
        },
        "initial_playbook": hashlib.sha256(initial_playbook_path.read_bytes()).hexdigest(),
        "runtime_config": (
            hashlib.sha256(runtime_config_path.read_bytes()).hexdigest()
            if runtime_config_path is not None else None
        ),
        "prompt_bundle": (
            hashlib.sha256(prompt_bundle_path.read_bytes()).hexdigest()
            if prompt_bundle_path is not None else None
        ),
        "source": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                Path(__file__).with_name("playbook.py"),
                Path(__file__).with_name("playbook_adapter.py"),
                Path(__file__).with_name("playbook_hpc_executor.py"),
                Path(__file__).with_name("playbook_hpc_agents.py"),
                Path(__file__).with_name("playbook_worker.py"),
            )
        },
        "selection": {"train": train_instance_ids, "validation": validation_instance_ids},
        "search": {"seed": seed, "reflection_minibatch_size": reflection_minibatch_size,
                   "skip_perfect_score": skip_perfect_score},
    }
    semantic_sha = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("semantic_sha256") != semantic_sha:
            raise ValueError("Playbook run manifest is incompatible with this controller")
        if max_metric_calls < int(existing["latest_max_metric_calls"]):
            raise ValueError("max_metric_calls cannot decrease on resume")
        if max_metric_calls > int(existing["latest_max_metric_calls"]):
            existing["latest_max_metric_calls"] = max_metric_calls
            tmp = manifest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
            tmp.replace(manifest_path)
    else:
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": 1, "semantic_sha256": semantic_sha,
                                  "semantic_config": semantic,
                                  "initial_max_metric_calls": max_metric_calls,
                                  "latest_max_metric_calls": max_metric_calls},
                                 indent=2, sort_keys=True) + "\n")
        tmp.replace(manifest_path)
    stopper = (
        MaxCandidateProposalsStopper(max_iterations)
        if max_iterations is not None
        else None
    )
    search = SimpleNamespace(seed=seed, reflection_minibatch_size=reflection_minibatch_size)
    state_config = SimpleNamespace(run_dir=run_dir, search=search)
    resuming = (run_dir / "gepa_state.bin").is_file()
    state = ReproducibleSearchState(state_config, resuming=resuming)
    callback = ProgressCallback(
        run_dir, checkpoint=state, proposer=adapter.propose_new_texts,
        accepted_candidates=state.accepted_candidates,
        completed_iterations=(max(0, GEPAState.load(str(run_dir)).i + 1) if resuming else 0),
    )
    status_path = run_dir / "controller_status.json"
    def write_status(status: str, **extra: Any) -> None:
        temporary = status_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "status": status, "pid": os.getpid(), **extra}, indent=2, sort_keys=True) + "\n")
        temporary.replace(status_path)
    write_status("running")
    try:
        result = optimize_fn(
            seed_candidate={"rules": initial.serialize()},
            trainset=GEPACaseLoader(train),
            valset=GEPACaseLoader(validation),
            adapter=adapter,
            reflection_lm=None,
            candidate_selection_strategy=state.selector,
            frontier_type="instance",
            batch_sampler=state.sampler,
            reflection_minibatch_size=None,
            perfect_score=0.0,
            skip_perfect_score=skip_perfect_score,
            max_metric_calls=max_metric_calls,
            stop_callbacks=stopper,
            run_dir=str(run_dir),
            cache_evaluation=True,
            track_best_outputs=True,
            callbacks=[callback],
            seed=seed,
        )
    except ControllerYield as exc:
        write_status("yielded", reason=exc.reason, batch_dir=exc.batch_dir,
                     worker_job_id=exc.job_id)
        return None
    except Exception as exc:
        write_status("failed", error_type=type(exc).__name__)
        raise
    result_path = run_dir / "result.json"
    result_tmp = result_path.with_suffix(".json.tmp")
    result_payload = (
        {**result.to_dict(), "run_status": "completed"}
        if hasattr(result, "to_dict")
        else {"schema_version": 1, "run_status": "completed"}
    )
    result_tmp.write_text(
        json.dumps(result_payload, indent=2, sort_keys=True, default=list) + "\n",
        encoding="utf-8",
    )
    result_tmp.replace(result_path)
    if hasattr(result, "best_candidate"):
        (run_dir / "best_playbook.json").write_text(
            str(result.best_candidate["rules"]) + "\n", encoding="utf-8"
        )
    if hasattr(result, "candidate_tree_html"):
        (run_dir / "candidate_tree.html").write_text(
            result.candidate_tree_html(), encoding="utf-8"
        )
    write_status("completed")
    return result
