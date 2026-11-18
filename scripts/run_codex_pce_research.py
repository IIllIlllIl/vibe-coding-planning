"""Run guide-only Codex research with the existing Safe PCE execution backend."""
import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.optimization.codex_pce_research import load_config, run_research, checked_research_identity  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--resume-source", type=Path,
                        help="Explicit predecessor source export for a verified owner-runtime upgrade")
    parser.add_argument("--check-resume", action="store_true",
                        help="Read-only identity/guide/session verification; no model or PCE")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check_resume:
        from tempfile import TemporaryDirectory
        from src.optimization.codex_session_runtime import codex_session_id, restore_codex_sessions
        from src.optimization.safe_pce_execution import sha256
        private = config.run_dir / "private"
        expected = checked_research_identity(config, resume_source=args.resume_source)
        current = json.loads((private / "identity.json").read_text())
        state = json.loads((private / "state.json").read_text())
        for row in state["rounds"]:
            if sha256(config.run_dir / "analysis_inputs" / row["guide"]) != row["guide_sha256"]:
                raise ValueError("evaluated guide identity changed")
        if state.get("pending"):
            pending = state["pending"]
            path = config.run_dir / "analysis_inputs/guides" / f"{pending['round']:04d}.md"
            if sha256(path) != pending["guide_sha256"]:
                raise ValueError("pending guide identity changed")
        session = codex_session_id(private / "native-session/sessions")
        if session is None:
            archives = sorted((private / "analysis").glob("attempt_*/analysis-sessions.json"))
            if not archives:
                raise ValueError("no retained Native session available for resume")
            with TemporaryDirectory(prefix="vibe-check-native-resume-") as temporary:
                restore_codex_sessions(temporary, archives[-1])
                session = codex_session_id(temporary)
        print(json.dumps({"resume_compatible": True, "session_id": session,
            "completed_evaluations": len(state["rounds"]), "evaluation_target": config.max_experiments,
            "pending": state.get("pending"),
            "runtime_identity_upgrade_required": current["sha256"] != expected["sha256"]}, indent=2))
    else:
        print(json.dumps(run_research(config, resume_source=args.resume_source), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
