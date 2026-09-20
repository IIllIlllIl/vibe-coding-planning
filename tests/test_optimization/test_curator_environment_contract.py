import json
from pathlib import Path

import pytest
import yaml

from src.optimization import playbook_runtime, playbook_worker


def test_missing_evidence_cache_has_actionable_error():
    with pytest.raises(ValueError, match="container.sif_cache_dir"):
        playbook_runtime.evidence_agent_config({"reflection": {"rounds": 1}})


def test_legacy_evidence_overrides_are_preserved():
    result = playbook_runtime.evidence_agent_config({
        "container": {"sif_cache_dir": "/shared"},
        "reflection": {"evidence_sif_cache_dir": "/legacy",
                       "evidence_image": "custom:image", "command_timeout_seconds": 42},
    })
    assert result == {"evidence_sif_cache_dir": "/legacy",
                      "evidence_image": "custom:image", "command_timeout_seconds": 42}


@pytest.mark.parametrize("name", [
    "gepa_verified_paired_repo_concern_playbook_formal24_8it_v1_20260919.yaml",
    "gepa_verified_paired_repo_concern_playbook_formal24_8it_v2_20260920.yaml",
    "gepa_verified_paired_repo_concern_playbook_formal24_8it_v3_20260920.yaml",
])
def test_formal_config_reaches_curator_environment_and_completion(name, tmp_path, monkeypatch):
    config_path = Path("configs") / name
    config = yaml.safe_load(config_path.read_text())
    seed = Path(config["inputs"]["initial_playbook"]).read_text()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    manifest = tmp_path / "task.json"
    manifest.write_text(json.dumps({
        "role": "curator", "fingerprint": "test", "task_index": 0,
        "validation_playbook": seed, "evidence_dir": str(evidence),
        "prompt_values": {"counted_internal_playbook": seed, "case_count": 24},
    }))
    captured = {}

    class Environment:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, command, **kwargs):
            return {"returncode": 0, "stdout": '{"reasoning":"none","operations":[]}', "stderr": ""}

        def cleanup(self):
            captured["cleaned"] = True

    class Agent:
        messages = []

        def run(self, **kwargs):
            return "Submitted", "done"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.setattr(playbook_runtime, "import_minisweagent", lambda: (object, object, None))
    monkeypatch.setattr(playbook_runtime, "build_model", lambda *a: object())
    monkeypatch.setattr(playbook_runtime, "DockerCapacityWindow", lambda **k: object())
    monkeypatch.setattr(playbook_runtime, "ApptainerEnvironment", Environment)
    monkeypatch.setattr(playbook_runtime, "build_default_agent", lambda *a, **k: Agent())
    monkeypatch.setattr(playbook_worker.litellm, "token_counter", lambda **k: 4)
    output = tmp_path / "output.json"
    assert playbook_worker.run_task(config_path=config_path, manifest_path=manifest,
                                   output_path=output, attempt_dir=tmp_path / "attempt") == 0
    assert json.loads(output.read_text())["status"] == "completed"
    assert (tmp_path / "attempt/agent_completion.json").is_file()
    assert captured["sif_cache_dir"] == Path("/scratch/users/testuser/vibe-coding-planning/shared/sif-cache")
    assert captured["image"] == "python:3.12-slim"
    assert captured["timeout"] == 1800
    assert captured["network_disabled"] is True
    assert captured["run_args"][-1] == f"{evidence.resolve()}:/evidence:ro"
    assert captured["cleaned"]
