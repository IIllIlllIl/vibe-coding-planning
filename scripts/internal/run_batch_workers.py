"""Internal bounded parallel scheduler for PCT/PCC batch instances."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

DOCKER_STORAGE_PATTERN = re.compile(
    r"no space left on device|input/output error|containerd\.metadata|"
    r"meta\.db|/var/lib/desktop-containerd",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InstanceResult:
    index: int
    instance_id: str
    returncode: int
    elapsed_seconds: int
    result_exists: bool
    resolved: bool | None
    docker_storage_error: bool
    log_path: str


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_master_log(path: Path, message: str) -> None:
    line = f"[{_timestamp()}] {message}"
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _load_instances(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        values = data.get("instances", [])
    elif isinstance(data, list):
        values = data
    else:
        values = []
    return [str(value) for value in values if str(value).strip()]


def _read_resolved(result_path: Path) -> bool | None:
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return any(
        plan.get("test_results", {}).get("resolved") is True
        for plan in data.get("plans", [])
    )


def _run_one(
    *,
    index: int,
    total: int,
    instance_id: str,
    config: Path,
    batch_id: str,
    dataset_short: str,
    output_root: Path,
    log_dir: Path,
    parallel: int,
) -> InstanceResult:
    result_path = (
        output_root / dataset_short / batch_id / instance_id / "result.json"
    )
    log_path = log_dir / f"{instance_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "src.main",
        "--instance",
        instance_id,
        "--config",
        str(config),
        "--batch-id",
        batch_id,
        "--docker-max-concurrent",
        str(parallel),
    ]
    if shutil.which("caffeinate"):
        command = ["caffeinate", "-i", "-s", "-d", *command]

    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    elapsed = int(time.monotonic() - started)
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    result_exists = result_path.is_file()
    return InstanceResult(
        index=index,
        instance_id=instance_id,
        returncode=completed.returncode,
        elapsed_seconds=elapsed,
        result_exists=result_exists,
        resolved=_read_resolved(result_path) if result_exists else None,
        docker_storage_error=bool(DOCKER_STORAGE_PATTERN.search(log_text)),
        log_path=str(log_path),
    )


def run_batch(
    *,
    instances: list[str],
    config: Path,
    batch_id: str,
    dataset_short: str,
    output_root: Path,
    log_dir: Path,
    master_log: Path,
    parallel: int,
    run_one: Callable[..., InstanceResult] = _run_one,
) -> dict[str, Any]:
    """Run incomplete instances with bounded parallelism."""
    if parallel < 1:
        raise ValueError("parallel must be at least 1")

    pending: list[tuple[int, str]] = []
    skipped = 0
    total = len(instances)
    for index, instance_id in enumerate(instances, 1):
        result_path = (
            output_root
            / dataset_short
            / batch_id
            / instance_id
            / "result.json"
        )
        if result_path.is_file():
            skipped += 1
            _append_master_log(
                master_log,
                f"[{index}/{total}] SKIP {instance_id} (result.json exists)",
            )
        else:
            pending.append((index, instance_id))

    completed_results: list[InstanceResult] = []
    fatal_storage = False
    next_pending = 0
    futures: dict[Future[InstanceResult], tuple[int, str]] = {}
    executor = ThreadPoolExecutor(
        max_workers=parallel,
        thread_name_prefix="pct-batch",
    )

    def submit(index: int, instance_id: str) -> None:
        _append_master_log(
            master_log,
            f"[{index}/{total}] START {instance_id} -> "
            f"{log_dir / (instance_id + '.log')}",
        )
        future = executor.submit(
            run_one,
            index=index,
            total=total,
            instance_id=instance_id,
            config=config,
            batch_id=batch_id,
            dataset_short=dataset_short,
            output_root=output_root,
            log_dir=log_dir,
            parallel=parallel,
        )
        futures[future] = (index, instance_id)

    try:
        while next_pending < len(pending) and len(futures) < parallel:
            submit(*pending[next_pending])
            next_pending += 1

        while futures:
            future = next(as_completed(tuple(futures)))
            index, instance_id = futures.pop(future)
            try:
                result = future.result()
            except Exception as exc:
                result = InstanceResult(
                    index=index,
                    instance_id=instance_id,
                    returncode=1,
                    elapsed_seconds=0,
                    result_exists=False,
                    resolved=None,
                    docker_storage_error=False,
                    log_path=str(log_dir / f"{instance_id}.log"),
                )
                _append_master_log(
                    master_log,
                    f"[{index}/{total}] FAIL  {instance_id} scheduler_error={exc}",
                )
            else:
                if result.returncode == 0 and result.result_exists:
                    _append_master_log(
                        master_log,
                        f"[{index}/{total}] DONE  {instance_id} "
                        f"rc={result.returncode} "
                        f"elapsed={result.elapsed_seconds}s "
                        f"resolved={result.resolved}",
                    )
                else:
                    _append_master_log(
                        master_log,
                        f"[{index}/{total}] FAIL  {instance_id} "
                        f"rc={result.returncode} "
                        f"elapsed={result.elapsed_seconds}s "
                        f"(see {result.log_path})",
                    )
                if result.docker_storage_error:
                    fatal_storage = True
                    _append_master_log(
                        master_log,
                        "FATAL Docker storage error detected; no new "
                        "instances will be submitted",
                    )
            completed_results.append(result)

            if not fatal_storage and next_pending < len(pending):
                submit(*pending[next_pending])
                next_pending += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=False)

    failed = sum(
        not (result.returncode == 0 and result.result_exists)
        for result in completed_results
    )
    unscheduled = len(pending) - next_pending
    return {
        "total": total,
        "parallel": parallel,
        "skipped": skipped,
        "started": len(completed_results),
        "completed": sum(
            result.returncode == 0 and result.result_exists
            for result in completed_results
        ),
        "failed": failed,
        "unscheduled": unscheduled,
        "docker_storage_fatal": fatal_storage,
        "results": [asdict(result) for result in completed_results],
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--dataset-short", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--log-dir", type=Path, default=Path("logs/batch"))
    parser.add_argument(
        "--master-log",
        type=Path,
        default=Path("logs/batch_run.log"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("logs/batch_summary.json"),
    )
    parser.add_argument("--parallel", type=int, default=1)
    args = parser.parse_args()

    instances = _load_instances(args.instances)
    if not instances:
        parser.error(f"instance list is empty: {args.instances}")

    summary = run_batch(
        instances=instances,
        config=args.config,
        batch_id=args.batch_id,
        dataset_short=args.dataset_short,
        output_root=args.output_root,
        log_dir=args.log_dir,
        master_log=args.master_log,
        parallel=args.parallel,
    )
    _write_json(args.summary, summary)
    return 74 if summary["docker_storage_fatal"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
