"""Run guide-only Codex research with the existing Safe PCE execution backend."""
import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.optimization.codex_pce_research import load_config, run_research  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    print(json.dumps(run_research(load_config(args.config)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
