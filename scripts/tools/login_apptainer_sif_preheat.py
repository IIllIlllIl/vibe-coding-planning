#!/usr/bin/env python3
"""Pre-pull Apptainer SIF images directly on a ULHPC access node.

This bypasses Slurm for low-concurrency SIF preheating. It intentionally keeps
the login-node workload serial and writes Apptainer cache/tmp data to scratch.
"""

from __future__ import annotations

import argparse
import hashlib
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


def _default_hpc_root() -> str:
    user = os.environ.get("ULHPC_USER") or os.environ.get("USER") or "<user>"
    return os.environ.get(
        "VIBE_HPC_ROOT",
        f"/scratch/users/{user}/vibe-coding-planning",
    )


DEFAULT_ULHPC_CONFIG = REPO_ROOT / "configs" / "ulhpc_submit.yaml"


def _normalize_image_ref(image: str) -> str:
    """Normalize the OCI repository component without changing its tag."""
    if not image.startswith("ghcr.io/"):
        return image
    name_and_tag = image[len("ghcr.io/") :]
    if ":" not in name_and_tag:
        return "ghcr.io/" + name_and_tag.lower()
    repository, tag = name_and_tag.rsplit(":", 1)
    return f"ghcr.io/{repository.lower()}:{tag}"


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


def _ssh_command(
    target: str, port: str, ssh_key: str, remote_command: str
) -> list[str]:
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
        help="Remote APPTAINER_CACHEDIR on scratch; defaults from the SSH user",
    )
    parser.add_argument(
        "--apptainer-tmp-dir",
        help="Remote APPTAINER_TMPDIR on scratch; defaults from the SSH user",
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
        "--existing-only",
        action="store_true",
        help="Audit only image refs whose expected SIF already exists remotely",
    )
    parser.add_argument(
        "--remote-images-json",
        help=(
            "Read the image list from a remote JSON list or an object with an "
            "'images' list instead of deriving it from the GEPA dataset"
        ),
    )
    parser.add_argument(
        "--failed-output",
        help="Remote failed image list path (default: <sif-cache-dir>/login_preheat_failed_images.txt)",
    )
    parser.add_argument(
        "--provenance-output",
        help=(
            "Remote image provenance manifest path "
            "(default: <sif-cache-dir>/login_preheat_provenance.json)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected images and remote command metadata without pulling",
    )
    parser.add_argument(
        "--cleanup-tmp",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Remove remote APPTAINER_TMPDIR contents after the batch (default: true)",
    )
    parser.add_argument(
        "--cleanup-apptainer-cache",
        action="store_true",
        help=(
            "Remove remote APPTAINER_CACHEDIR contents after the batch. Final SIF "
            "files in --sif-cache-dir are preserved."
        ),
    )
    return parser.parse_args()


def _remote_script() -> str:
    return r"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def image_to_sif_name(image: str) -> str:
    safe = image.replace("/", "_").replace(":", "_")
    safe = "".join(c for c in safe if c.isalnum() or c in "._-")
    return f"{safe}.sif"


def normalize_image_ref(image: str) -> str:
    if not image.startswith("ghcr.io/"):
        return image
    name_and_tag = image[len("ghcr.io/"):]
    if ":" not in name_and_tag:
        return "ghcr.io/" + name_and_tag.lower()
    repository, tag = name_and_tag.rsplit(":", 1)
    return f"ghcr.io/{repository.lower()}:{tag}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path, previous=None):
    stat = path.stat()
    if (
        previous
        and previous.get("sif_sha256")
        and previous.get("sif_bytes") == stat.st_size
        and previous.get("sif_mtime_ns") == stat.st_mtime_ns
    ):
        return str(previous["sif_sha256"])
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ghcr_digest(image):
    prefix = "ghcr.io/"
    if not image.startswith(prefix) or "@" in image:
        return None, "digest lookup supports tagged ghcr.io references only"
    name_and_tag = image[len(prefix):]
    if ":" not in name_and_tag:
        return None, "GHCR image reference has no explicit tag"
    repository, tag = name_and_tag.rsplit(":", 1)
    try:
        query = urllib.parse.urlencode(
            {"service": "ghcr.io", "scope": f"repository:{repository}:pull"}
        )
        with urllib.request.urlopen(
            "https://ghcr.io/token?" + query, timeout=60
        ) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{repository}/manifests/{tag}",
            method="HEAD",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": (
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            digest = response.headers.get("Docker-Content-Digest")
        if not digest:
            return None, "GHCR response omitted Docker-Content-Digest"
        return digest, None
    except (OSError, KeyError, ValueError, urllib.error.URLError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def inspect_sif(apptainer, sif):
    result = subprocess.run(
        [apptainer, "inspect", "--json", str(sif)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "inspect failed").strip()[-2000:]
    try:
        payload = json.loads(result.stdout)
        labels = payload["data"]["attributes"]["labels"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        return None, f"invalid Apptainer inspect JSON: {exc}"
    return labels.get("org.label-schema.usage.singularity.deffile.from"), None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def failure_category(error):
    lowered = error.lower()
    if "manifest unknown" in lowered:
        return "registry_manifest_not_found"
    if "403" in lowered or "forbidden" in lowered or "unauthorized" in lowered:
        return "registry_access_forbidden"
    if "invalid reference format" in lowered or "must be lowercase" in lowered:
        return "invalid_image_reference"
    if "timed out" in lowered or "timeout" in lowered:
        return "pull_timeout"
    if "no space left" in lowered:
        return "disk_full"
    if "temporary failure" in lowered or "connection reset" in lowered or "tls" in lowered:
        return "network_failure"
    return "unexpected_pull_failure"


payload = json.loads(sys.stdin.read())
apptainer_bin = payload["apptainer_bin"]
sif_cache_dir = Path(payload["sif_cache_dir"])
failed_output = Path(payload["failed_output"])
provenance_output = Path(
    payload.get("provenance_output")
    or (sif_cache_dir / "login_preheat_provenance.json")
)
timeout = payload["timeout"]
max_attempts = payload["max_attempts"]
retry_backoff = payload["retry_backoff"]
images = list(dict.fromkeys(normalize_image_ref(str(image)) for image in payload["images"]))
cleanup_tmp = bool(payload.get("cleanup_tmp", True))
cleanup_apptainer_cache = bool(payload.get("cleanup_apptainer_cache", False))

os.environ["APPTAINER_CACHEDIR"] = payload["apptainer_cache_dir"]
os.environ["APPTAINER_TMPDIR"] = payload["apptainer_tmp_dir"]
Path(os.environ["APPTAINER_CACHEDIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["APPTAINER_TMPDIR"]).mkdir(parents=True, exist_ok=True)
sif_cache_dir.mkdir(parents=True, exist_ok=True)

if provenance_output.exists():
    try:
        provenance = json.loads(provenance_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        provenance = {}
else:
    provenance = {}
if provenance.get("schema_version") not in {1, 2}:
    provenance = {
        "schema_version": 2,
        "created_at": utc_now(),
        "records": {},
    }
provenance["schema_version"] = 2
old_records = provenance.setdefault("records", {})
records = {}
for old_ref, record in old_records.items():
    normalized = normalize_image_ref(str(old_ref))
    if record.get("status") == "failed" and record.get("error"):
        record = dict(record)
        previous_category = record.get("failure_category")
        current_category = failure_category(str(record["error"]))
        if previous_category and previous_category != current_category:
            record["original_failure_category"] = previous_category
        record["failure_category"] = current_category
    if normalized not in records or record.get("status") in {"cached", "pulled"}:
        if normalized != old_ref:
            record = dict(record)
            record["original_requested_ref"] = old_ref
            record["requested_ref"] = normalized
        records[normalized] = record
provenance["records"] = records
provenance["updated_at"] = utc_now()
provenance["sif_cache_dir"] = str(sif_cache_dir)
previous_requested = [
    normalize_image_ref(str(image))
    for image in provenance.get("requested_images", [])
]
provenance["requested_images"] = list(dict.fromkeys(previous_requested + images))
run_record = {
    "run_id": utc_now(),
    "started_at": utc_now(),
    "requested_images": images,
    "max_attempts": max_attempts,
    "retry_backoff": retry_backoff,
    "downloader_source_sha256": payload.get("downloader_source_sha256"),
    "status": "running",
}
provenance.setdefault("runs", []).append(run_record)
provenance["complete"] = False
atomic_json(provenance_output, provenance)

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
    previous = records.get(image) if isinstance(records.get(image), dict) else None
    digest_before, digest_before_error = ghcr_digest(image)
    if sif.exists():
        cached += 1
        source_ref, inspect_error = inspect_sif(apptainer_bin, sif)
        if previous and previous.get("status") in {"cached", "pulled"}:
            records[image] = {
                **previous,
                "last_verified_at": utc_now(),
                "last_verified_source_ref": source_ref,
                "last_verified_source_ref_error": inspect_error,
                "last_verified_oci_digest": digest_before,
                "last_verified_oci_digest_error": digest_before_error,
                "sif_bytes": sif.stat().st_size,
                "sif_mtime_ns": sif.stat().st_mtime_ns,
                "sif_sha256": sha256_file(sif, previous),
            }
        else:
            records[image] = {
                "status": "cached",
                "requested_ref": image,
                "source_ref": source_ref,
                "source_ref_error": inspect_error,
                "oci_digest": digest_before,
                "oci_digest_error": digest_before_error,
                "digest_observation": "observed_at_audit_time",
                "provenance_strength": "retrospective",
                "sif_path": str(sif),
                "sif_bytes": sif.stat().st_size,
                "sif_mtime_ns": sif.stat().st_mtime_ns,
                "sif_sha256": sha256_file(sif, previous),
                "recorded_at": utc_now(),
            }
        provenance["updated_at"] = utc_now()
        atomic_json(provenance_output, provenance)
        print(
            json.dumps(
                {
                    "event": "cached",
                    "image": image,
                    "sif": sif.name,
                    "oci_digest": digest_before,
                    "provenance_strength": "retrospective",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        continue

    last_error = ""
    ok = False
    attempt_evidence = []
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
                digest_after, digest_after_error = ghcr_digest(image)
                source_ref, inspect_error = inspect_sif(apptainer_bin, sif)
                digest_matches = bool(
                    digest_before
                    and digest_after
                    and digest_before == digest_after
                )
                records[image] = {
                    "status": "pulled",
                    "requested_ref": image,
                    "source_ref": source_ref,
                    "source_ref_error": inspect_error,
                    "oci_digest": digest_after or digest_before,
                    "oci_digest_before": digest_before,
                    "oci_digest_before_error": digest_before_error,
                    "oci_digest_after": digest_after,
                    "oci_digest_after_error": digest_after_error,
                    "digest_observation": (
                        "before_and_after_pull_match"
                        if digest_matches
                        else "pull_digest_unverified"
                    ),
                    "provenance_strength": (
                        "pull_attested" if digest_matches else "sif_only"
                    ),
                    "sif_path": str(sif),
                    "sif_bytes": size,
                    "sif_mtime_ns": sif.stat().st_mtime_ns,
                    "sif_sha256": sha256_file(sif),
                    "recorded_at": utc_now(),
                }
                provenance["updated_at"] = utc_now()
                atomic_json(provenance_output, provenance)
                print(
                    json.dumps(
                        {
                            "event": "ok",
                            "image": image,
                            "sif": sif.name,
                            "bytes": size,
                            "oci_digest": digest_after or digest_before,
                            "provenance_strength": records[image]["provenance_strength"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                break
            last_error = (result.stderr or result.stdout or "unknown pull failure").strip()[-2000:]
        finally:
            if temporary.exists():
                temporary.unlink()

        attempt_evidence.append(
            {
                "attempt": attempt,
                "error": last_error,
                "failure_category": failure_category(last_error),
                "recorded_at": utc_now(),
            }
        )

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
        failed_record = {
            "status": "failed",
            "requested_ref": image,
            "oci_digest": digest_before,
            "oci_digest_error": digest_before_error,
            "failure_category": failure_category(last_error),
            "error": last_error,
            "attempts": attempt_evidence,
            "recorded_at": utc_now(),
        }
        if previous:
            prior_records = list(previous.get("prior_records", []))
            prior_records.append(
                {
                    key: value
                    for key, value in previous.items()
                    if key != "prior_records"
                }
            )
            failed_record["prior_records"] = prior_records
            original_category = previous.get(
                "original_failure_category", previous.get("failure_category")
            )
            if original_category:
                failed_record["original_failure_category"] = original_category
        records[image] = failed_record
        provenance["updated_at"] = utc_now()
        atomic_json(provenance_output, provenance)
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
    "provenance_output": str(provenance_output),
}
terminal_statuses = {"cached", "pulled", "failed"}
requested_set = set(provenance["requested_images"])
provenance["complete"] = requested_set == set(records) and all(
    records[image].get("status") in terminal_statuses for image in requested_set
)
provenance["completed_at"] = utc_now()
provenance["summary"] = {
    "requested": len(requested_set),
    "available": sum(
        records[image].get("status") in {"cached", "pulled"}
        for image in requested_set
    ),
    "failed": sum(
        records[image].get("status") == "failed" for image in requested_set
    ),
}
run_record.update(
    status="completed",
    completed_at=provenance["completed_at"],
    summary={"cached": cached, "pulled": pulled, "failed": len(failures)},
)
atomic_json(provenance_output, provenance)
print(json.dumps(summary, sort_keys=True), flush=True)
for label, directory, enabled in (
    ("apptainer_tmp", Path(os.environ["APPTAINER_TMPDIR"]), cleanup_tmp),
    ("apptainer_cache", Path(os.environ["APPTAINER_CACHEDIR"]), cleanup_apptainer_cache),
):
    if not enabled:
        continue
    removed = 0
    errors = []
    if directory.exists():
        for child in directory.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except Exception as exc:
                errors.append(f"{child}: {type(exc).__name__}: {exc}")
    print(
        json.dumps(
            {
                "event": "cleanup",
                "target": label,
                "path": str(directory),
                "removed_entries": removed,
                "errors": errors,
            },
            sort_keys=True,
        ),
        flush=True,
    )
raise SystemExit(1 if failures else 0)
"""


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


def _remote_images_from_json(
    target: str,
    port: str,
    ssh_key: str,
    remote_path: str,
) -> list[str]:
    script = (
        "python3 -c "
        + shlex.quote(
            "import json, sys; from pathlib import Path; "
            "value=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); "
            "images=value.get('images') if isinstance(value,dict) else value; "
            "assert isinstance(images,list) and all(isinstance(x,str) for x in images); "
            "print(json.dumps(images))"
        )
        + " "
        + shlex.quote(remote_path)
    )
    result = subprocess.run(
        _ssh_command(target, port, ssh_key, script),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to read remote image list: {result.stderr}")
    return list(json.loads(result.stdout.strip().splitlines()[-1]))


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
    if args.missing_only and args.existing_only:
        raise SystemExit("--missing-only and --existing-only are mutually exclusive")

    config = load_optimization_config(args.config, require_api_keys=False)
    if config.container.runtime != "apptainer":
        raise SystemExit(
            f"config container.runtime is {config.container.runtime!r}; expected 'apptainer'"
        )
    ssh_target, ssh_port, ssh_key = _ssh_config(Path(args.ulhpc_config))
    remote_user = ssh_target.split("@", 1)[0]
    remote_hpc_root = os.environ.get(
        "VIBE_HPC_ROOT",
        f"/scratch/users/{remote_user}/vibe-coding-planning",
    )
    apptainer_cache_dir = (
        args.apptainer_cache_dir or f"{remote_hpc_root}/shared/apptainer-cache-login"
    )
    apptainer_tmp_dir = (
        args.apptainer_tmp_dir or f"{remote_hpc_root}/shared/apptainer-tmp-login"
    )
    raw_config = _load_yaml(Path(args.config))
    raw_sif_cache = str(raw_config.get("container", {}).get("sif_cache_dir", ""))
    configured_sif_cache = (
        raw_sif_cache.replace("${USER}", remote_user).replace("$USER", remote_user)
        if raw_sif_cache
        else str(config.container.sif_cache_dir)
    )
    sif_cache_dir = str(args.sif_cache_dir or configured_sif_cache)
    if not sif_cache_dir:
        raise SystemExit(
            "--sif-cache-dir is required when config has no container.sif_cache_dir"
        )
    failed_output = (
        args.failed_output or f"{sif_cache_dir}/login_preheat_failed_images.txt"
    )
    provenance_output = (
        args.provenance_output or f"{sif_cache_dir}/login_preheat_provenance.json"
    )

    images = (
        _remote_images_from_json(
            ssh_target,
            ssh_port,
            ssh_key,
            args.remote_images_json,
        )
        if args.remote_images_json
        else _collect_images(config)
    )
    images = list(dict.fromkeys(_normalize_image_ref(image) for image in images))
    if args.missing_only or args.existing_only:
        existing = _remote_existing_sifs(ssh_target, ssh_port, ssh_key, sif_cache_dir)
        if args.missing_only:
            images = [
                image for image in images if _image_to_sif_name(image) not in existing
            ]
        else:
            images = [
                image for image in images if _image_to_sif_name(image) in existing
            ]
    if args.limit:
        images = images[: args.limit]

    metadata = {
        "ssh_target": ssh_target,
        "image_count": len(images),
        "sif_cache_dir": sif_cache_dir,
        "apptainer_cache_dir": apptainer_cache_dir,
        "apptainer_tmp_dir": apptainer_tmp_dir,
        "timeout": args.timeout,
        "max_attempts": args.max_attempts,
        "retry_backoff": args.retry_backoff,
        "missing_only": args.missing_only,
        "existing_only": args.existing_only,
        "remote_images_json": args.remote_images_json,
        "provenance_output": provenance_output,
        "dry_run": args.dry_run,
        "cleanup_tmp": args.cleanup_tmp,
        "cleanup_apptainer_cache": args.cleanup_apptainer_cache,
        "downloader_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
    }
    print(
        json.dumps({"event": "login_preheat_plan", **metadata}, sort_keys=True),
        flush=True,
    )
    for image in images:
        print(
            json.dumps(
                {
                    "event": "selected_image",
                    "image": image,
                    "sif": _image_to_sif_name(image),
                }
            ),
            flush=True,
        )

    if args.dry_run:
        return 0

    payload = {
        "images": images,
        "sif_cache_dir": sif_cache_dir,
        "apptainer_bin": args.apptainer_bin,
        "apptainer_cache_dir": apptainer_cache_dir,
        "apptainer_tmp_dir": apptainer_tmp_dir,
        "timeout": args.timeout,
        "max_attempts": args.max_attempts,
        "retry_backoff": args.retry_backoff,
        "failed_output": failed_output,
        "provenance_output": provenance_output,
        "cleanup_tmp": args.cleanup_tmp,
        "cleanup_apptainer_cache": args.cleanup_apptainer_cache,
        "downloader_source_sha256": metadata["downloader_source_sha256"],
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
