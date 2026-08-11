"""Frozen inputs and raw outcomes for PolyBench PCE."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FrozenImage:
    requested_ref: str
    sif_path: str
    sif_sha256: str
    sif_bytes: int
    provenance_strength: str
    oci_digest: str | None


@dataclass(frozen=True)
class PolyBenchPCECase:
    instance_id: str
    row_sha256: str
    issue_description: str
    repo: str
    base_commit: str
    language: str
    task_category: str
    test_patch: str
    f2p: tuple[str, ...]
    p2p: tuple[str, ...]
    test_command: str
    image: FrozenImage
    source_row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["f2p"] = list(self.f2p)
        value["p2p"] = list(self.p2p)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PolyBenchPCECase":
        image = dict(value["image"])
        return cls(
            instance_id=str(value["instance_id"]),
            row_sha256=str(value["row_sha256"]),
            issue_description=str(value["issue_description"]),
            repo=str(value["repo"]),
            base_commit=str(value["base_commit"]),
            language=str(value["language"]),
            task_category=str(value.get("task_category", "")),
            test_patch=str(value["test_patch"]),
            f2p=tuple(str(item) for item in value.get("f2p", [])),
            p2p=tuple(str(item) for item in value.get("p2p", [])),
            test_command=str(value["test_command"]),
            image=FrozenImage(
                requested_ref=str(image["requested_ref"]),
                sif_path=str(image["sif_path"]),
                sif_sha256=str(image["sif_sha256"]),
                sif_bytes=int(image["sif_bytes"]),
                provenance_strength=str(image.get("provenance_strength", "")),
                oci_digest=(
                    str(image["oci_digest"]) if image.get("oci_digest") else None
                ),
            ),
            source_row=dict(value.get("source_row", {})),
        )

    def plan_input(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "issue_description": self.issue_description,
            "repository": {
                "repo": self.repo,
                "base_commit": self.base_commit,
                "instance_id": self.instance_id,
            },
        }

    def evaluator_input(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "test_patch": self.test_patch,
            "f2p": list(self.f2p),
            "p2p": list(self.p2p),
            "test_command": self.test_command,
        }
