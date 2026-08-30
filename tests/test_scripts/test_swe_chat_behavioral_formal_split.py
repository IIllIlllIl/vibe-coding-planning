from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.tools.freeze_swe_chat_behavioral_formal_split import freeze_split


def _write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _case(case_id: str, repo: str, plan: str, signal: str) -> dict:
    return {
        "case_id": case_id,
        "boundary": {
            "proposed_plan_sha256": hashlib.sha256(plan.encode()).hexdigest(),
        },
        "selection_provenance": {"repo_id": repo},
        "checker_visible": {
            "events": [
                {
                    "role": "user",
                    "turn_type": "user_prompt",
                    "content": f"task for {case_id}",
                }
            ],
            "proposed_plan": plan,
        },
        "reflection_only": {"behavior_signal": signal},
    }


def _inputs(root: Path) -> dict[str, Path]:
    specifications = [
        ("dev", "org/dev", "Inspect parser and add a regression test.", "explicit_approval"),
        ("same-repo", "org/dev", "A different plan.", "explicit_rejection"),
        (
            "near-copy",
            "org/copy",
            "Inspect parser and add a regression test.",
            "explicit_rejection",
        ),
        ("held-out", "org/held", "Update documentation links.", "explicit_approval"),
    ]
    entries = []
    proxies = []
    case_root = root / "stage2"
    for index, (case_id, repo, plan, signal) in enumerate(specifications):
        relative = f"cases/{case_id}.json"
        case_hash = _write(case_root / relative, _case(case_id, repo, plan, signal))
        entries.append(
            {
                "case_id": case_id,
                "status": "eligible",
                "case_path": relative,
                "case_sha256": case_hash,
            }
        )
        proxies.append(
            {
                "case_id": case_id,
                "repo_id": repo,
                "proxy_source": (
                    "recorded_branch" if index % 2 else "all_reachable_refs"
                ),
            }
        )
    stage2 = root / "stage2.json"
    cleaning = root / "cleaning.json"
    proxy = root / "proxy.json"
    development = root / "development.json"
    _write(stage2, {"content_sha256": "stage2", "cases": entries})
    _write(cleaning, {"content_sha256": "cleaning", "excluded_cases": []})
    _write(proxy, {"content_sha256": "proxy", "cases": proxies})
    development_value = {
        "complete": True,
        "provisional": False,
        "assignments": [{"case_id": "dev", "split": "train"}],
    }
    development_value["content_sha256"] = _content_hash(development_value)
    _write(development, development_value)
    return {
        "stage2_manifest_path": stage2,
        "stage2_case_root": case_root,
        "repository_cleaning_path": cleaning,
        "proxy_manifest_path": proxy,
        "development_split_path": development,
    }


def test_formal_split_keeps_development_and_duplicate_components_in_train(
    tmp_path,
) -> None:
    inputs = _inputs(tmp_path)
    output = tmp_path / "formal-split.json"

    manifest = freeze_split(**inputs, output_path=output)
    by_id = {item["case_id"]: item for item in manifest["assignments"]}

    assert by_id["dev"]["split"] == "train"
    assert by_id["same-repo"]["split"] == "train"
    assert by_id["near-copy"]["split"] == "train"
    assert by_id["held-out"]["split"] == "validation"
    assert by_id["dev"]["dedup_group"] == by_id["near-copy"]["dedup_group"]
    assert manifest["duplicate_audit"]["cross_split_repository_or_duplicate_components"] == 0
    assert manifest["content_sha256"] == _content_hash(manifest)


def test_formal_split_assignment_does_not_change_when_labels_are_inverted(
    tmp_path,
) -> None:
    inputs = _inputs(tmp_path)
    first = freeze_split(**inputs, output_path=tmp_path / "first.json")
    for path in inputs["stage2_case_root"].glob("cases/*.json"):
        case = json.loads(path.read_text())
        signal = case["reflection_only"]["behavior_signal"]
        case["reflection_only"]["behavior_signal"] = (
            "explicit_rejection"
            if signal == "explicit_approval"
            else "explicit_approval"
        )
        path.write_text(json.dumps(case), encoding="utf-8")
    stage2 = json.loads(inputs["stage2_manifest_path"].read_text())
    for entry in stage2["cases"]:
        entry["case_sha256"] = hashlib.sha256(
            (inputs["stage2_case_root"] / entry["case_path"]).read_bytes()
        ).hexdigest()
    inputs["stage2_manifest_path"].write_text(json.dumps(stage2), encoding="utf-8")

    second = freeze_split(**inputs, output_path=tmp_path / "second.json")

    assert [
        (item["case_id"], item["split"], item["dedup_group"])
        for item in first["assignments"]
    ] == [
        (item["case_id"], item["split"], item["dedup_group"])
        for item in second["assignments"]
    ]
