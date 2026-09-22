"""Prepare an explicitly hash-pinned failed paired proposal for in-place resume.

Dry-run by default. No Slurm submission and no model calls. The run must be
inactive; the controller lock is acquired and all original files are backed up.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import pickle
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.optimization.pending_playbook_resume import MARKER, file_hash


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def prepare(root: Path, authority: dict, *, apply=False, repo=REPO):
    root = root.resolve()
    if root.name != authority["run_name"]:
        raise ValueError("wrong run root")
    with (root / "controller.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _prepare(root, authority, apply=apply, repo=repo)


def _prepare(root, authority, *, apply, repo):
    manifest = json.loads((root / "run_manifest.json").read_text())
    if (root / MARKER).exists() and manifest["semantic_config"].get("pending_proposal_recovery") == file_hash(root / MARKER):
        if json.loads((root / MARKER).read_text())["authority"] != authority:
            raise ValueError("existing recovery has another authority")
        return {"status": "already_prepared", "run_dir": str(root)}
    for name, sha in authority["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts or file_hash(root / name) != sha:
            raise ValueError(f"recovery authority mismatch: {name}")
    state = pickle.loads((root / "gepa_state.bin").read_bytes())
    resume = json.loads((root / "gepa_resume_state.json").read_text())
    trace = state["full_program_trace"][-1]
    if (state["i"] != authority["failed_iteration"] - 1
            or resume["gepa_state_i"] != state["i"] or trace["i"] != state["i"]
            or set(trace) != {"i", "selected_program_candidate", "subsample_ids", "subsample_scores"}
            or resume["reflection_failures"] != [{"error_type": "ValueError", "error": authority["error"]}]):
        raise ValueError("not the pinned pre-evaluation failed proposal")
    if len(state["program_candidates"]) != authority["candidate_count"]:
        raise ValueError("candidate pool changed")
    parent = state["program_candidates"][trace["selected_program_candidate"]]
    entries = [state["evaluation_cache"].get(parent, i) for i in trace["subsample_ids"]]
    if any(e is None for e in entries) or [e.score for e in entries] != trace["subsample_scores"]:
        raise ValueError("original parent evaluation cache is incomplete")
    task = json.loads((root / authority["curator_task"]).read_text())
    result = json.loads((root / authority["curator_result"]).read_text())
    if result["status"] != "completed" or result["fingerprint"] != task["fingerprint"]:
        raise ValueError("original Curator completion is invalid")
    reviews = json.loads((root / authority["reviews"]).read_text())
    if [r["instance_id"] for r in reviews] != trace["subsample_ids"]:
        raise ValueError("Reflection order differs from failed minibatch")
    pending = {"schema_version": 1, "authority": authority,
               "trace": deepcopy(trace), "parent": parent,
               "parent_outputs": [e.output for e in entries],
               "reviews": reviews,
               "counted_playbook": task["prompt_values"]["counted_internal_playbook"],
               "curator_output": result["agent_output"]}
    marker_bytes = encoded(pending)
    semantic = manifest["semantic_config"]
    # Scientific YAML, prompts, dataset, selection and model settings are unchanged.
    # Only explicitly listed source fixes may change their recorded hash.
    for name, new_sha in authority["replacement_source_hashes"].items():
        path = repo / "src/optimization" / name
        if file_hash(path) != new_sha:
            raise ValueError(f"unexpected recovery implementation: {name}")
        semantic["source"][name] = new_sha
    for name, sha in semantic["source"].items():
        paths = [repo / "src/optimization" / name, repo / "src/environment" / name]
        if not any(p.is_file() and file_hash(p) == sha for p in paths):
            raise ValueError(f"unapproved source change: {name}")
    semantic["pending_proposal_recovery"] = hashlib.sha256(marker_bytes).hexdigest()
    manifest["semantic_sha256"] = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
    # Replaying the same cached parent adds these logical calls back exactly once.
    # The original physical work and call accounting remain in the immutable backup.
    state["i"] -= 1
    state["full_program_trace"] = state["full_program_trace"][:-1]
    state["total_num_evals"] -= len(trace["subsample_ids"])
    resume["gepa_state_i"] = state["i"]
    resume["reflection_failures"] = []
    # Keep post-draw RNG, sampler, candidates, frontier, evaluation cache and ledger.
    writes = {MARKER: marker_bytes, "gepa_state.bin": pickle.dumps(state),
              "gepa_resume_state.json": encoded(resume),
              "controller_status.json": encoded({"schema_version": 1, "status": "yielded", "reason": "prepared_pending_proposal_resume"}),
              "iteration_progress.json": encoded({"schema_version": 1, "first_observed_completed_iterations": 0,
                                                  "completed_iterations": state["i"] + 1, "last_event": "pending_proposal_resume_prepared"}),
              "progress.json": encoded({"status": "resumable", "iteration": state["i"] + 1,
                                        "accepted_candidates": len(state["program_candidates"]) - 1,
                                        "metric_calls_used": state["total_num_evals"]}),
              "run_manifest.json": encoded(manifest)}
    summary = {"status": "prepared" if apply else "dry_run", "run_dir": str(root),
               "completed_iterations": state["i"] + 1, "pending_iteration": trace["i"] + 1,
               "parent_candidate": trace["selected_program_candidate"], "pairs": len(entries),
               "candidate_count": len(state["program_candidates"]), "agent_calls": 0}
    if apply:
        backup = root / "recovery_backups" / authority["recovery_id"]
        if backup.exists():
            raise ValueError("recovery backup already exists; inspect interrupted preparation before retry")
        backup.mkdir(parents=True)
        for name in set(writes) | set(authority["files"]):
            source = root / name
            if source.is_file():
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        (backup / "authority.json").write_bytes(encoded(authority))
        (backup / "preparation.json").write_bytes(encoded(summary))
        for name, data in writes.items():
            temporary = root / (name + ".recovery.tmp")
            temporary.write_bytes(data)
            temporary.replace(root / name)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--authority", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.run_dir, json.loads(args.authority.read_text()), apply=args.apply), sort_keys=True))


if __name__ == "__main__":
    main()
