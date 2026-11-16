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

from src.agents.output_contract import AgentOutputContractError
from src.agents.runtime import codex_options
from src.artifact_policy import mark_private_artifact_dir
from src.exceptions import ControllerYield
from src.optimization.audit import optional_diagnostic
from src.optimization.behavioral_hybrid_evidence import write_training_record_index
from src.optimization.behavioral_runner import _controller_lock
from src.optimization.codex_cli_runtime import CodexConversation, CodexSIFExecution, CodexCLIError, CodexConfigurationError
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
    max_updates: int
    model: dict
    analysis_sif: Path
    analysis_sif_sha256: str
    execution_backend: str = "local"


def load_config(path):
    raw = yaml.safe_load(Path(path).read_text())
    def resolve(value):
        target = Path(os.path.expandvars(value)).expanduser()
        return target if target.is_absolute() else ROOT / target
    paths = raw["paths"]
    count = raw["max_updates"]
    if type(count) is not int or count < 1 or not str(raw["goal"]).strip():
        raise ValueError("research requires a goal and positive max_updates budget")
    backend = raw.get("execution", {}).get("backend")
    if backend not in {"local", "hpc_slurm"}:
        raise ValueError("research execution backend must be local or hpc_slurm")
    digest = raw["analysis"]["sif_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("analysis SIF requires its reviewed SHA256")
    return ResearchConfig(resolve(paths["dataset_snapshot"]), resolve(paths["safe_pce_config"]),
        resolve(paths["seed_guide"]), resolve(paths["run_dir"]), raw["goal"], count,
        codex_options(raw["analysis"]["model"]), resolve(raw["analysis"]["sif_path"]), digest, backend)


def _write_bytes(path, value):
    atomic_write(path, lambda temporary: temporary.write_bytes(value))


class GuideConversation:
    """Only the raw guide is submitted; all tool-visible evidence is read-only."""
    def __init__(self, config, evidence, private):
        if sha256(config.analysis_sif) != config.analysis_sif_sha256:
            raise ValueError("analysis SIF hash mismatch")
        outputs = tuple(f"guide-{index:04d}-{attempt}.md"
                        for index in range(1, config.max_updates + 1) for attempt in range(1, 4))
        boundary = CodexSIFExecution(config.analysis_sif, evidence,
            output_files=outputs, tool_network_disabled=True)
        self.session = CodexConversation(model_config=config.model, container=boundary,
                                         attempt_dir=private / "analysis")
        self.private = private

    def __enter__(self):
        self.session.__enter__()
        return self

    def propose(self, index):
        attempts, feedback = [], ""
        for attempt in range(1, 4):
            try:
                raw, trace = self.session.call_text(system="The research objective and evidence are in "
                    "/evidence/GOAL.md and /evidence/manifest.json. Your final response is the complete "
                    "planning guide supplied to the Planner.",
                    user="New experiment feedback is available. All previous training evidence and submitted "
                         "guides remain readable in the evidence inventory. " + feedback,
                    task="Submit the complete improved planning guide as your final response.",
                    output_filename=f"guide-{index:04d}-{attempt}.md")
                attempts.append({"attempt": attempt, "trajectory": trace, "raw_response": raw})
                if not isinstance(raw, str) or not raw.strip():
                    raise AgentOutputContractError("The final response is empty. Submit the complete nonempty guide text.")
                return raw
            except CodexConfigurationError:
                raise
            except (AgentOutputContractError, CodexCLIError) as exc:
                attempts.append({"attempt": attempt, "error": str(exc),
                                 "trajectory": getattr(exc, "trajectory", [])})
                feedback = "Previous submission error: " + str(exc)
                if attempt == 3:
                    raise
            finally:
                with optional_diagnostic("research Codex turn archive"):
                    atomic_json(self.private / f"analysis-turn-{index:04d}.json", attempts)

    def __exit__(self, *exception):
        with optional_diagnostic("research Codex session archive"):
            atomic_json(self.private / "analysis-sessions.json", self.session.session_evidence())
        return self.session.__exit__(*exception)


def run_research(config, *, conversation_factory=GuideConversation, backend_factory=SafePCEBackend):
    """One seed evaluation and max_updates proposals; no score-based rejection."""
    config.run_dir.mkdir(parents=True, exist_ok=True)
    mark_private_artifact_dir(config.run_dir)
    with _controller_lock(config):
        return _run_locked(config, conversation_factory, backend_factory)


def _run_locked(config, conversation_factory, backend_factory):
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
    identity = {"config": asdict(config), "pce": pce_identity(config.pce_config),
        "seed_sha256": sha256(config.seed), "snapshot": {
            name: sha256(config.snapshot / name) for name in ("manifest.json", "train.jsonl", "validation.jsonl")},
        "research_source_sha256": sha256(Path(__file__))}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    manifest_path = private / "identity.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text())["sha256"] != digest:
        raise ValueError("research inputs or configuration changed; use a new run directory")
    if not manifest_path.exists():
        atomic_json(manifest_path, {"sha256": digest})
    checkpoint = private / "state.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"rounds": []}
    for row in state["rounds"]:
        if sha256(evidence / row["guide"]) != row["guide_sha256"]:
            raise ValueError("a previously evaluated guide changed")
    _write_bytes(evidence / "GOAL.md", config.goal.encode("utf-8"))
    _write_bytes(evidence / "guides" / "0000.md", config.seed.read_bytes())

    def publish():
        atomic_json(evidence / "manifest.json", {"schema_version": 1,
            "goal": "GOAL.md", "current_guide": state["rounds"][-1]["guide"] if state["rounds"] else "guides/0000.md",
            "feedback_semantics": "Official resolved/total. Validation is aggregate selection feedback; "
                                  "its task inputs, outcomes and trajectories are private. "
                                  "Training record indexes link complete original data, not mandatory reading steps.",
            "rounds": state["rounds"]})

    publish()
    if len(state["rounds"]) == config.max_updates + 1:
        return state["rounds"]
    # One conversation spans all new proposals. After a process interruption,
    # a new conversation can read the same complete durable training history.
    session = None
    try:
        for index in range(len(state["rounds"]), config.max_updates + 1):
            path = evidence / "guides" / f"{index:04d}.md"
            if not path.exists():
                if session is None:
                    session = conversation_factory(config, evidence, private)
                    session.__enter__()
                raw = session.propose(index)
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("submit a nonempty UTF-8 planning guide as the complete final response")
                _write_bytes(path, raw.encode("utf-8"))
            guide = path.read_bytes().decode("utf-8")
            if not guide.strip():
                raise ValueError("guide is empty")
            pending = {"round": index, "guide_sha256": sha256(path)}
            if "pending" in state and state["pending"] != pending:
                raise ValueError("pending research guide identity changed")
            state["pending"] = pending
            atomic_json(checkpoint, state)
            # Identical guides in different rounds get independent PCE runs;
            # a resume of this round retains its existing Worker checkpoints.
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
            publish()
    finally:
        if session is not None:
            session.__exit__(None, None, None)
    return state["rounds"]


def _score_outputs(backend, cases, guide):
    while True:
        try:
            waves = backend.evaluate(cases, guide, 1)
            break
        except ControllerYield:
            if backend.config.execution.backend != "hpc_slurm":
                raise
            # Keep this bounded pilot's Native conversation alive while the
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
