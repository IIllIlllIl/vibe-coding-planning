"""Agent-side command for the fixed PCE evaluator; standard-library IPC only."""
import argparse
import fcntl
import json
from pathlib import Path
import time
import uuid


def evaluate(address, action="evaluate"):
    address = Path(address)
    # Serial callers share two bounded control files, not an accumulating queue.
    # The ID is generated here, never copied or reconstructed by the Agent.
    with (address / "client.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        identifier = uuid.uuid4().hex
        temporary = address / "request.tmp"
        temporary.write_text(json.dumps({"id": identifier, "action": action}))
        temporary.replace(address / "request.json")
        while True:
            try:
                response = json.loads((address / "response.json").read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                response = {}
            if response.get("id") == identifier:
                return response["result"]
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("evaluate", "resume"))
    args = parser.parse_args()
    result = evaluate("/agent-output/pce-command", args.action)
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
