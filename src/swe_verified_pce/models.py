"""Frozen inputs and raw outcomes for SWE-Verified PCE."""

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
    oci_digest: str | None = None


@dataclass(frozen=True)
class SWEVerifiedPCECase:
    instance_id: str
    row_sha256: str
    issue_description: str
    repo: str
    base_commit: str
    version: str
    difficulty: str
    environment_setup_commit: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    gold_patch: str
    image: FrozenImage
    source_row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fail_to_pass"] = list(self.fail_to_pass)
        value["pass_to_pass"] = list(self.pass_to_pass)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SWEVerifiedPCECase":
        image = dict(value["image"])
        return cls(
            instance_id=str(value["instance_id"]),
            row_sha256=str(value["row_sha256"]),
            issue_description=str(value["issue_description"]),
            repo=str(value["repo"]),
            base_commit=str(value["base_commit"]),
            version=str(value.get("version", "")),
            difficulty=str(value.get("difficulty", "")),
            environment_setup_commit=str(value.get("environment_setup_commit", "")),
            test_patch=str(value.get("test_patch", "")),
            fail_to_pass=tuple(str(item) for item in value.get("fail_to_pass", [])),
            pass_to_pass=tuple(str(item) for item in value.get("pass_to_pass", [])),
            gold_patch=str(value.get("gold_patch", "")),
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
            source_row=dict(value.get("source_row", {})),
        )

    def agent_projection(self) -> dict[str, Any]:
        """Return the only benchmark fields that may reach an Agent phase."""
        return {
            "instance_id": self.instance_id,
            "issue_description": self.issue_description,
            "repository": {
                "repo": self.repo,
                "base_commit": self.base_commit,
                "instance_id": self.instance_id,
                "dataset_type": "swe_verified",
                "image_name": self.image.requested_ref,
            },
        }

    def evaluator_input(self) -> dict[str, Any]:
        """Return the official SWE-bench row only to the evaluator phase."""
        value = dict(self.source_row)
        value.update(
            instance_id=self.instance_id,
            repo=self.repo,
            base_commit=self.base_commit,
            problem_statement=self.issue_description,
            patch=self.gold_patch,
            test_patch=self.test_patch,
            FAIL_TO_PASS=list(self.fail_to_pass),
            PASS_TO_PASS=list(self.pass_to_pass),
            version=self.version,
            environment_setup_commit=self.environment_setup_commit,
            difficulty=self.difficulty,
        )
        return value
