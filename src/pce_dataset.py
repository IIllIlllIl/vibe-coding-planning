"""Dataset bindings over the proven repository PCE lifecycle, not a new workflow.

PolyBench keeps its existing distinct execution-unit interface. Repository PCE
adapters differ only in loading/serialization and the Runner's evaluator binding.
Imports are lazy so config/preparation does not initialize Agents or GEPA.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RepositoryPCEDataset:
    mode: str
    load_config: Callable
    load_cases: Callable
    case_from_dict: Callable
    runner_class: type


def frozen_source_file(root, relative, expected_hash):
    """Verify a source-relative frozen payload without interpreting its content."""
    from src.swe_verified_pce.dataset import file_sha256
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe frozen source reference")
    path = Path(root) / relative
    if not path.resolve().is_relative_to(Path(root).resolve()) or file_sha256(path) != expected_hash:
        raise ValueError(f"frozen source hash mismatch: {relative}")
    return path


def repository_pce_dataset(mode: str) -> RepositoryPCEDataset:
    if mode == "swe_verified_pce":
        from src.swe_verified_pce.config import load_swe_verified_pce_config
        from src.swe_verified_pce.dataset import load_swe_verified_pce_cases
        from src.swe_verified_pce.models import SWEVerifiedPCECase
        from src.swe_verified_pce.runner import SWEVerifiedPCERunner
        return RepositoryPCEDataset(mode, load_swe_verified_pce_config,
            load_swe_verified_pce_cases, SWEVerifiedPCECase.from_dict, SWEVerifiedPCERunner)
    if mode == "swe_rpg_pce":
        from src.swe_rpg_pce.config import load_swe_rpg_pce_config
        from src.swe_rpg_pce.dataset import load_swe_rpg_pce_cases
        from src.swe_rpg_pce.models import SWERPGPCECase
        from src.swe_rpg_pce.runner import SWERPGPCERunner
        return RepositoryPCEDataset(mode, load_swe_rpg_pce_config,
            load_swe_rpg_pce_cases, SWERPGPCECase.from_dict, SWERPGPCERunner)
    raise ValueError(f"unsupported repository PCE dataset: {mode}")
