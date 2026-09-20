import pytest
from pathlib import Path
import yaml

from src.optimization.playbook_cli import _deployment_resources


def test_iris_supervisor_reuses_exact_scientific_config():
    path = Path("configs/gepa_verified_paired_repo_concern_playbook_formal24_8it_v4_iris_supervisor_20260920.yaml")
    args = yaml.safe_load(path.read_text())["arguments"]
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == "4G"
    assert args[args.index("--ulhpc-config") + 1] == "configs/ulhpc_submit.yaml"
    assert args[args.index("--gepa-config") + 1] == "configs/gepa_verified_paired_repo_concern_playbook_formal24_8it_v4_20260920.yaml"


def test_no_override_preserves_frozen_resources(monkeypatch):
    monkeypatch.delenv("VIBE_PLAYBOOK_AGENT_CPUS", raising=False)
    monkeypatch.delenv("VIBE_PLAYBOOK_AGENT_MEM", raising=False)
    assert _deployment_resources({"cpus_per_task": 1, "mem": "1750M"}) == (1, "1750M")


@pytest.mark.parametrize("mem", ["1750M", "4G"])
def test_deployment_changes_only_resource_values(monkeypatch, mem):
    frozen = {"cpus_per_task": 1, "mem": "1750M"}
    monkeypatch.setenv("VIBE_PLAYBOOK_AGENT_CPUS", "1")
    monkeypatch.setenv("VIBE_PLAYBOOK_AGENT_MEM", mem)
    assert _deployment_resources(frozen) == (1, mem)
    assert frozen == {"cpus_per_task": 1, "mem": "1750M"}


@pytest.mark.parametrize("cpus,mem", [("2", "4G"), ("1", "8G"), (None, "4G"), ("1", None)])
def test_incomplete_or_unsupported_override_rejected(monkeypatch, cpus, mem):
    for key, value in [("VIBE_PLAYBOOK_AGENT_CPUS", cpus), ("VIBE_PLAYBOOK_AGENT_MEM", mem)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        _deployment_resources({"cpus_per_task": 1, "mem": "1750M"})
