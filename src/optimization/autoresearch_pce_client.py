"""Agent-side command for the fixed PCE evaluator; standard-library IPC only."""
import argparse
import fcntl
import json
from pathlib import Path
import time
import uuid


def _notify(callback, message):
    if callback is not None:
        try:
            callback(message)
        except OSError:
            pass  # Display failure must not cancel an in-flight evaluation.


def evaluate(address, action="evaluate", *, on_progress=None):
    address = Path(address)
    # Serial callers share two bounded control files, not an accumulating queue.
    # The ID is generated here, never copied or reconstructed by the Agent.
    with (address / "client.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        identifier = uuid.uuid4().hex
        temporary = address / "request.tmp"
        temporary.write_text(json.dumps({"id": identifier, "action": action}))
        temporary.replace(address / "request.json")
        _notify(on_progress, "Request sent; waiting for Host acknowledgment.")
        acknowledged = False
        next_notice = time.monotonic() + 60
        while True:
            try:
                response = json.loads((address / "response.json").read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                response = {}
            if response.get("id") == identifier:
                if "result" in response:
                    return response["result"]
                if not acknowledged and "progress" in response:
                    acknowledged = True
                    progress = response["progress"]
                    _notify(on_progress, progress["message"] + " " + progress["wait_hint"]
                        + " This is a waiting estimate, not a timeout or a score.")
            if on_progress is not None and time.monotonic() >= next_notice:
                _notify(on_progress, "Still waiting on the same request; no additional experiment was started."
                    + (" Host acknowledged it." if acknowledged else " Host acknowledgment not yet observed."))
                next_notice = time.monotonic() + 60
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("evaluate", "resume"))
    args = parser.parse_args()
    result = evaluate("/agent-output/pce-command", args.action,
        on_progress=lambda message: print("[PCE] " + message, flush=True))
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
