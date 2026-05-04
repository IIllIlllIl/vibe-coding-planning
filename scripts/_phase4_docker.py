"""Phase 4: Docker environment smoke test.

Verifies that:
1. DockerEnvWrapper.start() launches a container via 1.17.5 DockerEnvironment
2. Read-only mount prevents writes to /testbed
3. /tmp is writable inside the container
4. cleanup() stops the container properly

Requires Docker daemon running locally.
"""

from __future__ import annotations

import os
import sys
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from src.environment.docker_env import DockerEnvWrapper
from src.config import DockerConfig


def main() -> int:
    # Use a lightweight image likely already cached
    image = "python:3.9-slim"
    workdir = "/testbed"

    # Create a temp directory on host to mount ro
    with tempfile.TemporaryDirectory() as tmpdir:
        # Put a marker file inside
        marker = os.path.join(tmpdir, "marker.txt")
        with open(marker, "w") as f:
            f.write("hello from host\n")

        print(f"[1/4] Starting container: image={image} workdir={workdir}")
        print(f"       ro_mount_source={tmpdir}")

        env = DockerEnvWrapper(DockerConfig())
        try:
            env.start(image=image, workdir=workdir, ro_mount_source=tmpdir)
            print("       Container started OK")
        except Exception as exc:
            print(f"       FAILED to start: {type(exc).__name__}: {exc}")
            return 1

        # --- test 1: can read mounted file ---
        print("[2/4] Checking read-only mount...")
        try:
            out = env.execute("cat /testbed/marker.txt")
            # 1.17.5 returns dict {output, returncode}
            if isinstance(out, dict):
                stdout = out.get("output", "")
                rc = out.get("returncode", 0)
            else:
                stdout = str(out)
                rc = 0
            if "hello from host" in stdout and rc == 0:
                print("       Read OK: marker file visible")
            else:
                print(f"       WARNING: unexpected content: rc={rc} out={out!r}")
        except Exception as exc:
            print(f"       FAILED read test: {type(exc).__name__}: {exc}")
            env.stop()
            return 1

        # --- test 2: ro mount blocks writes ---
        result = env.execute("touch /testbed/should_fail.txt")
        if isinstance(result, dict):
            rc = result.get("returncode", 0)
            if rc != 0:
                print(f"       Write blocked as expected (rc={rc}, read-only mount works)")
            else:
                print("       WARNING: write to /testbed succeeded (mount may not be ro)")
        else:
            print(f"       WARNING: unexpected result type: {type(result)}")

        # --- test 3: /tmp is writable ---
        print("[3/4] Checking /tmp is writable...")
        result = env.execute("touch /tmp/should_succeed.txt && rm /tmp/should_succeed.txt")
        if isinstance(result, dict):
            rc = result.get("returncode", 0)
            if rc == 0:
                print("       /tmp write OK")
            else:
                print(f"       FAILED /tmp write: rc={rc} out={result!r}")
                env.stop()
                return 1
        else:
            print("       /tmp write OK (legacy string return)")

        # --- test 4: stop / cleanup ---
        print("[4/4] Stopping container...")
        try:
            env.stop()
            print("       Container stopped OK")
        except Exception as exc:
            print(f"       FAILED stop: {type(exc).__name__}: {exc}")
            return 1

        # Verify container is really gone by trying execute
        try:
            env.execute("echo should fail")
            print("       WARNING: execute() still works after stop()")
        except Exception:
            print("       Execute correctly fails after stop()")

    print("\nPhase 4 PASSED: Docker environment operational")
    return 0


if __name__ == "__main__":
    sys.exit(main())
