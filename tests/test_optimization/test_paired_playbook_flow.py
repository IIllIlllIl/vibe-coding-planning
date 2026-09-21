from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from src.optimization import playbook_cli
from src.evaluator.swe_evaluator import derive_image_name
from src.optimization.models import (
    PairedGEPACase,
    PairedPlanObservation,
    RepositoryRef,
)
from src.optimization.paired_dataset import load_paired_snapshot
from src.optimization.paired_playbook import validate_paired_reflector_review
from src.optimization.playbook import PlaybookBullet, RejectPlaybook
from src.optimization.playbook_adapter import PairedRepoPlaybookGEPAAdapter
from src.optimization.playbook_cli import _validate_frozen_inputs
from src.optimization.playbook_hpc_agents import HPCPairedRepoPlaybookChecker


def _observation(name: str, plan: str) -> PairedPlanObservation:
    return PairedPlanObservation(
        observation_id=name,
        plan=plan,
        plan_sha256=hashlib.sha256(plan.encode()).hexdigest(),
        historical_evidence={},
    )


def _case(pair_id: str = "pair-1", task_id: str = "org__repo-1") -> PairedGEPACase:
    return PairedGEPACase(
        instance_id=pair_id,
        task_id=task_id,
        split="train",
        issue_description="Fix the behavior.",
        repository=RepositoryRef("org/repo", "abc123", task_id),
        resolved_observation=_observation("observation-a", "# Plan\nGood"),
        unresolved_observation=_observation("observation-b", "# Plan\nBad"),
    )


def _result(triggered: bool) -> dict:
    return {
        "rule_results": [
            {
                "rule_number": 1,
                "triggered": triggered,
                "finding": "Material mismatch" if triggered else None,
                "evidence": (
                    [{"source": "plan", "location": None, "observation": "Mismatch"}]
                    if triggered
                    else []
                ),
                "reason": "Supported" if triggered else "Not supported",
            }
        ]
    }


class _Checker:
    def __init__(self, resolved_reject: bool, unresolved_reject: bool) -> None:
        self.values = (resolved_reject, unresolved_reject)

    def evaluate_batch(self, batch, playbook):
        del playbook
        return [
            ((_result(self.values[0]), []), (_result(self.values[1]), []))
            for _ in batch
        ]


@pytest.mark.parametrize(
    ("resolved_reject", "unresolved_reject", "score", "decision"),
    [
        (False, True, 1.0, "CORRECT_ORDER"),
        (True, False, -1.0, "INVERTED"),
        (False, False, 0.0, "TIED"),
        (True, True, 0.0, "TIED"),
    ],
)
def test_pair_score_truth_table(
    resolved_reject: bool,
    unresolved_reject: bool,
    score: float,
    decision: str,
) -> None:
    adapter = PairedRepoPlaybookGEPAAdapter(
        _Checker(resolved_reject, unresolved_reject),
        proposer=object(),
    )
    candidate = {
        "rules": RejectPlaybook((PlaybookBullet("plan-00001", "Concern"),)).serialize()
    }
    result = adapter.evaluate([_case()], candidate, capture_traces=True)
    assert result.scores == [score]
    assert result.outputs[0]["pair_decision"] == decision
    assert result.trajectories[0]["resolved_side"]["plan"] == "# Plan\nGood"


def test_pair_invalid_candidate_scores_minus_100() -> None:
    adapter = PairedRepoPlaybookGEPAAdapter(
        _Checker(False, True),
        proposer=object(),
        token_counter=lambda text: len(text.split()),
        maximum_bullet_tokens=1,
    )
    candidate = {
        "rules": RejectPlaybook(
            (PlaybookBullet("plan-00001", "This concern is too long"),)
        ).serialize()
    }
    result = adapter.evaluate([_case()], candidate)
    assert result.scores == [-100.0]
    assert result.outputs[0]["pair_decision"] == "INVALID"


@pytest.mark.parametrize("levels", [False, True])
def test_pair_checker_calls_are_label_and_pair_blind(levels) -> None:
    class Executor:
        def __init__(self) -> None:
            self.role = None
            self.items = None

        def run_wave(self, role, items):
            self.role = role
            self.items = items
            return [{"agent_output": _result(False), "trajectory": []} for _ in items]

    case = _case()
    repository = {
        "repo": case.repository.repo,
        "base_commit": case.repository.base_commit,
        "instance_id": case.task_id,
    }
    image_ref = derive_image_name(repository)
    records = {
        image_ref: {
            "instance_id": case.task_id,
            "status": "audited",
            "base_commit_verified": True,
            "expected_base_commit": case.repository.base_commit,
            "sif_path": "/tmp/image.sif",
            "sif_sha256": "a" * 64,
            "sif_bytes": 1,
        }
    }
    executor = Executor()
    checker = HPCPairedRepoPlaybookChecker(executor, image_records=records, levels=levels)
    playbook = RejectPlaybook((PlaybookBullet("plan-00001", "Concern"),))
    checker.evaluate_batch([case], playbook)
    assert executor.role == "paired_repo_checker"
    assert len(executor.items) == 2
    assert [item["prompt_values"]["plan"] for item in executor.items] == [
        "# Plan\nGood",
        "# Plan\nBad",
    ]
    for item in executor.items:
        assert item.get("output_contract") == ("levels_v1" if levels else None)
        visible = json.dumps(item["prompt_values"], sort_keys=True)
        assert case.instance_id not in visible
        assert "resolved" not in visible.casefold()
        assert "unresolved" not in visible.casefold()
        assert "outcome" not in visible.casefold()


@pytest.mark.parametrize("r,u,score", [(0, 2, 1), (1, 2, 1), (2, 1, -1),
                                       (0, 1, 0), (1, 1, 0), (2, 2, 0)])
def test_level_pair_score_and_reflection_trace(r, u, score):
    def result(level):
        value = _result(level > 0)
        value["rule_results"][0].pop("triggered")
        value["rule_results"][0]["level"] = level
        return value

    class Checker:
        def evaluate_batch(self, batch, playbook):
            return [((result(r), []), (result(u), [])) for _ in batch]

    playbook = RejectPlaybook((PlaybookBullet("plan-00001", "Concern", category="Scope"),))
    adapter = PairedRepoPlaybookGEPAAdapter(Checker(), proposer=object(), levels=True)
    evaluated = adapter.evaluate([_case()], {"rules": playbook.serialize()}, capture_traces=True)
    assert evaluated.scores == [score]
    sides = evaluated.outputs[0]
    assert sides["resolved_side"]["rule_results"][0]["level"] == r
    assert sides["unresolved_side"]["rule_results"][0]["level"] == u
    # The normal evidence trace retains warnings, even though they do not gate.
    assert '"level": ' + str(r) in json.dumps(evaluated.trajectories[0])


def _row(pair_id: str, task_id: str, split: str) -> dict:
    def side(name: str, plan: str) -> dict:
        return {
            "observation_id": name,
            "plan": plan,
            "plan_sha256": hashlib.sha256(plan.encode()).hexdigest(),
            "historical_evidence": {},
        }

    return {
        "schema_version": 1,
        "pair_id": pair_id,
        "task_id": task_id,
        "split": split,
        "issue_description": "Issue",
        "repository": {
            "repo": "org/repo",
            "base_commit": "abc",
            "instance_id": task_id,
        },
        "resolved_observation": side(pair_id + "-r", "# Plan\nR"),
        "unresolved_observation": side(pair_id + "-u", "# Plan\nU"),
    }


def test_pair_snapshot_rejects_task_leakage(tmp_path: Path) -> None:
    train = _row("pair-train", "same-task", "train")
    validation = _row("pair-validation", "same-task", "validation")
    (tmp_path / "train.jsonl").write_text(json.dumps(train) + "\n")
    (tmp_path / "validation.jsonl").write_text(json.dumps(validation) + "\n")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "data_unit": "within_task_plan_pair",
                "train_pairs": 1,
                "validation_pairs": 1,
            }
        )
    )
    with pytest.raises(ValueError, match="task IDs overlap"):
        load_paired_snapshot(tmp_path)


def test_empty_pair_reflection_is_valid() -> None:
    playbook = RejectPlaybook((PlaybookBullet("plan-00001", "Concern"),))
    value = {
        "instance_id": "pair-1",
        "pair_analysis": None,
        "reusable_concerns": [],
        "uncertainty": None,
        "bullet_tags": [
            {
                "id": "plan-00001",
                "tag": "neutral",
                "attribution": None,
                "confidence": "low",
            }
        ],
    }
    assert (
        validate_paired_reflector_review(
            value, instance_id="pair-1", playbook=playbook
        )["pair_analysis"]
        is None
    )


def test_paired_prompt_has_no_level_contract() -> None:
    path = Path(
        "configs/prompts/offline_gepa_paired_repo_concern_playbook_v1_20260919.yaml"
    )
    prompts = yaml.safe_load(path.read_text())
    for prompt_name in ("checker_system", "reflector_system"):
        text = prompts[prompt_name].casefold()
        assert '"level"' not in text
        assert "level 0" not in text
        assert "level 1" not in text
        assert "level 2" not in text
    contract = yaml.safe_load(
        Path(
            "configs/prompts/offline_gepa_paired_repo_checker_contract_v1_20260919.yaml"
        ).read_text()
    )
    assert "`level`" not in contract["checker_contract_appendix"]


def test_frozen_pair_snapshot_is_task_grouped_and_hash_bound() -> None:
    root = Path(
        "configs/frozen_swe_verified_plan_pairs/20260919_safe_pce_within_task_pairs_v1"
    )
    manifest = json.loads((root / "manifest.json").read_text())
    train, validation = load_paired_snapshot(root)
    assert (len(train), len(validation)) == (144, 36)
    assert (
        {case.task_id for case in train} & {case.task_id for case in validation}
    ) == set()
    assert manifest["train_tasks"] == 46
    assert manifest["validation_tasks"] == 11
    for name, expected in manifest["artifacts"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    assert manifest["source_authorities"]["observation_index"].startswith(
        "/mnt/scratch/users/twang/vibe-coding-planning/run_state/authorities/"
    )
    cleaning_record = Path(manifest["source_authorities"]["cleaning_record"])
    assert cleaning_record.name == "20260919_pair_cleaning_record_v1.md"
    assert (
        hashlib.sha256(cleaning_record.read_bytes()).hexdigest()
        == manifest["source_authorities"]["cleaning_record_sha256"]
    )


def test_paired_smoke_and_formal_configs_bind_new_contract() -> None:
    smoke_path = Path(
        "configs/gepa_verified_paired_repo_concern_playbook_smoke4_v1_20260919.yaml"
    )
    formal_path = Path(
        "configs/gepa_verified_paired_repo_concern_playbook_formal24_8it_v1_20260919.yaml"
    )
    for path in (smoke_path, formal_path):
        raw = yaml.safe_load(path.read_text())
        _validate_frozen_inputs(path, raw)
        assert raw["mode"] == "offline_paired_repo_concern_playbook"
        assert raw["scoring"] == {
            "correct_order": 1,
            "inverted": -1,
            "tied": 0,
            "invalid": -100,
        }
        assert "paired_repo_checker_contract" in raw["inputs"]["repo_checker_contract"]
    formal = yaml.safe_load(formal_path.read_text())
    assert formal["search"]["reflection_minibatch_size"] == 24
    assert formal["search"]["max_iterations"] == 8
    assert formal["reflection"] == {"rounds": 1}


def test_paired_config_selects_paired_adapter_and_data_unit(monkeypatch) -> None:
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return "paired"

    class Agents:
        batch_checker = object()
        reflector_call = staticmethod(lambda _record, _prior: {})
        curator = staticmethod(lambda _playbook, _reviews, _records: {})
        refiner = None

    monkeypatch.setattr(playbook_cli, "run_playbook_search", fake_run)
    result = playbook_cli.run_from_config(
        "configs/gepa_verified_paired_repo_concern_playbook_smoke4_v1_20260919.yaml",
        agents=Agents(),
    )
    assert result == "paired"
    assert isinstance(captured["adapter"], PairedRepoPlaybookGEPAAdapter)
    assert captured["data_unit"] == "within_task_plan_pair"
    assert captured["reflection_minibatch_size"] == 2
