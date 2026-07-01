#!/usr/bin/env python3
"""Pre-pull Apptainer SIF images directly on a ULHPC access node.

This bypasses Slurm for low-concurrency SIF preheating. It intentionally keeps
the login-node workload serial and writes Apptainer cache/tmp data to scratch.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tools.prepare_apptainer_sifs import _collect_images  # noqa: E402
from src.environment.apptainer_env import _image_to_sif_name  # noqa: E402
from src.optimization.config import load_optimization_config  # noqa: E402

DEFAULT_APPTAINER_BIN = (
    "/mnt/aiongpfs/apps/resif/iris-rhel8/2020b/broadwell/software/"
    "Apptainer/1.2.1/bin/apptainer"
)
DEFAULT_APPTAINER_CACHE_DIR = (
    "/scratch/users/twang/vibe-coding-planning/shared/apptainer-cache-login"
)
DEFAULT_APPTAINER_TMP_DIR = (
    "/scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp-login"
)
DEFAULT_ULHPC_CONFIG = REPO_ROOT / "configs" / "ulhpc_submit.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _ssh_config(path: Path) -> tuple[str, str, str]:
    data = _load_yaml(path)
    host = os.environ.get("ULHPC_HOST") or str(data.get("host", "access-iris.uni.lu"))
    port = os.environ.get("ULHPC_PORT") or str(data.get("port", "8022"))
    user = os.environ.get("ULHPC_USER") or str(data.get("user", ""))
    ssh_key = os.environ.get("ULHPC_SSH_KEY") or str(data.get("ssh_key", ""))
    if not user:
        raise SystemExit(
            "cannot determine ULHPC user; set configs/ulhpc_submit.yaml user or ULHPC_USER"
        )
    return f"{user}@{host}", port, ssh_key


def _ssh_command(target: str, port: str, ssh_key: str, remote_command: str) -> list[str]:
    command = ["ssh", "-p", port]
    if ssh_key:
        command.extend(["-i", os.path.expanduser(ssh_key)])
    command.extend([target, remote_command])
    return command


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-pull Apptainer SIFs on a ULHPC access/login node",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, help="GEPA YAML config path")
    parser.add_argument(
        "--ulhpc-config",
        default=str(DEFAULT_ULHPC_CONFIG),
        help="ULHPC submit config used for SSH host/user/port",
    )
    parser.add_argument(
        "--sif-cache-dir",
        help="Remote shared SIF cache directory; defaults to config container.sif_cache_dir",
    )
    parser.add_argument(
        "--apptainer-bin",
        default=DEFAULT_APPTAINER_BIN,
        help="Remote Apptainer binary path",
    )
    parser.add_argument(
        "--apptainer-cache-dir",
        default=DEFAULT_APPTAINER_CACHE_DIR,
        help="Remote APPTAINER_CACHEDIR on scratch",
    )
    parser.add_argument(
        "--apptainer-tmp-dir",
        default=DEFAULT_APPTAINER_TMP_DIR,
        help="Remote APPTAINER_TMPDIR on scratch",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=21600,
        help="Timeout per missing image pull in seconds (default: 21600)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=1,
        help="Attempts per missing SIF image before marking it failed (default: 1)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=int,
        default=0,
        help="Seconds to wait between failed pull attempts (default: 0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit image list length after optional missing-only filtering; 0 means no limit",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Filter the image list locally by checking remote SIF existence first",
    )
    parser.add_argument(
        "--failed-output",
        help="Remote failed image list path (default: <sif-cache-dir>/login_preheat_failed_images.txt)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected images and remote command metadata without pulling",
    )
    return parser.parse_args()


def _remote_script() -> str:
    return r'''
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def image_to_sif_name(image: str) -> str:
    safe = image.replace("/", "_").replace(":", "_")
    safe = "".join(c for c in safe if c.isalnum() or c in "._-")
    return f"{safe}.sif"


payload = json.loads(sys.stdin.read())
apptainer_bin = payload["apptainer_bin"]
sif_cache_dir = Path(payload["sif_cache_dir"])
failed_output = Path(payload["failed_output"])
timeout = payload["timeout"]
max_attempts = payload["max_attempts"]
retry_backoff = payload["retry_backoff"]
images = payload["images"]

os.environ["APPTAINER_CACHEDIR"] = payload["apptainer_cache_dir"]
os.environ["APPTAINER_TMPDIR"] = payload["apptainer_tmp_dir"]
Path(os.environ["APPTAINER_CACHEDIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["APPTAINER_TMPDIR"]).mkdir(parents=True, exist_ok=True)
sif_cache_dir.mkdir(parents=True, exist_ok=True)

print(
    json.dumps(
        {
            "event": "login_preheat_started",
            "images": len(images),
            "sif_cache_dir": str(sif_cache_dir),
            "apptainer_cache_dir": os.environ["APPTAINER_CACHEDIR"],
            "apptainer_tmp_dir": os.environ["APPTAINER_TMPDIR"],
        },
        sort_keys=True,
    ),
    flush=True,
)

cached = 0
pulled = 0
failures = []
for image in images:
    sif = sif_cache_dir / image_to_sif_name(image)
    if sif.exists():
        cached += 1
        print(json.dumps({"event": "cached", "image": image, "sif": sif.name}), flush=True)
        continue

    last_error = ""
    ok = False
    for attempt in range(1, max_attempts + 1):
        temporary = sif.with_name(f"{sif.name}.tmp.login.{os.getpid()}")
        if temporary.exists():
            temporary.unlink()
        print(
            json.dumps(
                {
                    "event": "pulling",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "image": image,
                    "sif": sif.name,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        try:
            result = subprocess.run(
                [
                    apptainer_bin,
                    "pull",
                    "--force",
                    str(temporary),
                    f"docker://{image}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"timed out after {exc.timeout}s"
        except FileNotFoundError as exc:
            last_error = f"Apptainer not found: {exc}"
        else:
            if result.returncode == 0 and temporary.exists():
                temporary.replace(sif)
                pulled += 1
                ok = True
                size = sif.stat().st_size
                print(
                    json.dumps(
                        {"event": "ok", "image": image, "sif": sif.name, "bytes": size},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                break
            last_error = (result.stderr or result.stdout or "unknown pull failure").strip()[-2000:]
        finally:
            if temporary.exists():
                temporary.unlink()

        print(
            json.dumps(
                {
                    "event": "retryable_failure",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "image": image,
                    "error": last_error,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if attempt < max_attempts and retry_backoff > 0:
            time.sleep(retry_backoff)

    if not ok:
        failures.append((image, last_error))
        print(
            json.dumps({"event": "failed", "image": image, "error": last_error}, sort_keys=True),
            flush=True,
        )

if failures:
    failed_output.parent.mkdir(parents=True, exist_ok=True)
    failed_output.write_text(
        "".join(f"{image}\t{error}\n" for image, error in failures),
        encoding="utf-8",
    )
elif failed_output.exists():
    failed_output.unlink()

summary = {
    "event": "summary",
    "cached": cached,
    "pulled": pulled,
    "failed": len(failures),
    "total": len(images),
}
print(json.dumps(summary, sort_keys=True), flush=True)
raise SystemExit(1 if failures else 0)
'''


def _remote_existing_sifs(
    target: str,
    port: str,
    ssh_key: str,
    sif_cache_dir: str,
) -> set[str]:
    script = (
        "python3 -c "
        + shlex.quote(
            "import json, sys; from pathlib import Path; "
            "cache=Path(sys.argv[1]); "
            "print(json.dumps(sorted(p.name for p in cache.glob('*.sif')) if cache.is_dir() else []))"
        )
        + " "
        + shlex.quote(sif_cache_dir)
    )
    result = subprocess.run(
        _ssh_command(target, port, ssh_key, script),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to inspect remote SIF cache: {result.stderr}")
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def main() -> int:
    args = _parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive for login preheat")
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be >= 1")
    if args.retry_backoff < 0:
        raise SystemExit("--retry-backoff must be >= 0")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0")

    config = load_optimization_config(args.config, require_api_keys=False)
    if config.container.runtime != "apptainer":
        raise SystemExit(
            f"config container.runtime is {config.container.runtime!r}; expected 'apptainer'"
        )
    sif_cache_dir = str(args.sif_cache_dir or config.container.sif_cache_dir)
    if not sif_cache_dir:
        raise SystemExit("--sif-cache-dir is required when config has no container.sif_cache_dir")
    failed_output = args.failed_output or f"{sif_cache_dir}/login_preheat_failed_images.txt"

    ssh_target, ssh_port, ssh_key = _ssh_config(Path(args.ulhpc_config))
    images = _collect_images(config)
    if args.missing_only:
        existing = _remote_existing_sifs(ssh_target, ssh_port, ssh_key, sif_cache_dir)
        images = [image for image in images if _image_to_sif_name(image) not in existing]
    if args.limit:
        images = images[: args.limit]

    metadata = {
        "ssh_target": ssh_target,
        "image_count": len(images),
        "sif_cache_dir": sif_cache_dir,
        "apptainer_cache_dir": args.apptainer_cache_dir,
        "apptainer_tmp_dir": args.apptainer_tmp_dir,
        "timeout": args.timeout,
        "max_attempts": args.max_attempts,
        "retry_backoff": args.retry_backoff,
        "missing_only": args.missing_only,
        "dry_run": args.dry_run,
    }
    print(json.dumps({"event": "login_preheat_plan", **metadata}, sort_keys=True), flush=True)
    for image in images:
        print(
            json.dumps(
                {"event": "selected_image", "image": image, "sif": _image_to_sif_name(image)}
            ),
            flush=True,
        )

    if args.dry_run:
        return 0

    payload = {
        "images": images,
        "sif_cache_dir": sif_cache_dir,
        "apptainer_bin": args.apptainer_bin,
        "apptainer_cache_dir": args.apptainer_cache_dir,
        "apptainer_tmp_dir": args.apptainer_tmp_dir,
        "timeout": args.timeout,
        "max_attempts": args.max_attempts,
        "retry_backoff": args.retry_backoff,
        "failed_output": failed_output,
    }
    command = "python3 -c " + shlex.quote(_remote_script())
    result = subprocess.run(
        _ssh_command(ssh_target, ssh_port, ssh_key, command),
        input=json.dumps(payload),
        capture_output=False,
        text=True,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
