"""Private result-directory metadata, separate from scientific payload bytes."""

from __future__ import annotations

from pathlib import Path


PRIVATE_ARTIFACT_MARKER = ".vibe-private-artifacts"
_PRIVATE_DIRS = {"logs", "trajectories", "patches", "opencode_logs",
                 ".tmp_hpc_smoke", ".ulhpc_submit"}
_PRIVATE_LOCAL_DIRS = {"hpc-supervisor", "operations", "recovery-review"}


def conventional_private_root(path: Path) -> Path | None:
    """Recognize established output roots, not arbitrary JSON or source files.

    Keep the reviewed output/README.md, catalog.json and archive/README.md
    outside the private directories. A caller with a custom output location
    should mark that exact result directory explicitly instead.
    """
    path = path.absolute()
    parts = path.parts
    for index, part in enumerate(parts[:-1]):
        if part in _PRIVATE_DIRS:
            return Path(*parts[:index + 1])
        if part == "output" and index + 2 < len(parts):
            if parts[index + 1] == "archive":
                if index + 3 < len(parts):
                    return Path(*parts[:index + 3])
                continue
            return Path(*parts[:index + 2])
        if (part == ".local" and index + 2 < len(parts)
                and parts[index + 1] in _PRIVATE_LOCAL_DIRS):
            return Path(*parts[:index + 2])
        if (part == "run_state" and index + 2 < len(parts)
                and parts[index + 1] == "operations"):
            return Path(*parts[:index + 2])
    return None


def mark_private_artifact_dir(directory: Path) -> None:
    """Mark one private directory; never read or rewrite any result file."""
    directory = directory.absolute()
    if directory.is_symlink():
        raise ValueError("Private artifact root must not be a symbolic link")
    directory = directory.resolve()
    if directory in {Path(directory.anchor), Path.home().resolve()} or (directory / ".git").exists():
        raise ValueError("Select a specific private artifact directory, not a home or checkout root")
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in ((PRIVATE_ARTIFACT_MARKER, "private_experiment_artifacts_v1\n"),
                          (".gitignore", "# Private experiment artifacts; never commit.\n*\n")):
        try:
            with (directory / name).open("x", encoding="utf-8") as handle:
                handle.write(content)
        except FileExistsError:
            pass  # Existing metadata and scientific payloads remain untouched.


def mark_artifact_path(path: Path) -> None:
    root = conventional_private_root(path)
    if root is not None:
        mark_private_artifact_dir(root)
