"""Task-private mount preparation for Codex inside Apptainer.

Standalone standard-library entry point; never changes host mounts.
"""

import ctypes
import os
from pathlib import Path
import sys


def launch(command):
    if not command or not command[0].startswith("/"):
        raise ValueError("Namespace launcher requires an absolute executable path")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise ValueError("Namespace launcher must run as a non-root user")
    libc = ctypes.CDLL(None, use_errno=True)

    def require(result, operation):
        if result != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, operation + ": " + os.strerror(errno))

    require(libc.unshare(0x10000000), "unshare user namespace")
    Path("/proc/self/uid_map").write_text(f"{uid} {uid} 1\n")
    Path("/proc/self/setgroups").write_text("deny\n")
    Path("/proc/self/gid_map").write_text(f"{gid} {gid} 1\n")
    require(libc.unshare(0x00020000), "unshare mount namespace")
    # MS_REC | MS_PRIVATE clears the unbindable flag only in this namespace.
    require(libc.mount(None, b"/", None, ctypes.c_ulong((1 << 18) | (1 << 14)), None),
            "make namespace mounts recursively private")
    # Exec as the retained non-root identity drops namespace capabilities.
    os.execv(command[0], command)


if __name__ == "__main__":
    launch(sys.argv[1:])
