"""Agent-side command for the fixed PCE evaluator; standard-library IPC only."""
import argparse
import json
from multiprocessing.connection import Client


def evaluate(address, action="evaluate"):
    # send_bytes/recv_bytes never unpickle data from the research Agent.
    with Client(str(address), family="AF_UNIX") as connection:
        connection.send_bytes(action.encode("ascii"))
        return json.loads(connection.recv_bytes().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("evaluate", "resume"))
    args = parser.parse_args()
    result = evaluate("/agent-output/pce.sock", args.action)
    # These are Host facts, not an Agent-authored Plan, patch or guide.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    for split in ("train", "validation"):
        if split in result:
            score = result[split]
            print(f"{split}_score: {score['resolved'] / score['total']:.6f}")
    print(f"status: {result['status']}")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
