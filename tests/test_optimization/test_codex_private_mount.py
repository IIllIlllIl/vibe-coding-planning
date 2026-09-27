from types import SimpleNamespace

import pytest

from src.optimization import codex_private_mount as launcher


def test_mount_preparation_keeps_identity_and_only_changes_new_namespace(monkeypatch):
    events = []
    monkeypatch.setattr(launcher.os, "getuid", lambda: 1234)
    monkeypatch.setattr(launcher.os, "getgid", lambda: 5678)
    monkeypatch.setattr(launcher.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(
        unshare=lambda flag: events.append(("unshare", flag)) or 0,
        mount=lambda source, target, fs, flags, data:
            events.append(("mount", target, flags.value)) or 0,
    ))
    monkeypatch.setattr(launcher, "Path", lambda path: SimpleNamespace(
        write_text=lambda value: events.append((path, value))))
    monkeypatch.setattr(launcher.os, "execv", lambda executable, command:
        events.append(("exec", executable, command)))
    launcher.launch(["/bin/true"])
    assert events == [
        ("unshare", 0x10000000),
        ("/proc/self/uid_map", "1234 1234 1\n"),
        ("/proc/self/setgroups", "deny\n"),
        ("/proc/self/gid_map", "5678 5678 1\n"),
        ("unshare", 0x00020000),
        ("mount", b"/", (1 << 18) | (1 << 14)),
        ("exec", "/bin/true", ["/bin/true"]),
    ]


def test_namespace_failure_never_executes_command(monkeypatch):
    monkeypatch.setattr(launcher.os, "getuid", lambda: 1234)
    monkeypatch.setattr(launcher.os, "getgid", lambda: 1234)
    monkeypatch.setattr(launcher.ctypes, "CDLL", lambda *a, **k:
        SimpleNamespace(unshare=lambda flag: -1))
    monkeypatch.setattr(launcher.ctypes, "get_errno", lambda: 1)
    monkeypatch.setattr(launcher.os, "execv", lambda *a: pytest.fail("must not exec"))
    with pytest.raises(OSError, match="unshare user namespace"):
        launcher.launch(["/bin/true"])


@pytest.mark.parametrize("command", [[], ["codex"]])
def test_launcher_rejects_non_absolute_command(command):
    with pytest.raises(ValueError, match="absolute"):
        launcher.launch(command)
