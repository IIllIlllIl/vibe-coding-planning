"""Native subagent evidence gathering over the existing ACE transaction.

The main Curator owns delegation and reading choices. Host keeps the same
validated Ref -> counters/history -> Cur boundary inside one isolated session.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import yaml

from src.agents.output_contract import AgentOutputContractError
from src.optimization.agent_references import phase_references, decode_phase_output, project_identities
from src.optimization.behavioral_hybrid_ace import _playbook, apply_hybrid_delta
from src.optimization.behavioral_hybrid_evidence import _record_files
from src.optimization.codex_cli_runtime import CodexConversation, CodexSIFExecution
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.paired_playbook import validate_ace_paired_reflector_review
from src.optimization.pce_reflector_evidence import pce_reflector_views
from src.optimization.playbook_adapter import GlobalPlaybookCounters
from src.optimization.audit import optional_diagnostic


def validate_coordinated_output(payload, output, limits, *, reviews_only=False):
    if not isinstance(output, dict) or set(output) != {"reviews", "curator_delta"}:
        raise AgentOutputContractError("coordinator output fields: reviews, curator_delta")
    reviews = output["reviews"]
    if not isinstance(reviews, list) or len(reviews) != len(payload["records"]):
        raise AgentOutputContractError("coordinator requires one Ref report per supplied training case")
    book = _playbook(payload["parent"])
    for review, record in zip(reviews, payload["records"], strict=True):
        validate_ace_paired_reflector_review(review, instance_id=record["instance_id"],
                                            playbook=book, output_contract="ace_v2")
    if not reviews_only:
        apply_hybrid_delta(payload["parent"], output["curator_delta"], payload["records"], limits,
                           history={"events": payload["ledger"].get("history", {})})
    # Counts/history are reconstructed with the existing ledger, never inferred
    # from the Curator's prose or accepted as model-authored bookkeeping.
    return reviews


def write_coordinator_evidence(root, payload, limits, prompts):
    root.mkdir(parents=True, exist_ok=True)
    local, cases, items = phase_references("curator", payload)
    atomic_json(root / "parent.json", local["parent"])
    atomic_json(root / "ledger.json", payload["ledger"])
    ledger = GlobalPlaybookCounters(root / "ledger.json")
    book = ledger.hydrate(_playbook(payload["parent"]))
    atomic_json(root / "history.json", ledger.feedback_for(book, include_inactive=True))
    atomic_json(root / "limits.json", limits.agent_view())
    atomic_json(root / "roles.json", {key: prompts[key] for key in ("reflector_task", "curator_task", "reader_task")})
    records = []
    for number, record in enumerate(local["records"], 1):
        index = _record_files(root, record, f"records/case-{number:02d}")
        if record.get("task_semantics") == "safe_pce_resolution_v1":
            views, view_index = pce_reflector_views(record, index["fields"]["pce_runs"],
                                                  view_prefix=f"cases/case-{number:02d}/pce")
            view_index["reference_format"] = (
                "evidence_ref identifies a file and RFC 6901 JSON pointer into unchanged original data. "
                "Behavior files index complete model messages and command events; referenced bodies remain available.")
            # Different cases use distinct view namespaces; original archive
            # pointers continue to address the exact original physical records.
            for path, value in views.items():
                atomic_json(root / path, value)
            index["pce_evidence"] = view_index
        records.append(index)
    atomic_json(root / "records.json", records)
    files = [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
             for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json"]
    # Reuse the Native SIF manifest path, without prescribing reading priority.
    atomic_json(root / "manifest.json", {
        "schema_version": 1,
        "purpose": "Training evidence for autonomous subagent-assisted checklist learning",
        "entries": {
            "parent.json": "Current checklist and item identities",
            "records.json": "Local case references and lossless field paths: planner_input is the decision-time task; "
                            "pce_runs preserves Plans, Planner/Coder trajectories, patches and evaluator evidence; "
                            "score summarizes recorded evaluator outcomes",
            "ledger.json": "Original prior training counter/history state",
            "history.json": "Prior attributions, original reports and artifact contexts",
            "limits.json": "Hard item-count and tokenizer-token limits",
            "roles.json": "Case analysis, history reading and Curator tasks/output contracts"},
        "files": files})
    return local, cases, items, ledger, book


def verify_coordinator_evidence(attempt_dir, payload, output, limits):
    """Read-only fidelity checks for the existing offline dataflow audit."""
    root = attempt_dir / "inputs"
    local, cases, items = phase_references("curator", payload)
    first = json.loads((attempt_dir / "reviews_response.json").read_text())
    decoded = [decode_phase_output("reflector", row, cases, items) for row in first["reviews"]]
    by_id = {row["instance_id"]: row for row in decoded}
    if len(by_id) != len(decoded) or [by_id[row["instance_id"]] for row in payload["records"]] != output["reviews"]:
        raise ValueError("coordinator raw Ref reports differ from canonical output")
    second = json.loads((attempt_dir / "curator_response.json").read_text())
    if decode_phase_output("curator", second, cases, items) != output["curator_delta"]:
        raise ValueError("coordinator raw Curator output differs from canonical output")
    index = json.loads((root / "manifest.json").read_text())
    if "required_files" in index or "optional_files" in index:
        raise ValueError("coordinator inventory unexpectedly prescribes reading priority")
    for record, entry in zip(local["records"], json.loads((root / "records.json").read_text()), strict=True):
        restored = {key: json.loads((root / path).read_text()) for key, path in entry["fields"].items()}
        if restored != record:
            raise ValueError("coordinator changed a staged training record")
    ledger = GlobalPlaybookCounters(root / "ledger.json")
    if ledger.export() != payload["ledger"]:
        raise ValueError("coordinator changed its prior counter/history authority")
    book = ledger.hydrate(_playbook(payload["parent"]))
    counted, _ = ledger.apply_reviews(book, output["reviews"])
    parent = dict(payload["parent"], counters=[
        {"id": b.id, "helpful": b.helpful, "harmful": b.harmful, "neutral": b.neutral} for b in counted.bullets])
    if json.loads((root / "parent.json").read_text()) != project_identities({"parent": parent}, cases, items)["parent"]:
        raise ValueError("coordinator counted parent differs from existing ACE bookkeeping")
    if json.loads((root / "history.json").read_text()) != ledger.feedback_for(book, output["reviews"], payload["records"], include_inactive=True):
        raise ValueError("coordinator history differs from existing ACE bookkeeping")


def run_coordinated_phase(config, payload, attempt_dir, *, retry_feedback="", conversation_factory=CodexConversation):
    from src.optimization.behavioral_hybrid_runtime import hybrid_limits, require_evidence_sif
    bundle = Path(config.hybrid.prompt_bundle).read_bytes()
    if config.hybrid.prompt_bundle_sha256 and hashlib.sha256(bundle).hexdigest() != config.hybrid.prompt_bundle_sha256:
        raise ValueError("coordinator prompt bundle changed after configuration loading")
    prompts = yaml.safe_load(bundle)
    limits = hybrid_limits(config)
    evidence = attempt_dir / "inputs"
    local, cases, items, ledger, book = write_coordinator_evidence(evidence, payload, limits, prompts)
    atomic_json(attempt_dir / "reference_map.json", {"cases": cases.decode_map, "items": items.decode_map})
    model = dict(asdict(config.reflection), **config.hybrid.curator_model)
    boundary = CodexSIFExecution(require_evidence_sif(config), evidence,
        output_files=("reviews_response.json", "curator_response.json"), tool_network_disabled=True)
    trajectory = []
    last_raw = None
    try:
        with conversation_factory(model_config=model, container=boundary, attempt_dir=attempt_dir) as session:
            try:
                raw, first_trace = session.call_json(system=prompts["coordinator_task"],
                    user="The file structure and complete inventory are in /evidence/manifest.json. "
                         "The current cases are indexed by /evidence/records.json. " + retry_feedback,
                    task="Delegate training-case analysis and history/counter reading to subagents. "
                         "Collect the case reports. Return a JSON object containing only reviews, "
                         "an array with one Reflector report for each current case. Host will update bookkeeping before "
                         "continuing this same Curator conversation.", output_filename="reviews_response.json")
                trajectory.extend(first_trace)
                last_raw = raw
                atomic_json(attempt_dir / "reviews_completion.json", {"agent_output": raw, "trajectory": first_trace})
                if set(raw) != {"reviews"} or not isinstance(raw["reviews"], list):
                    raise AgentOutputContractError("first turn fields: reviews array")
                decoded = [decode_phase_output("reflector", review, cases, items) for review in raw["reviews"]]
                by_id = {review["instance_id"]: review for review in decoded}
                expected = [record["instance_id"] for record in payload["records"]]
                if len(by_id) != len(decoded) or set(by_id) != set(expected):
                    raise AgentOutputContractError("Ref case IDs must cover each supplied case exactly once")
                reviews = [by_id[identifier] for identifier in expected]
                validate_coordinated_output(payload, {"reviews": reviews, "curator_delta": {}}, limits,
                                            reviews_only=True)
                counted, _ = ledger.apply_reviews(book, reviews)
                history = ledger.feedback_for(book, reviews, payload["records"], include_inactive=True)
                counted_parent = dict(payload["parent"], counters=[
                    {"id": b.id, "helpful": b.helpful, "harmful": b.harmful, "neutral": b.neutral}
                    for b in counted.bullets])
                atomic_json(evidence / "parent.json", project_identities({"parent": counted_parent}, cases, items)["parent"])
                atomic_json(evidence / "history.json", history)
                atomic_json(evidence / "reviews.json", raw["reviews"])
                index = json.loads((evidence / "manifest.json").read_text())
                index["entries"]["reviews.json"] = "Unchanged current reports accepted by Host"
                index["files"] = [{"path": str(path.relative_to(evidence)), "bytes": path.stat().st_size}
                                  for path in sorted(evidence.rglob("*"))
                                  if path.is_file() and path.name != "manifest.json"]
                atomic_json(evidence / "manifest.json", index)
                delta, second_trace = session.call_json(system=prompts["coordinator_task"],
                    user="Host validated the reports and updated /evidence/parent.json counters and "
                         "/evidence/history.json using the existing ACE policy. /evidence/reviews.json "
                         "preserves your submitted reports. Continue in your Curator role; delegate "
                         "further evidence/history lookup as useful.",
                    task="Maintain the checklist using the Curator task in /evidence/roles.json. "
                         "Return a JSON object with exactly reasoning and operations.", output_filename="curator_response.json")
                trajectory.extend(second_trace)
                last_raw = delta
                output = {"reviews": reviews,
                          "curator_delta": decode_phase_output("curator", delta, cases, items)}
                effective_limits = limits.relaxed() if config.hybrid.refiner_enabled else limits
                apply_hybrid_delta(payload["parent"], output["curator_delta"], payload["records"],
                                   effective_limits, counted=counted, history=history)
            finally:
                with optional_diagnostic("subagent session evidence"):
                    trajectory.append({"role": "codex_session_archive", "content": session.session_evidence()})
    except Exception as exc:
        exc.trajectory = trajectory + getattr(exc, "trajectory", [])
        if last_raw is not None:
            exc.raw_agent_output = last_raw
        exc.worker_failure_stage = "agent_output_validation" if isinstance(exc, (ValueError, AgentOutputContractError)) else "agent_execution"
        raise
    return output, trajectory
