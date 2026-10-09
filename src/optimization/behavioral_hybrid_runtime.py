"""Hybrid runtime wiring over existing evidence Agents, Slurm tasks and GEPA."""
from dataclasses import asdict
from functools import partial
import hashlib
import json
from pathlib import Path

import yaml

from src.optimization.behavioral_hybrid import HybridBehavioralAdapter, HybridLimits, validate_candidate
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer, _playbook, apply_hybrid_delta
from src.optimization.behavioral_runner import run_behavioral_optimization
from src.optimization.hpc.task_batch import TaskFiles, atomic_json
from src.optimization.offline_hpc_reflection import HPCOfflineReflectionProposer
from src.optimization.paired_playbook import validate_ace_paired_reflector_review
from src.optimization.playbook_runtime import run_evidence_reflector, run_evidence_curator, _codex_evidence_sif
from src.optimization.playbook_hpc_executor import _file_sha256
from src.optimization.audit import redact_sensitive


def hybrid_limits(config):
    h = config.hybrid
    return HybridLimits(h.max_items, h.shared_guide_words, h.condition_words, h.item_guide_words)


def phase_identity(config, role, payload):
    identity = {"role": role, "payload": payload, "hybrid": asdict(config.hybrid),
                "reflection": asdict(config.reflection), "command_timeout": config.docker.timeout}
    root = Path(__file__).parent
    identity["source"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in ("behavioral_hybrid_runtime.py", "behavioral_hybrid_worker.py",
                                       "behavioral_hybrid_ace.py", "behavioral_hybrid.py", "playbook.py",
                                       "playbook_runtime.py", "codex_cli_runtime.py")}
    identity["source"]["agent_protocol"] = hashlib.sha256(
        (root.parent / "agents" / "_deps.py").read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def require_evidence_sif(config):
    expected = config.hybrid.evidence_sif_sha256
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("hybrid execution requires a frozen evidence_sif_sha256")
    path = _codex_evidence_sif({"evidence_sif_cache_dir": config.hybrid.evidence_sif_cache_dir,
                               "evidence_image": config.hybrid.evidence_image})
    if _file_sha256(path) != expected:
        raise ValueError("hybrid evidence SIF hash mismatch")
    return path


def validate_phase(role, payload, output, config):
    if role == "reflector":
        validate_ace_paired_reflector_review(output, instance_id=payload["record"]["instance_id"],
                                  playbook=_playbook(payload["parent"]), output_contract="ace_v2")
    elif role == "curator":
        limits = HybridLimits(1000000, 1000000, 1000000, 1000000) if config.hybrid.refiner_enabled else hybrid_limits(config)
        apply_hybrid_delta(payload["parent"], output, payload["records"], limits, history=payload.get("history"))
    elif role == "refiner":
        validate_candidate(json.dumps(output), hybrid_limits(config))
    else:
        raise ValueError("unsupported hybrid phase")


def run_local_phase(config, role, payload, attempt_dir, *, retry_feedback=""):
    """Reuse isolated ACE evidence runners, including their Native Codex support."""
    bundle_bytes = Path(config.hybrid.prompt_bundle).read_bytes()
    if config.hybrid.prompt_bundle_sha256 and hashlib.sha256(bundle_bytes).hexdigest() != config.hybrid.prompt_bundle_sha256:
        raise ValueError("hybrid prompt bundle changed after config loading")
    prompts = yaml.safe_load(bundle_bytes)
    require_evidence_sif(config)
    evidence = attempt_dir / "inputs"
    atomic_json(evidence / "input.json", payload)
    atomic_json(evidence / "manifest.json", {"schema_version": 1, "role": role, "files": ["input.json"]})
    model = dict(asdict(config.reflection), **getattr(config.hybrid, f"{role}_model"))
    env_config = {"evidence_sif_cache_dir": config.hybrid.evidence_sif_cache_dir,
                  "evidence_image": config.hybrid.evidence_image,
                  "command_timeout_seconds": config.docker.timeout}
    common = dict(model_config=model, reflection_config=env_config,
                  system=prompts[f"{role}_task"],
                  instance_template=("Read {{ evidence_path }}/input.json completely.\n"
                      + ("" if model.get("executor") == "codex_cli" else
                         f"Write the required JSON to /tmp/{'reflection' if role == 'reflector' else 'curator'}.json "
                         "and submit its contents using the shared Agent protocol.\n")
                      + "{{ retry_feedback }}"),
                  evidence_dir=str(evidence), retry_feedback=retry_feedback, attempt_dir=attempt_dir)
    try:
        if role == "reflector":
            output, trajectory = run_evidence_reflector(**common, internal_playbook=json.dumps(payload["parent"]))
        else:
            output, trajectory = run_evidence_curator(**common, counted_internal_playbook=json.dumps(payload["parent"]),
                                                      case_count=len(payload.get("records", [])), maximum_rules=config.hybrid.max_items,
                                                      task="Maintain the generalized checklist and investigation guides.")
    except Exception as exc:
        atomic_json(attempt_dir / "completion_error.json", redact_sensitive({
            "error": str(exc), "raw_response": getattr(exc, "raw_response", None),
            "trajectory": getattr(exc, "trajectory", [])}))
        raise
    # Preserve the raw completion BEFORE validating it. Never repair it here.
    atomic_json(attempt_dir / "completion.json", redact_sensitive({"agent_output": output, "trajectory": trajectory}))
    validate_phase(role, payload, output, config)
    return output, trajectory


class HybridPhaseExecutor(HPCOfflineReflectionProposer):
    """Reuse shared scripts and SlurmTaskBatch; one independent Agent per element."""
    def call(self, role, payload):
        require_evidence_sif(self.config)
        fingerprint = phase_identity(self.config, role, payload)
        task_dir = self.config.run_dir / "hpc_tasks" / f"hybrid_{role}" / fingerprint
        manifest = dict(mode="behavioral_hybrid_phase", fingerprint=fingerprint, role=role, payload=payload)
        path = task_dir / "input.json"
        if path.exists() and json.loads(path.read_text()) != manifest:
            raise ValueError("hybrid task manifest mismatch")
        atomic_json(path, manifest)
        task = TaskFiles(0, payload.get("record", {}).get("instance_id", role), path,
                         task_dir / "result.json", task_dir / "attempts" / "task_0000")
        def validate(_, value):
            if value.get("fingerprint") != fingerprint or value.get("role") != role:
                raise ValueError("hybrid result identity mismatch")
            validate_phase(role, payload, value["agent_output"], self.config)
        if self.config.execution.backend == "local":
            # No transport retries are silently added to the local path.
            return run_local_phase(self.config, role, payload, task.attempts_dir / "attempt_01")
        def write_script(indices, attempt):
            if list(indices) != [0]:
                raise ValueError("hybrid phase owns one Agent task")
            script = task_dir / f"attempt_{attempt:02d}.sbatch"
            role_model = getattr(self.config.hybrid, f"{role}_model")
            credential_env = None if role_model.get("executor") == "codex_cli" else role_model.get("api_key_env", self.config.reflection.api_key_env)
            script.write_text(self._script(task_dir, attempt, phase=f"hybrid-{role}",
                worker_module_override="src.optimization.behavioral_hybrid_worker", load_container_modules=True,
                credential_env=credential_env))
            return script
        outputs = self.runtime.run(batch_dir=task_dir, fingerprint=fingerprint, tasks=[task],
            job_name=lambda attempt: f"{self.config.hpc.job_name_prefix}-hybrid-{role}-a{attempt}",
            write_script=write_script, validate_output=validate)
        return outputs[0]["agent_output"], outputs[0]["trajectory"]


def run_hybrid_optimization(config, *, phase_executor=None, checker=None, optimize_fn=None):
    if not config.hybrid.enabled:
        raise ValueError("hybrid.enabled must be true")
    limits = hybrid_limits(config)
    validate_candidate(config.initial_rules_path.read_text(), limits)
    executor = phase_executor or HybridPhaseExecutor(config)
    proposer = IncrementalHybridACEProposer(
        lambda parent, record: executor.call("reflector", {"parent": parent, "record": record}),
        lambda parent, reviews, records, history, caps: executor.call("curator", {
            "parent": parent, "reviews": reviews, "records": records, "history": history, "limits": caps}),
        limits, ledger_path=config.run_dir / "global_counter_ledger.json",
        trace_dir=config.run_dir / "hybrid_proposals",
        refiner=(lambda draft, caps: executor.call("refiner", {"draft": draft, "limits": caps, "split": "train"}))
                if config.hybrid.refiner_enabled else None)
    kwargs = dict(checker=checker, proposer=proposer,
                  adapter_factory=partial(HybridBehavioralAdapter, limits=limits))
    if optimize_fn is not None:
        kwargs["optimize_fn"] = optimize_fn
    return run_behavioral_optimization(config, **kwargs)
