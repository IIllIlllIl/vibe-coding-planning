"""Phase 3: LLM smoke test via mini-swe-agent 1.17.5 LitellmModel.

Verifies that:
1. LitellmModel can be constructed with deepseek prefix
2. DeepSeek API key is valid
3. A minimal completion request succeeds
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from src.agents._deps import build_model, import_minisweagent


def main() -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 1

    _, LitellmModel, _ = import_minisweagent()

    print("[1/3] Constructing LitellmModel...")
    model = build_model(
        LitellmModel,
        model_name="deepseek-v4-flash",
        api_key=api_key,
        api_base="https://api.deepseek.com",
    )
    print(f"  config: {model.config}")

    print("[2/3] Calling LitellmModel.query()...")
    try:
        result = model.query(
            [{"role": "user", "content": "Reply with exactly the single word: pong"}]
        )
        print(f"  SUCCESS!")
        print(f"  response (truncated 200): {str(result)[:200]!r}")
        if "pong" in str(result).lower():
            print("[3/3] Phase 3 PASSED: API key valid, model responsive")
            return 0
        else:
            print("  WARNING: response did not contain 'pong', but call succeeded")
            return 0
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")

    print("ERROR: No working generation method found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
