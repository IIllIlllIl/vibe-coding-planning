"""Prepare (never launch) an explicit task-only Safe PCE / ACE experiment."""
import argparse
import json
from pathlib import Path

from src.optimization.safe_pce_preparation import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("learning-config", "pce-config", "split-selection", "snapshot", "run-dir", "output-config"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--minibatch-size", type=int)
    parser.add_argument("--max-metric-calls", type=int)
    parser.add_argument("--diagnostic-source-root", type=Path)
    parser.add_argument("--diagnostic-source-manifest", type=Path)
    parser.add_argument("--planner-prompt", type=Path,
                        help="Versioned Planner prompt bundle; default preserves the v1 baseline")
    parser.add_argument("--initial-guideline", type=Path,
                        help="Versioned initial checklist; default preserves the v1 seed")
    args = parser.parse_args()
    path = prepare(learning_config=args.learning_config, pce_config=args.pce_config,
                   selection=json.loads(args.split_selection.read_text()), snapshot=args.snapshot,
                   run_dir=args.run_dir, output_config=args.output_config,
                   max_iterations=args.max_iterations, minibatch_size=args.minibatch_size,
                   max_metric_calls=args.max_metric_calls,
                   diagnostic_source_root=args.diagnostic_source_root,
                   diagnostic_source_manifest=args.diagnostic_source_manifest,
                   planner_prompt=args.planner_prompt, initial_guideline=args.initial_guideline)
    print(f"Prepared {path}. No Agent or HPC task was started.")


if __name__ == "__main__":
    main()
