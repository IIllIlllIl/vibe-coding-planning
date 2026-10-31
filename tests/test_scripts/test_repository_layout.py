"""Small regressions for the maintained navigation surface, not experiment state."""
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_root_launch_identities_are_declared_in_the_configuration_index():
    index = (ROOT / "configs/README.md").read_text()
    for path in (ROOT / "configs").glob("*supervisor*.yaml"):
        assert path.name in index, f"Undeclared root launch identity: {path.name}"


def test_date_specific_scripts_are_not_in_the_maintained_tool_directory():
    assert not [path.name for path in (ROOT / "scripts/tools").iterdir()
                if path.is_file() and re.search(r"20\d{6}", path.name)]


def test_current_index_file_links_resolve():
    for relative in ("README.md", "configs/README.md", "docs/README.md", "scripts/README.md"):
        path = ROOT / relative
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if target.startswith(("http:", "https:", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            assert (path.parent / target).exists(), f"{relative}: missing {target}"


def test_private_local_maintenance_evidence_is_ignored():
    result = subprocess.run(
        ["git", "check-ignore", ".local/operations/layout-check/report.json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0
