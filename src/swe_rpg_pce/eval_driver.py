"""Run only inside the evaluator SIF; original RPG parser/scorer stay unmodified.

This stdlib-only file is staged read-only outside the submitted repository.
It never loads a project module or executes a dataset parser on the Host.
"""
import importlib.util
import json
from pathlib import Path


def grade(input_dir, output_dir):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    spec = importlib.util.spec_from_file_location("frozen_rpg_evaluator", input_dir / "official_evaluator.py")
    official = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(official)
    task = json.loads((input_dir / "task.json").read_text())
    text = (output_dir / "print.log").read_text(errors="replace")
    status, parser = official.run_parser(task["log_parser"], text)
    report = official.status_to_report(task, status)
    result = {"parser": parser, "test_status": status, "report": report}
    (output_dir / "parsed.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    grade("/rpg-eval-input", "/rpg-eval-output")
