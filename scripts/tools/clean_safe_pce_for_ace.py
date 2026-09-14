#!/usr/bin/env python3
"""Derive an immutable ACE eligibility ledger from a Safe PCE run.

The raw run remains authoritative and is never rewritten.  This tool retains
only terminal official evaluator outcomes, excludes confirmed source-boundary
exposure, and records unfinished cases separately for later evaluation.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


OFFICIAL_TERMINALS = {
    "official_tests_resolved",
    "official_tests_unresolved",
}
BENIGN_NETWORK_HOSTS = {
    "example.com",
    "example.net",
    "example.org",
    "httpbin.org",
    "localhost",
    "127.0.0.1",
}
SOLUTION_PATH_PARTS = (
    "/pull/",
    "/pulls/",
    "/commit/",
    "/commits/",
    "/compare/",
    "/changeset/",
    "/raw/",
    ".patch",
    ".diff",
)
URL_RE = re.compile(r"https?://[^\s<>'\"`]+", re.IGNORECASE)
HTTP_CLIENT_RE = re.compile(
    r"(?im)(?:^|[;&|\n]\s*)"
    r"(?:timeout\s+\d+(?:\.\d+)?\s+)?"
    r"(?:curl|wget)\b|"
    r"\b(?:requests|httpx)\.(?:get|head|post|put|patch|delete)\s*\(|"
    r"\burllib\.request\.urlopen\s*\("
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ordered_ids_sha256(instance_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(instance_ids).encode("utf-8")).hexdigest()


def _canonical_url(value: str) -> str:
    value = value.rstrip(".,;:)]}")
    parts = urlsplit(value)
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            parts.query,
            "",
        )
    )


def _extract_urls(value: str) -> set[str]:
    urls = set()
    for match in URL_RE.finditer(value):
        try:
            urls.add(_canonical_url(match.group(0)))
        except ValueError:
            # Malformed URL-shaped test inputs are not evidence of a
            # successful source fetch.  They remain available in raw traces.
            continue
    return urls


def _host_is_benign(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    labels = host.split(".")
    # Malformed URL values used in parser/validation tests cannot be a
    # successful source fetch and must not become cleaning false positives.
    if not host or "*" in host or any(not label for label in labels):
        return True
    return host in BENIGN_NETWORK_HOSTS or any(
        host.endswith(f".{item}") for item in BENIGN_NETWORK_HOSTS
    )


def _action_uses_http_client(action: str) -> bool:
    return HTTP_CLIENT_RE.search(action) is not None


def _is_solution_surface(url: str) -> bool:
    lowered = url.casefold()
    host = (urlsplit(url).hostname or "").casefold()
    if any(part in lowered for part in SOLUTION_PATH_PARTS):
        return True
    if host in {"raw.githubusercontent.com", "cdn.jsdelivr.net"}:
        return True
    if host == "api.github.com" and any(
        part in lowered for part in ("/git/", "/contents/", "/issues/")
    ):
        return True
    return False


def _parse_observation(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("role") != "user":
        return None
    content = str(message.get("content", ""))
    if not content.startswith("Observation: "):
        return None
    try:
        parsed = ast.literal_eval(content.removeprefix("Observation: "))
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _target_tokens(case: dict[str, Any]) -> set[str]:
    repo = str(case.get("repo", ""))
    name = repo.rsplit("/", 1)[-1].casefold()
    tokens = {name}
    if name == "scikit-learn":
        tokens.update({"scikit_learn", "sklearn"})
    return {token for token in tokens if token}


def _outside_target_exposure(
    *, action: str, output: str, case: dict[str, Any]
) -> str | None:
    """Return a narrow reason for successful non-worktree target evidence.

    Merely searching or seeing an editable-install path is not enough.  The
    command must expose future target-package metadata or read target source
    outside /testbed.
    """
    lowered_action = action.casefold()
    tokens = _target_tokens(case)
    if not any(token in lowered_action for token in tokens):
        return None

    if "/conda-meta/" in lowered_action and any(
        token in lowered_action for token in tokens
    ):
        return "FUTURE_TARGET_PACKAGE_METADATA_EXPOSED"

    if "start_plan" in lowered_action or "end_plan" in lowered_action:
        return None
    for token in tokens:
        concrete_path = re.search(
            rf"/(?:opt|usr|root|home|srv|var)/[^\s;'\"]*{re.escape(token)}"
            r"[^\s;'\"]*(?:\.py|\.json|\.patch|\.diff)\b",
            lowered_action,
        )
        if concrete_path and "*" not in concrete_path.group(0):
            before = lowered_action[: concrete_path.start()]
            if re.search(r"(?:^|[;&|\n]\s*)(?:cat|sed|head|tail|grep|rg)\b", before):
                return "ALTERNATE_TARGET_SOURCE_EXPOSED"
    return None


def _trajectory_findings(
    output: dict[str, Any], case: dict[str, Any]
) -> tuple[set[str], list[dict[str, Any]]]:
    reasons: set[str] = set()
    evidence: list[dict[str, Any]] = []
    prompt_urls = _extract_urls(str(case.get("issue_description", "")))
    for phase_key in ("plan_trajectory", "code_trajectory"):
        phase = phase_key.removesuffix("_trajectory")
        for message in output.get(phase_key, []):
            observation = _parse_observation(message)
            if observation is None or observation.get("returncode") != 0:
                continue
            action = str(observation.get("action", ""))
            observed_output = str(observation.get("output", ""))
            if _action_uses_http_client(action):
                for url in sorted(_extract_urls(action)):
                    if _host_is_benign(url):
                        continue
                    if url not in prompt_urls or _is_solution_surface(url):
                        reasons.add("EXECUTED_SOLUTION_OR_UNFROZEN_HTTP")
                        evidence.append(
                            {
                                "phase": phase,
                                "kind": "executed_http",
                                "url": url,
                            }
                        )
            local_reason = _outside_target_exposure(
                action=action,
                output=observed_output,
                case=case,
            )
            if local_reason:
                reasons.add(local_reason)
                evidence.append(
                    {
                        "phase": phase,
                        "kind": "local_alternate_source",
                        "reason": local_reason,
                        "action_sha256": hashlib.sha256(
                            action.encode("utf-8")
                        ).hexdigest(),
                    }
                )
    return reasons, evidence


def _source_event_findings(
    *, run_root: Path, batch_root: Path, task_index: int
) -> tuple[set[str], list[dict[str, Any]]]:
    reasons: set[str] = set()
    evidence: list[dict[str, Any]] = []
    attempts = batch_root / "attempts" / f"task_{task_index:04d}"
    for log_path in sorted(attempts.glob("attempt_*/source_access.jsonl")):
        for event in _read_jsonl(log_path):
            if not event.get("executed") or event.get("returncode") != 0:
                continue
            reason = str(event.get("reason", ""))
            urls = [_canonical_url(str(url)) for url in event.get("urls", [])]
            unsafe = reason == "prompt_solution_surface" or any(
                not _host_is_benign(url)
                and (
                    reason == "non_prompt_http"
                    or _is_solution_surface(url)
                )
                for url in urls
            )
            if unsafe:
                reasons.add("EXECUTED_SOLUTION_OR_UNFROZEN_HTTP")
                evidence.append(
                    {
                        "kind": "source_access_event",
                        "phase": event.get("phase"),
                        "reason": reason,
                        "urls": urls,
                        "log_relative_path": str(log_path.relative_to(run_root)),
                    }
                )
    return reasons, evidence


def _find_batch_root(run_root: Path) -> Path:
    candidates = sorted((run_root / "hpc_tasks" / "pce").glob("*"))
    candidates = [path for path in candidates if path.is_dir()]
    if len(candidates) != 1:
        raise ValueError(f"expected one PCE batch under {run_root}, got {candidates}")
    return candidates[0]


def build(*, run_root: Path, selection_path: Path, output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"refusing to modify existing output: {output_dir}")
    selection = _read_json(selection_path)
    selected = list(selection["selected_instance_ids"])
    selected_cases = {
        row["instance_id"]: row for row in selection["selected_cases"]
    }
    batch_root = _find_batch_root(run_root)

    ledger: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    retained_outcomes: Counter[str] = Counter()

    for task_index, instance_id in enumerate(selected):
        task_path = batch_root / "tasks" / f"task_{task_index:04d}.json"
        output_path = batch_root / "outputs" / f"task_{task_index:04d}.json"
        task = _read_json(task_path)
        case = task["case"]
        if task.get("instance_id") != instance_id or case.get("instance_id") != instance_id:
            raise ValueError(f"task identity mismatch at index {task_index}")

        base = {
            "instance_id": instance_id,
            "repo": case["repo"],
            "task_index": task_index,
            "source_case_row_sha256": selected_cases[instance_id]["row_sha256"],
            "task_relative_path": str(task_path.relative_to(run_root)),
        }
        if not output_path.exists():
            row = {**base, "action": "defer_test", "reason_codes": ["NO_TERMINAL_OUTPUT"]}
            ledger.append(row)
            deferred.append(row)
            continue

        output = _read_json(output_path)
        if output.get("status") != "completed":
            row = {
                **base,
                "action": "defer_test",
                "reason_codes": ["NONTERMINAL_OUTPUT"],
                "pce_status": output.get("pce_status"),
                "terminal_phase": output.get("terminal_phase"),
                "terminal_reason": output.get("terminal_reason"),
            }
            ledger.append(row)
            deferred.append(row)
            continue

        evaluator = output.get("evaluator_result", {})
        outcome = str(evaluator.get("task_outcome", "unknown"))
        outcome_counts[outcome] += 1
        reasons: set[str] = set()
        evidence: list[dict[str, Any]] = []
        if output.get("terminal_reason") not in OFFICIAL_TERMINALS:
            reasons.add("NON_OFFICIAL_EVALUATOR_TERMINAL")
        if evaluator.get("classification_policy") != "swe_verified_pce_outcomes_v1":
            reasons.add("OUTCOME_AUTHORITY_INVALID")
        if outcome not in {"resolved", "unresolved"}:
            reasons.add("OUTCOME_AUTHORITY_INVALID")

        trajectory_reasons, trajectory_evidence = _trajectory_findings(output, case)
        source_reasons, source_evidence = _source_event_findings(
            run_root=run_root,
            batch_root=batch_root,
            task_index=task_index,
        )
        reasons.update(trajectory_reasons)
        reasons.update(source_reasons)
        evidence.extend(trajectory_evidence)
        evidence.extend(source_evidence)

        row = {
            **base,
            "action": "exclude" if reasons else "retain_training",
            "reason_codes": sorted(reasons),
            "outcome": outcome,
            "terminal_reason": output.get("terminal_reason"),
            "output_relative_path": str(output_path.relative_to(run_root)),
            "output_row_sha256": output.get("row_sha256"),
            "plan_sha256": output.get("plan_sha256"),
            "patch_sha256": output.get("patch_sha256"),
            "audit_evidence": evidence,
        }
        ledger.append(row)
        if reasons:
            excluded.append(row)
        else:
            retained.append(row)
            retained_outcomes[outcome] += 1

    if len(ledger) != len(selected):
        raise AssertionError("cleaning ledger is not exhaustive")

    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "audit_ledger.jsonl", ledger)
    _write_jsonl(output_dir / "training_instances.jsonl", retained)
    _write_jsonl(output_dir / "excluded_instances.jsonl", excluded)
    _write_jsonl(output_dir / "deferred_test_instances.jsonl", deferred)
    reason_counts = Counter(
        reason for row in excluded for reason in row["reason_codes"]
    )
    manifest = {
        "schema_version": 1,
        "cleaning_policy": "safe-pce-ace-reliability-v1",
        "immutable": True,
        "complete_for_observed_run_state": True,
        "raw_run_unchanged": True,
        "source_run_root": str(run_root),
        "source_run_manifest_sha256": _sha256(run_root / "run_manifest.json"),
        "source_selection": str(selection_path),
        "source_selection_sha256": _sha256(selection_path),
        "source_batch_fingerprint": batch_root.name,
        "selected_instances": len(selected),
        "terminal_instances": sum(outcome_counts.values()),
        "terminal_outcomes": dict(sorted(outcome_counts.items())),
        "retained_training_instances": len(retained),
        "retained_training_outcomes": dict(sorted(retained_outcomes.items())),
        "excluded_instances": len(excluded),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "deferred_test_instances": len(deferred),
        "deferred_selection_caveat": (
            "Operational leftovers, not a random or prevalence-representative holdout."
        ),
        "network_policy": (
            "Exclude successful non-benign HTTP outside frozen issue URLs and "
            "successful access to solution surfaces; blocked attempts do not exclude."
        ),
        "local_source_policy": (
            "Exclude successful exposure of future target-package metadata or "
            "target source content outside /testbed; unsuccessful probes and "
            "editable-install path discovery alone do not exclude."
        ),
        "evaluator_policy": (
            "Retain only official_tests_resolved or official_tests_unresolved "
            "under swe_verified_pce_outcomes_v1."
        ),
        "ordered_training_ids_sha256": _ordered_ids_sha256(
            [row["instance_id"] for row in retained]
        ),
        "ordered_deferred_test_ids_sha256": _ordered_ids_sha256(
            [row["instance_id"] for row in deferred]
        ),
        "artifacts": {},
    }
    for name in (
        "audit_ledger.jsonl",
        "training_instances.jsonl",
        "excluded_instances.jsonl",
        "deferred_test_instances.jsonl",
    ):
        manifest["artifacts"][name] = _sha256(output_dir / name)
    _write_json(output_dir / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(
        run_root=args.run_root.resolve(),
        selection_path=args.selection.resolve(),
        output_dir=args.output.resolve(),
    )


if __name__ == "__main__":
    main()
