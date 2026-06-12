"""Verify that the vendored GEPA package is importable.

This script is a standalone smoke test. It does not import any project
source modules and therefore cannot affect existing PCT/PCC/Analysis logic.
It also avoids making any LLM API calls.

Run it after installing GEPA in editable mode:

    conda activate mini-swe
    pip install -e third_party/gepa
    python scripts/tools/gepa_import_check.py
"""

from __future__ import annotations

import gepa
import gepa.optimize_anything as oa


def main() -> None:
    print("GEPA import OK")
    print("GEPA location:", getattr(gepa, "__file__", "unknown"))

    # Verify key public symbols are accessible without invoking any API.
    assert hasattr(oa, "optimize_anything")
    assert hasattr(oa, "GEPAConfig")
    assert hasattr(oa, "EngineConfig")
    assert hasattr(oa, "ReflectionConfig")

    config = oa.GEPAConfig(
        engine=oa.EngineConfig(max_metric_calls=5),
    )
    print("GEPA config OK:", config)
    print("All GEPA smoke checks passed.")


if __name__ == "__main__":
    main()
