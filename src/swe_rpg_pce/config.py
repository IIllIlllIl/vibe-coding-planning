"""RPG identity over the established Safe PCE configuration parser."""
from dataclasses import dataclass
from src.swe_verified_pce.config import SWEVerifiedPCEConfig, load_repository_pce_config


@dataclass(frozen=True)
class SWERPGPCEConfig(SWEVerifiedPCEConfig):
    mode = "swe_rpg_pce"
    dataset = "Xin-Zhou-smu/SWE-RPG-Bench"
    dataset_type = "swe_rpg"


def load_swe_rpg_pce_config(path, *, require_api_keys=True):
    config = load_repository_pce_config(path, require_api_keys=require_api_keys,
        expected_mode=SWERPGPCEConfig.mode, config_type=SWERPGPCEConfig)
    if config.plan_submission_protocol != "direct_human_markdown_v5":
        raise ValueError("RPG requires the current Safe PCE Plan protocol")
    return config
