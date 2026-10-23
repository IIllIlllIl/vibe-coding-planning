"""Hybrid runtime wiring over existing evidence Agents, Slurm tasks and GEPA."""
from dataclasses import asdict
from functools import partial
import hashlib
import json
from pathlib import Path

import yaml

from src.optimization.behavioral_hybrid import HybridBehavioralAdapter, HybridLimits, HybridSizeLimitError, validate_candidate
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer, _playbook, apply_hybrid_delta
from src.optimization.behavioral_runner import run_behavioral_optimization
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.hpc.task_batch import SlurmTaskBatch
from src.optimization.hpc.phase_wave import prepare_phase_wave, build_phase_array_script
from src.optimization.paired_playbook import validate_ace_paired_reflector_review
from src.optimization.playbook_runtime import run_evidence_reflector, run_evidence_curator, _codex_evidence_sif
from src.optimization.playbook_hpc_executor import _file_sha256
from src.optimization.audit import redact_sensitive, redact_agent_record
from src.agents.runtime import credential_envs


def hybrid_limits(config):
    h = config.hybrid
    return HybridLimits(h.max_items, h.shared_guide_words, h.condition_words, h.item_guide_words,
                        h.shared_guide_tokens, h.condition_tokens, h.item_guide_tokens,
                        shared_guide_enabled=h.shared_guide_enabled)


def phase_identity(config, role, payload):
    identity = {"role": role, "payload": payload, "hybrid": asdict(config.hybrid),
                "task": asdict(config.task),
                "reflection": asdict(config.reflection), "command_timeout": config.docker.timeout}
    root = Path(__file__).parent
    identity["source"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in ("behavioral_hybrid_runtime.py", "behavioral_hybrid_worker.py",
                                       "behavioral_hybrid_ace.py", "behavioral_hybrid.py", "playbook.py",
                                       "playbook_runtime.py", "codex_cli_runtime.py",
                                       "hpc/phase_wave.py", "hpc/worker_contract.py",
                                       "ace_transaction.py", "agent_references.py",
                                       "ace_evidence.py", "behavioral_hybrid_evidence.py", "audit.py")}
    identity["source"]["agent_protocol"] = hashlib.sha256(
        (root.parent / "agents" / "_deps.py").read_bytes()).hexdigest()
    for name in ("runtime.py", "output_contract.py"):
        identity["source"]["agents/" + name] = hashlib.sha256(
            (root.parent / "agents" / name).read_bytes()).hexdigest()
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


def require_history_bundle(config, case):
    """Require the prepared, hash-verified authority; never build in Agents."""
    import os
    from src.environment.repository_history import RepositoryHistoryCache
    directory = config.hybrid.repository_history_cache_dir
    if not directory:
        raise ValueError("hybrid Codex Checker requires repository_history_cache_dir")
    cache = RepositoryHistoryCache(Path(os.path.expandvars(directory)).expanduser())
    result = cache.validate(sif_sha256=config.hybrid.evidence_sif_sha256,
                            base_commit=case.repository.proxy_commit)
    if result is None:
        raise ValueError(f"missing or invalid prepared repository history: {case.instance_id}")
    return result


def verify_hybrid_snapshot(config):
    expected = config.hybrid.dataset_file_sha256
    if set(expected) != {"manifest.json", "train.jsonl", "validation.jsonl"}:
        raise ValueError("hybrid smoke requires three frozen snapshot file hashes")
    for name, digest in expected.items():
        if _file_sha256(config.dataset_snapshot / name) != digest:
            raise ValueError(f"hybrid snapshot hash mismatch: {name}")


def validate_phase(role, payload, output, config):
    if role == "reflector":
        validate_ace_paired_reflector_review(output, instance_id=payload["record"]["instance_id"],
                                  playbook=_playbook(payload["parent"]), output_contract="ace_v2")
    elif role == "curator":
        limits = hybrid_limits(config)
        if config.hybrid.refiner_enabled:
            limits = limits.relaxed()
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
    from .agent_references import phase_references, decode_phase_output
    from .behavioral_hybrid_evidence import write_hybrid_evidence
    agent_payload, case_refs, item_refs = phase_references(role, payload)
    if role in {"curator", "refiner"}:
        agent_payload["limits"] = hybrid_limits(config).agent_view()
    if role == "refiner":
        try:
            validate_candidate(json.dumps(payload["draft"]), hybrid_limits(config))
        except HybridSizeLimitError as exc:
            agent_payload["size_violations"] = exc.violations
    atomic_json(attempt_dir / "reference_map.json", {
        "cases": case_refs.decode_map, "items": item_refs.decode_map})
    write_hybrid_evidence(evidence, role, agent_payload,
                          canonical_parent=payload.get("parent"))
    model = dict(asdict(config.reflection), **getattr(config.hybrid, f"{role}_model"))
    env_config = {"evidence_sif_cache_dir": config.hybrid.evidence_sif_cache_dir,
                  "evidence_image": config.hybrid.evidence_image,
                  "command_timeout_seconds": config.docker.timeout}
    common = dict(model_config=model, reflection_config=env_config,
                  system=prompts[f"{role}_task"],
                  instance_template=("Read {{ evidence_path }}/manifest.json first, then read every "
                      "required_files entry completely. Each path is relative to {{ evidence_path }}. "
                      "Read required evidence in manageable sections so tool output is not truncated; "
                      "continue until all entries are read. Use optional_files for supporting and "
                      "opposing details as needed. counted_playbook.json is the parent artifact; "
                      "record.json or records.json indexes case fields by file path. "
                      "For Refiner, draft.json is the artifact to refine.\n"
                      + ("" if model.get("executor") == "codex_cli" else
                         f"Write the required JSON to /tmp/{'reflection' if role == 'reflector' else 'curator'}.json "
                         "and submit its contents using the shared Agent protocol.\n")
                      + "{{ retry_feedback }}"),
                  evidence_dir=str(evidence), retry_feedback=retry_feedback, attempt_dir=attempt_dir)
    try:
        if role == "reflector":
            task = ({"task": "Analyze the planning rules using the complete Planner, Coder, and evaluator evidence."}
                    if config.task.semantics == "safe_pce_resolution_v1" else {})
            output, trajectory = run_evidence_reflector(**common, internal_playbook=json.dumps(agent_payload["parent"]), **task)
        elif role == "curator":
            output, trajectory = run_evidence_curator(**common, counted_internal_playbook=json.dumps(agent_payload["parent"]),
                                                      case_count=len(payload.get("records", [])), maximum_rules=config.hybrid.max_items,
                                                      task="Maintain the generalized checklist and investigation guides.")
        elif role == "refiner":
            output, trajectory = run_evidence_curator(**common, counted_internal_playbook=json.dumps(agent_payload["draft"]),
                                                      case_count=0, maximum_rules=config.hybrid.max_items,
                                                      task="Refine the supplied checklist and guides within the given limits.")
        else:
            raise ValueError("unsupported hybrid phase")
    except Exception as exc:
        atomic_json(attempt_dir / "completion_error.json", redact_sensitive({
            "error": str(exc), "raw_response": getattr(exc, "raw_response", None),
            "trajectory": getattr(exc, "trajectory", [])}))
        raise
    # Preserve the raw completion BEFORE validating it. Never repair it here.
    try:
        atomic_json(attempt_dir / "completion.json", redact_agent_record({"agent_output": output, "trajectory": trajectory}))
        output = decode_phase_output(role, output, case_refs, item_refs)
        atomic_json(attempt_dir / "resolved_completion.json", {"agent_output": output})
        validate_phase(role, payload, output, config)
    except Exception as exc:
        exc.worker_failure_stage = "agent_output_validation"
        raise
    return output, trajectory


class HybridPhaseExecutor:
    """Hybrid payload adapter over the same phase-wave components as playbook."""
    def __init__(self, config):
        self.config = config
        self.runtime = SlurmTaskBatch(config.hpc)

    def _job_name(self, role, fingerprint, attempt):
        return f"{self.config.hpc.job_name_prefix}-hybrid-{role}-{fingerprint[:12]}-a{attempt}"

    def script_for_wave(self, batch_dir, role, indices, attempt):
        model = getattr(self.config.hybrid, f"{role}_model")
        credentials = credential_envs([{"api_key_env": self.config.reflection.api_key_env, **model}])
        credential = credentials[0] if credentials else None
        return build_phase_array_script(hpc=self.config.hpc, batch_dir=batch_dir,
            indices=indices, attempt=attempt,
            job_name=self._job_name(role, batch_dir.name, attempt),
            config_path=self.config.hpc.worker_config_path,
            worker_module="src.optimization.behavioral_hybrid_worker",
            modules=(self.config.hpc.python_module, self.config.hpc.container_module),
            credential_env=credential)

    def run_wave(self, role, payloads):
        if role not in {"reflector", "curator", "refiner"} or not payloads:
            raise ValueError("hybrid wave requires a supported role and nonempty payloads")
        require_evidence_sif(self.config)
        identities = [phase_identity(self.config, role, payload) for payload in payloads]
        fingerprint = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
        batch_dir = self.config.run_dir / "hpc_tasks" / f"hybrid_{role}" / fingerprint
        manifests = [dict(mode="behavioral_hybrid_phase", fingerprint=identity, role=role, payload=payload)
                     for identity, payload in zip(identities, payloads, strict=True)]
        tasks = prepare_phase_wave(batch_dir=batch_dir, role=role, fingerprint=fingerprint,
            manifests=manifests, instance_ids=[payload.get("record", {}).get("instance_id", role) for payload in payloads],
            mismatch_error="hybrid task manifest mismatch")
        def validate(task, value):
            if value.get("fingerprint") != identities[task.index] or value.get("role") != role:
                raise ValueError("hybrid result identity mismatch")
            if not isinstance(value.get("agent_output"), dict) or not isinstance(value.get("trajectory"), list):
                raise ValueError("hybrid worker output schema mismatch")
            validate_phase(role, payloads[task.index], value["agent_output"], self.config)
        if self.config.execution.backend == "local":
            return [run_local_phase(self.config, role, payload, task.attempts_dir / "attempt_01")
                    for task, payload in zip(tasks, payloads, strict=True)]
        def write_script(indices, attempt):
            path = batch_dir / f"attempt_{attempt:02d}.sbatch"
            path.write_text(self.script_for_wave(batch_dir, role, indices, attempt))
            return path
        outputs = self.runtime.run(batch_dir=batch_dir, fingerprint=fingerprint, tasks=tasks,
            job_name=lambda attempt: self._job_name(role, fingerprint, attempt),
            write_script=write_script, validate_output=validate)
        return [(value["agent_output"], value["trajectory"]) for value in outputs]

    def reflect_batch(self, parent, records):
        if not records or any(record.get("split") != "train" for record in records):
            raise ValueError("hybrid reflection wave requires training records")
        if len({record["instance_id"] for record in records}) != len(records):
            raise ValueError("hybrid reflection wave requires unique instances")
        return self.run_wave("reflector", [{"parent": parent, "record": record} for record in records])

    def call(self, role, payload):
        return self.run_wave(role, [payload])[0]


def run_hybrid_optimization(config, *, phase_executor=None, checker=None, optimize_fn=None,
                            evaluation_backend=None):
    if not config.hybrid.enabled:
        raise ValueError("hybrid.enabled must be true")
    limits = hybrid_limits(config)
    validate_candidate(config.initial_rules_path.read_text(), limits)
    if phase_executor is None:
        verify_hybrid_snapshot(config)
    executor = phase_executor or HybridPhaseExecutor(config)
    proposer = IncrementalHybridACEProposer(
        lambda parent, record: executor.call("reflector", {"parent": parent, "record": record}),
        lambda parent, reviews, records, history, caps: executor.call("curator", {
            "parent": parent, "reviews": reviews, "records": records, "history": history, "limits": caps}),
        limits, ledger_path=config.run_dir / "global_counter_ledger.json",
        batch_reflector=(lambda parent, records: executor.reflect_batch(parent, records))
                        if isinstance(executor, HybridPhaseExecutor) else None,
        abort_on_failure=isinstance(executor, HybridPhaseExecutor),
        trace_dir=config.run_dir / "hybrid_proposals",
        refiner=(lambda draft, caps: executor.call("refiner", {"draft": draft, "limits": caps, "split": "train"}))
                if config.hybrid.refiner_enabled else None)
    kwargs = dict(checker=checker, proposer=proposer,
                  adapter_factory=partial(HybridBehavioralAdapter, limits=limits))
    if config.task.semantics == "safe_pce_resolution_v1":
        if checker is not None:
            raise ValueError("Safe PCE learning does not use a Checker")
        from .safe_pce_learning import SafePCEBackend, SafePCEGEPAAdapter
        backend = evaluation_backend or SafePCEBackend(config)
        kwargs.update(evaluation_backend=backend,
                      adapter_factory=partial(SafePCEGEPAAdapter, limits=limits))
    elif evaluation_backend is not None:
        raise ValueError("evaluation_backend is reserved for Safe PCE semantics")
    if optimize_fn is not None:
        kwargs["optimize_fn"] = optimize_fn
    return run_behavioral_optimization(config, **kwargs)
