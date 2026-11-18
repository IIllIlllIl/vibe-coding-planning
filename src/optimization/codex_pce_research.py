"""Guide-only research, reusing Safe PCE without running the GEPA search engine."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import yaml

from src.agents.runtime import codex_options
from src.artifact_policy import mark_private_artifact_dir
from src.exceptions import ControllerYield
from src.optimization.audit import optional_diagnostic
from src.optimization.behavioral_hybrid_evidence import write_training_record_index
from src.optimization.behavioral_runner import _controller_lock
from src.optimization.hpc.task_batch import atomic_json, atomic_write
from src.optimization.safe_pce_execution import pce_identity, sha256
from src.optimization.safe_pce_learning import SafePCEBackend, _validate_learning_evidence, outcome_score


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ResearchConfig:
    snapshot: Path
    pce_config: Path
    seed: Path
    run_dir: Path
    goal: str
    max_experiments: int
    model: dict
    analysis_sif: Path
    analysis_sif_sha256: str
    execution_backend: str = "local"
    mode: str = "autoresearch_pce"
    program: Path | None = None
    evaluation_wait_hint: str = "PCE duration varies with task preparation, queueing and retries."


def load_config(path):
    raw = yaml.safe_load(Path(path).read_text())
    def resolve(value):
        target = Path(os.path.expandvars(value)).expanduser()
        return target if target.is_absolute() else ROOT / target
    paths = raw["paths"]
    mode = raw.get("mode")
    if mode != "autoresearch_pce":
        raise ValueError("only autoresearch_pce is maintained; the script-Codex prototype is archived")
    count = raw["max_experiments"]
    if type(count) is not int or count < 1 or not str(raw["goal"]).strip():
        raise ValueError("research requires a goal and positive experiment budget")
    program = resolve(raw["program"])
    backend = raw.get("execution", {}).get("backend")
    if backend not in {"local", "hpc_slurm"}:
        raise ValueError("research execution backend must be local or hpc_slurm")
    digest = raw["analysis"]["sif_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("analysis SIF requires its reviewed SHA256")
    return ResearchConfig(resolve(paths["dataset_snapshot"]), resolve(paths["safe_pce_config"]),
        resolve(paths["seed_guide"]), resolve(paths["run_dir"]), raw["goal"],
        count,
        codex_options(raw["analysis"]["model"]), resolve(raw["analysis"]["sif_path"]), digest, backend, mode, program,
        raw.get("execution", {}).get("evaluation_wait_hint",
            "PCE duration varies with task preparation, queueing and retries."))


def _write_bytes(path, value):
    atomic_write(path, lambda temporary: temporary.write_bytes(value))


def run_research(config, *, conversation_factory=None, backend_factory=SafePCEBackend, resume_source=None):
    """The AR Agent owns research; the shared runner owns frozen PCE execution."""
    from src.optimization.autoresearch_pce import run_autoresearch
    config.run_dir.mkdir(parents=True, exist_ok=True)
    mark_private_artifact_dir(config.run_dir)
    with _controller_lock(config):
        atomic_json(config.run_dir / "private/research_exit.json", {"status": "running"})
        try:
            return run_autoresearch(config, backend_factory=backend_factory,
                                   conversation_factory=conversation_factory, resume_source=resume_source)
        except Exception as exc:
            atomic_json(config.run_dir / "private/research_exit.json", {
                "status": "failed", "error_type": type(exc).__name__,
                "errno": getattr(exc, "errno", None),
                "failure_record": getattr(exc, "failure_record", {}), "error": str(exc)})
            raise


def research_identity(config, *, source_root=ROOT):
    """Scientific identity plus the exact implementation used by an owner."""
    identity = {"config": asdict(config), "pce": pce_identity(config.pce_config),
        "seed_sha256": sha256(config.seed), "snapshot": {
            name: sha256(config.snapshot / name) for name in ("manifest.json", "train.jsonl", "validation.jsonl")},
        "research_source_sha256": sha256(source_root / "src/optimization/codex_pce_research.py")}
    identity["autoresearch"] = {"program_sha256": sha256(config.program),
        "adapter_sha256": sha256(source_root / "src/optimization/autoresearch_pce.py"),
        "client_sha256": sha256(source_root / "src/optimization/autoresearch_pce_client.py"),
        "upstream_lock_sha256": sha256(source_root / "third_party/autoresearch.version.json")}
    session_runtime = source_root / "src/optimization/codex_session_runtime.py"
    if session_runtime.is_file():
        identity["autoresearch"]["session_runtime_sha256"] = sha256(session_runtime)
    return json.loads(json.dumps(identity, sort_keys=True, default=str))


def checked_research_identity(config, *, resume_source=None):
    """An explicit predecessor must reproduce the old digest before upgrading.

    Only the owner source hashes may change. Inputs, program/client, goal,
    budget, PCE prompts/models/scoring and all frozen selections stay bound.
    This is a runtime migration, never an unverified identity bypass.
    """
    current = research_identity(config)
    digest = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    path = config.run_dir / "private/identity.json"
    previous = json.loads(path.read_text()) if path.is_file() else None
    record = {"sha256": digest, "identity": current}
    if previous and previous["sha256"] != digest:
        if resume_source is None:
            raise ValueError("research inputs or configuration changed; use a new run directory or a verified --resume-source")
        predecessor = research_identity(config, source_root=Path(resume_source))
        old_digest = hashlib.sha256(json.dumps(predecessor, sort_keys=True).encode()).hexdigest()
        if old_digest != previous["sha256"]:
            raise ValueError("resume source does not reproduce the checkpoint identity")
        if any(predecessor["autoresearch"][key] != current["autoresearch"][key]
               for key in ("client_sha256", "upstream_lock_sha256")):
            raise ValueError("resume source changes the fixed research protocol")
        record["predecessors"] = [*previous.get("predecessors", []), previous["sha256"]]
    elif previous:
        record.update({key: previous[key] for key in ("predecessors",) if key in previous})
    return record


def prepare_research(config, backend_factory, *, resume_source=None):
    """Frozen inputs, evidence and recovery identity reused by AR experiments."""
    private, evidence = config.run_dir / "private", config.run_dir / "analysis_inputs"
    private.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    binding = SimpleNamespace(safe_pce_config=config.pce_config, dataset_snapshot=config.snapshot,
        run_dir=private / "pce", search=SimpleNamespace(reflection_minibatch_size=1),
        execution=SimpleNamespace(backend=config.execution_backend),
        hpc=SimpleNamespace(job_name_prefix=config.run_dir.name))
    backend = backend_factory(binding)
    train, validation = backend.load_snapshot()
    if (not train or not validation or any(case.split != "train" for case in train)
            or any(case.split != "validation" for case in validation)
            or len({case.instance_id for case in train + validation}) != len(train) + len(validation)):
        raise ValueError("research requires disjoint nonempty train and validation task sets")
    manifest_path = private / "identity.json"
    identity = checked_research_identity(config, resume_source=resume_source)
    if not manifest_path.exists() or json.loads(manifest_path.read_text()) != identity:
        atomic_json(manifest_path, identity)
    checkpoint = private / "state.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"rounds": []}
    for row in state["rounds"]:
        if sha256(evidence / row["guide"]) != row["guide_sha256"]:
            raise ValueError("a previously evaluated guide changed")
    _write_bytes(evidence / "GOAL.md", config.goal.encode("utf-8"))
    _write_bytes(evidence / "guides" / "0000.md", config.seed.read_bytes())
    return private, evidence, binding, backend, train, validation, state


def publish_research(evidence, state, **metadata):
    atomic_json(evidence / "manifest.json", {"schema_version": 1,
            "goal": "GOAL.md", "current_guide": state["rounds"][-1]["guide"] if state["rounds"] else "guides/0000.md",
            "feedback_semantics": "Official resolved/total. Validation is aggregate selection feedback; "
                                  "its task inputs, outcomes and trajectories are private. "
                                  "Training record indexes link complete original data, not mandatory reading steps.",
            "rounds": state["rounds"], **metadata})


def evaluate_research_guide(config, *, binding, backend, train, validation, private, evidence, state, index, path,
                            on_pending=None):
    """One immutable guide through the same complete train/validation PCE chain."""
    guide = path.read_bytes().decode("utf-8")
    if not guide.strip():
        raise ValueError("guide is empty")
    pending = {"round": index, "guide_sha256": sha256(path)}
    if "pending" in state and state["pending"] != pending:
        raise ValueError("pending research guide identity changed")
    state["pending"] = pending
    checkpoint = private / "state.json"
    atomic_json(checkpoint, state)
    with optional_diagnostic("AR pending notification"):
        if on_pending is not None:
            on_pending()
    binding.run_dir = private / "pce" / f"round-{index:04d}"
    binding.hpc.job_name_prefix = config.run_dir.name + f"-r{index:04d}"
    reports = {}
    for split, cases in (("train", train), ("validation", validation)):
        target = private / f"round-{index:04d}-{split}.json"
        if target.exists():
            cached = json.loads(target.read_text())
            if (cached["guide_sha256"] != sha256(path)
                    or [(row["instance_id"], row["split"]) for row in cached["records"]]
                    != [(case.instance_id, split) for case in cases]):
                raise ValueError("cached research evaluation identity mismatch")
            reports[split] = cached["records"]
        else:
            reports[split] = _score_outputs(backend, cases, guide)
            atomic_json(target, {"guide_sha256": sha256(path), "records": reports[split]})
    train_root = evidence / "rounds" / f"{index:04d}"
    train_root.mkdir(parents=True, exist_ok=True)
    write_training_record_index(train_root, reports["train"])
    resolved = sum(row["score"] for row in reports["validation"])
    previous = [row["validation"]["resolved"] for row in state["rounds"]]
    state["rounds"].append({"round": index, "guide": f"guides/{index:04d}.md",
        "guide_sha256": sha256(path), "training_records": f"rounds/{index:04d}/records.json",
        "train": {"resolved": sum(row["score"] for row in reports["train"]), "total": len(train)},
        "validation": {"resolved": resolved, "total": len(validation),
            "improved_over_best": bool(previous) and resolved > max(previous)}})
    state.pop("pending")
    atomic_json(checkpoint, state)
    publish_research(evidence, state)
    return state["rounds"][-1]


def _score_outputs(backend, cases, guide):
    while True:
        try:
            waves = backend.evaluate(cases, guide, 1)
            break
        except ControllerYield:
            if backend.config.execution.backend != "hpc_slurm":
                raise
            # Keep this owner's Native conversation alive while the
            # existing executor owns submission, preparation, retries and
            # exhausted-Agent finalization. A yield is waiting, not a retry.
            time.sleep(backend.pce.hpc.poll_interval_seconds)
    if len(waves) != 1 or len(waves[0]) != len(cases):
        raise ValueError("incomplete Safe PCE output batch")
    records = []
    for case, output in zip(cases, waves[0], strict=True):
        _validate_learning_evidence(output)
        score = outcome_score(output, mode=backend.mode, instance_id=case.instance_id)
        row = {"instance_id": case.instance_id, "split": case.split,
               "task_semantics": "safe_pce_resolution_v1", "planner_input": case.planner_input(),
               "score": score, "pce_runs": [{"repetition": 1, "pce_output": output, "score": score}]}
        if case.split == "train":
            references = backend.learning_diagnostics_for(case.instance_id)
            if references is not None:
                row["retrospective_diagnostics"] = deepcopy(references)
        records.append(row)
    return records
