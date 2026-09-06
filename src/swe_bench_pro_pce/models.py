"""Frozen Pro inputs with strict Agent/evaluator projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.swe_verified_pce.models import FrozenImage


@dataclass(frozen=True)
class SWEBenchProPCECase:
    instance_id: str
    row_sha256: str
    issue_description: str
    repo: str
    base_commit: str
    dockerhub_tag: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    before_repo_set_cmd: str
    selected_test_files_to_run: tuple[str, ...]
    gold_patch: str
    test_patch: str
    image: FrozenImage
    evaluator_assets: dict[str, str]
    source_row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fail_to_pass"] = list(self.fail_to_pass)
        value["pass_to_pass"] = list(self.pass_to_pass)
        value["selected_test_files_to_run"] = list(self.selected_test_files_to_run)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SWEBenchProPCECase":
        image = dict(value["image"])
        return cls(
            instance_id=str(value["instance_id"]),
            row_sha256=str(value["row_sha256"]),
            issue_description=str(value["issue_description"]),
            repo=str(value["repo"]),
            base_commit=str(value["base_commit"]),
            dockerhub_tag=str(value["dockerhub_tag"]),
            fail_to_pass=tuple(map(str, value.get("fail_to_pass", []))),
            pass_to_pass=tuple(map(str, value.get("pass_to_pass", []))),
            before_repo_set_cmd=str(value["before_repo_set_cmd"]),
            selected_test_files_to_run=tuple(
                map(str, value.get("selected_test_files_to_run", []))
            ),
            gold_patch=str(value.get("gold_patch", "")),
            test_patch=str(value.get("test_patch", "")),
            image=FrozenImage(
                requested_ref=str(image["requested_ref"]),
                sif_path=str(image["sif_path"]),
                sif_sha256=str(image["sif_sha256"]),
                sif_bytes=int(image["sif_bytes"]),
                provenance_strength=str(image["provenance_strength"]),
                oci_digest=str(image["oci_digest"])
                if image.get("oci_digest")
                else None,
            ),
            evaluator_assets={
                str(key): str(item)
                for key, item in dict(value["evaluator_assets"]).items()
            },
            source_row=dict(value.get("source_row", {})),
        )

    def agent_projection(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "issue_description": self.issue_description,
            "repository": {
                "repo": self.repo,
                "base_commit": self.base_commit,
                "instance_id": self.instance_id,
                "dataset_type": "pro",
                "image_name": self.image.requested_ref,
            },
        }

    def evaluator_input(self) -> dict[str, Any]:
        return dict(self.source_row)
