"""Whole-candidate reflection using the existing Native/Worker/GEPA interfaces."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import yaml

from src.exceptions import ControllerYield, OfflineReflectionBlocked
from src.optimization.audit import optional_diagnostic
from src.optimization.behavioral_hybrid import validate_candidate
from src.optimization.behavioral_hybrid_evidence import write_training_record_index
from src.optimization.codex_cli_runtime import CodexConversation, CodexSIFExecution
from src.optimization.hpc.task_batch import atomic_json


def require_training_records(records):
    if not records or any(record.get("split") != "train" for record in records):
        raise ValueError("reflection accepts training evidence only")
    if len({record["instance_id"] for record in records}) != len(records):
        raise ValueError("reflection requires unique training instances")


def write_reflection_evidence(root, payload, limits):
    require_training_records(payload["records"])
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "parent.json", payload["parent"])
    atomic_json(root / "limits.json", limits.agent_view())
    write_training_record_index(root, payload["records"])
    atomic_json(root / "manifest.json", {
        "schema_version": 1,
        "purpose": "Training evidence for complete planning-checklist replacement",
        "entries": {
            "parent.json": "The current candidate; Planner rule_number N refers to its Nth item",
            "limits.json": "Hard item-count and tokenizer-token limits",
            "records.json": "Training case field paths and lossless PCE views: planner_input is the "
                            "decision-time task; pce_runs contains exact Plans, Planner/Coder trajectories, "
                            "patches and official outcomes; score summarizes those outcomes. "
                            "Retrospective diagnostics, when present, are learning-only references."},
        "files": [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
                  for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json"]})


def verify_reflection_evidence(attempt_dir, payload, output, limits):
    """Offline fidelity audit; never a second runtime acceptance gate."""
    from src.optimization.pce_reflector_evidence import pce_reflector_views
    root = attempt_dir / "inputs"
    require_training_records(payload["records"])
    for name, expected in (("parent.json", payload["parent"]), ("limits.json", limits.agent_view())):
        if json.loads((root / name).read_text()) != expected:
            raise ValueError(f"reflection changed its staged {name}")
    if json.loads((attempt_dir / "candidate_response.json").read_text()) != output:
        raise ValueError("reflection raw candidate differs from Worker output")
    if json.loads((attempt_dir / "completion.json").read_text())["agent_output"] != output:
        raise ValueError("reflection completion differs from Worker output")
    manifest = json.loads((root / "manifest.json").read_text())
    if {"required_files", "optional_files"} & manifest.keys():
        raise ValueError("reflection inventory prescribes reading priority")
    indexes = json.loads((root / "records.json").read_text())
    if len(indexes) != len(payload["records"]):
        raise ValueError("reflection record count differs")
    for number, (record, entry) in enumerate(zip(payload["records"], indexes, strict=True), 1):
        if {key: json.loads((root / path).read_text()) for key, path in entry["fields"].items()} != record:
            raise ValueError("reflection changed a staged training record")
        if record.get("task_semantics") == "safe_pce_resolution_v1":
            views, expected = pce_reflector_views(record, entry["fields"]["pce_runs"],
                                                   view_prefix=f"cases/case-{number:02d}/pce")
            if entry["pce_evidence"]["runs"] != expected["runs"]:
                raise ValueError("reflection PCE view index differs")
            for path, value in views.items():
                if json.loads((root / path).read_text()) != value:
                    raise ValueError(f"reflection PCE view differs: {path}")


def run_reflection_phase(config, payload, attempt_dir, *, retry_feedback="",
                         conversation_factory=CodexConversation):
    from src.optimization.behavioral_hybrid_runtime import hybrid_limits, require_evidence_sif
    bundle = Path(config.hybrid.prompt_bundle).read_bytes()
    if config.hybrid.prompt_bundle_sha256 and hashlib.sha256(bundle).hexdigest() != config.hybrid.prompt_bundle_sha256:
        raise ValueError("reflection prompt bundle changed after configuration loading")
    prompt = yaml.safe_load(bundle)["reflection_task"]
    limits = hybrid_limits(config)
    evidence = attempt_dir / "inputs"
    write_reflection_evidence(evidence, payload, limits)
    model = dict(asdict(config.reflection), **config.hybrid.curator_model)
    boundary = CodexSIFExecution(require_evidence_sif(config), evidence,
        output_files=("candidate_response.json",), tool_network_disabled=True)
    trajectory, raw = [], None
    try:
        with conversation_factory(model_config=model, container=boundary, attempt_dir=attempt_dir) as session:
            try:
                raw, trace = session.call_json(system=prompt,
                    user="The file structure and complete evidence inventory are in /evidence/manifest.json. "
                         + retry_feedback,
                    task="Return the complete improved planning checklist as a JSON object.",
                    output_filename="candidate_response.json")
                trajectory.extend(trace)
                # Save the original submission before validation, without repair.
                atomic_json(attempt_dir / "completion.json", {"agent_output": raw, "trajectory": trace})
                validate_candidate(json.dumps(raw, ensure_ascii=False), limits)
            finally:
                with optional_diagnostic("reflection session evidence"):
                    trajectory.append({"role": "codex_session_archive", "content": session.session_evidence()})
    except Exception as exc:
        exc.trajectory = trajectory + getattr(exc, "trajectory", [])
        if raw is not None:
            exc.raw_agent_output = raw
        exc.worker_failure_stage = "agent_output_validation" if isinstance(exc, ValueError) else "agent_execution"
        raise
    return raw, trajectory


class FullCandidateProposer:
    """GEPA custom proposer; no ACE operations or model-authored bookkeeping."""
    def __init__(self, phase, limits, *, trace_dir=None, abort_on_failure=False):
        self.phase, self.limits = phase, limits
        self.trace_dir, self.abort_on_failure = trace_dir, abort_on_failure
        self.successful_proposals = 0
        self.failures = []

    def __call__(self, candidate, reflective_dataset, components_to_update):
        if components_to_update != ["rules"]:
            raise ValueError("only rules may be updated")
        parent = validate_candidate(candidate["rules"], self.limits)
        records = list(reflective_dataset["rules"])
        require_training_records(records)
        payload = {"parent": parent, "records": records, "proposal_index": self.successful_proposals}
        trace = dict(payload)
        try:
            output, trajectory = self.phase(payload)
            trace.update(agent_output=output, reflection_trajectory=trajectory)
            proposed = json.dumps(output, ensure_ascii=False)
            validate_candidate(proposed, self.limits)
        except ControllerYield:
            raise
        except Exception as exc:
            self.failures.append({"error_type": type(exc).__name__, "error": str(exc)})
            if self.abort_on_failure:
                raise OfflineReflectionBlocked(exc) from exc
            raise
        self.successful_proposals += 1
        if self.trace_dir is not None:
            # Reporting is not a second checkpoint or an acceptance gate.
            with optional_diagnostic("proposal trace"):
                atomic_json(self.trace_dir / f"proposal-{payload['proposal_index']:04d}.json",
                            dict(trace, candidate=proposed, status="completed"))
        return {"rules": proposed}
