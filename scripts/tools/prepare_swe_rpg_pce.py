"""Package already acquired RPG authority for the shared Safe PCE adapter."""
import argparse
import json
from pathlib import Path
from src.swe_rpg_pce.dataset import prepare_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--image-manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    print(json.dumps(prepare_snapshot(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
