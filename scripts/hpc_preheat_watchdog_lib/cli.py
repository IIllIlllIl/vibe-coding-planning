from __future__ import annotations

import argparse

from scripts.hpc_preheat_watchdog_lib.config import (
    DEFAULT_PREHEAT_SCRIPT,
    DEFAULT_STATE_FILE,
    DEFAULT_ULHPC_CONFIG,
    WatchdogConfig,
    default_hpc_root,
    parse_command,
    parse_duration,
    resolve_repo_path,
)
from scripts.hpc_preheat_watchdog_lib.slurm import SlurmClient, load_slurm_config
from scripts.hpc_preheat_watchdog_lib.state import load_state, save_state
from scripts.hpc_preheat_watchdog_lib.supervisor import run_forever, run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch ULHPC SIF preheat pilot jobs and submit full preheat after success.",
        allow_abbrev=False,
    )
    parser.add_argument("--pilot-config", required=True)
    parser.add_argument("--full-config", required=True)
    parser.add_argument("--pilot-sif-cache-dir", required=True)
    parser.add_argument("--full-sif-cache-dir", required=True)
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--preheat-script", default=str(DEFAULT_PREHEAT_SCRIPT))
    parser.add_argument("--ulhpc-config", default=str(DEFAULT_ULHPC_CONFIG))
    parser.add_argument(
        "--remote-project-dir",
        default=f"{default_hpc_root()}/runs/vibe-sif-preheat-watchdog",
    )
    parser.add_argument(
        "--remote-dataset-dir",
        default=f"{default_hpc_root()}/hpc_datasets",
    )
    parser.add_argument("--pilot-job-name", default="gepa-preheat-pilot-watchdog")
    parser.add_argument("--full-job-name", default="gepa-preheat-full-watchdog")
    parser.add_argument("--pilot-time", default="00:30:00")
    parser.add_argument("--full-time", default="08:00:00")
    parser.add_argument("--cpus", default="1")
    parser.add_argument("--mem", default="4G")
    parser.add_argument("--pull-timeout", default="0")
    parser.add_argument("--max-pull-attempts", default="1")
    parser.add_argument("--retry-backoff", default="0")
    parser.add_argument("--poll-interval", type=parse_duration, default=3600)
    parser.add_argument("--agent-cooldown", type=parse_duration, default=18000)
    parser.add_argument("--max-repair-attempts", type=int, default=6)
    parser.add_argument("--max-whitelist-violations", type=int, default=2)
    parser.add_argument("--max-agent-cooldowns", type=int, default=20)
    parser.add_argument(
        "--agent-command",
        type=parse_command,
        default=None,
        help="Repair agent command. The watchdog appends the repair prompt as the final argument.",
    )
    parser.add_argument("--enable-agent-repair", action="store_true")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--monitor-full", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> WatchdogConfig:
    return WatchdogConfig(
        pilot_config=resolve_repo_path(args.pilot_config),
        full_config=resolve_repo_path(args.full_config),
        pilot_sif_cache_dir=args.pilot_sif_cache_dir,
        full_sif_cache_dir=args.full_sif_cache_dir,
        state_file=resolve_repo_path(args.state_file),
        preheat_script=resolve_repo_path(args.preheat_script),
        ulhpc_config=resolve_repo_path(args.ulhpc_config),
        remote_project_dir=args.remote_project_dir,
        remote_dataset_dir=args.remote_dataset_dir,
        pilot_job_name=args.pilot_job_name,
        full_job_name=args.full_job_name,
        pilot_time=args.pilot_time,
        full_time=args.full_time,
        cpus=args.cpus,
        mem=args.mem,
        pull_timeout=args.pull_timeout,
        max_pull_attempts=args.max_pull_attempts,
        retry_backoff=args.retry_backoff,
        poll_interval_seconds=args.poll_interval,
        agent_cooldown_seconds=args.agent_cooldown,
        max_repair_attempts=args.max_repair_attempts,
        max_whitelist_violations=args.max_whitelist_violations,
        max_agent_cooldowns=args.max_agent_cooldowns,
        submit=args.submit,
        enable_agent_repair=args.enable_agent_repair,
        stop_after_full_submit=not args.monitor_full,
        **({"agent_command": args.agent_command} if args.agent_command is not None else {}),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    state = load_state(config.state_file)
    slurm = SlurmClient(load_slurm_config(config.ulhpc_config))
    if args.once:
        state = run_once(config, state, slurm)
        save_state(config.state_file, state)
        return 0 if state.phase != "blocked" else 2
    return run_forever(config, state, slurm, lambda new_state: save_state(config.state_file, new_state))
