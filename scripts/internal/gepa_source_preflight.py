"""Validate the pinned GEPA source before transport; never fetch or repair it."""
from pathlib import Path
import subprocess
import sys


REQUIRED_FILES = (
    "pyproject.toml", "src/gepa/api.py", "src/gepa/core/engine.py",
    "src/gepa/core/state.py", "src/gepa/strategies/batch_sampler.py",
    "src/gepa/strategies/candidate_selector.py",
)


def validate_gepa_source(root: Path) -> str | None:
    root = Path(root).resolve()
    source = root / "third_party/gepa"
    for name in REQUIRED_FILES:
        if not (source / name).is_file():
            return (f"vendored GEPA is incomplete: third_party/gepa/{name}; "
                    "initialize the pinned submodule: git submodule update --init -- third_party/gepa")
    # Exports lack Git metadata. Require files there; Git worktrees additionally
    # require the actual checkout to match the parent's frozen gitlink.
    if not (root / ".git").exists():
        return None
    def git(directory, *args):
        return subprocess.run(["git", "-C", str(directory), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    try:
        entry = git(root, "ls-tree", "HEAD", "--", "third_party/gepa").split()
        if len(entry) < 3 or entry[0] != "160000":
            return "parent commit does not pin third_party/gepa as a submodule"
        if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
            return "vendored GEPA files exist but the submodule is not initialized"
        actual = git(source, "rev-parse", "HEAD")
        if actual != entry[2]:
            return f"vendored GEPA revision mismatch: expected={entry[2]} actual={actual}"
        if git(source, "status", "--porcelain", "--untracked-files=no"):
            return "vendored GEPA has tracked modifications; use the pinned clean checkout"
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"cannot verify vendored GEPA identity: {type(exc).__name__}"
    return None


if __name__ == "__main__":
    error = validate_gepa_source(Path(sys.argv[1]))
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
