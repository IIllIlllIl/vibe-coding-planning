"""RPG contracts over real shared PCE components; no model/container/scheduler."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from src.swe_rpg_pce.dataset import prepare_snapshot, load_swe_rpg_pce_cases, EVALUATOR_FILE
from src.swe_rpg_pce.config import load_swe_rpg_pce_config
from src.swe_rpg_pce.runner import SWERPGPCERunner
from src.swe_rpg_pce.evaluator import evaluate_swe_rpg_apptainer, _inputs, SWERPGEvaluatorOperationalError
from src.optimization.safe_pce_learning import outcome_score, IncompletePCEEvaluation
from src.swe_verified_pce.dataset import file_sha256


# Interface fixture only. Official semantics are checked separately against
# the acquired, hash-pinned source, never replaced in production by this stub.
GRADER = '''import json
def run_parser(source, log):
    try:
        namespace = {}
        exec(source, namespace)
        status = namespace['parser'](log)
        return status, {'ok': True, 'status_count': len(status)}
    except Exception as exc:
        return {}, {'ok': False, 'error': str(exc)}
def status_to_report(instance, status):
    return {'instance_id': instance['instance_id'], 'resolved': status.get('target') == 'pass',
            'FAIL_TO_PASS': {'success': ['target'] if status.get('target') == 'pass' else []}}
'''


def rpg_source(tmp_path):
    source = tmp_path / "official"
    (source / "data").mkdir(parents=True)
    (source / "evaluation").mkdir()
    rows, records = [], {}
    for index in range(3):
        ref = "xinzhou1997/swe_rpg_unified@sha256:" + str(index + 1) * 64
        row = dict(instance_id=f"owner__repo-{index + 1}", repo=f"owner/repo{index}", pl="python",
            docker_image=ref, base_commit="b" * 40, working_dir="/testbed2" if index == 1 else "/testbed",
            issues=[dict(issue_title="Fix target", issue_body="Original task body", discussion="FUTURE DISCUSSION")],
            PR_Title="REFERENCE PR", PR_Body="REFERENCE SOLUTION", patch="REFERENCE GOLD PATCH",
            test_patch="REFERENCE TEST PATCH", rebuild_cmds=[":"], test_cmds=[":"], print_cmds=[":"],
            log_parser="def parser(log):\n return {'target': log.strip()}\n", FAIL_TO_PASS=["target"], PASS_TO_PASS=[])
        rows.append(row)
        records[ref] = dict(accepted=True, base_commit_verified=True, expected_base_commit=row["base_commit"],
            head_commit=row["base_commit"], repository_path=row["working_dir"], instance_id=row["instance_id"],
            sif_path=str(tmp_path / f"{index}.sif"), sif_bytes=1, sif_sha256=str(index + 1) * 64,
            provenance_strength="pull_attested", oci_digest=ref.split("@")[1])
    (source / "data/benchmark.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (source / EVALUATOR_FILE).write_text(GRADER)
    authority = tmp_path / "authority.json"
    authority.write_text(json.dumps(dict(dataset="Xin-Zhou-smu/SWE-RPG-Bench", revision="c" * 40,
        files=[dict(path=str(p.relative_to(source)), sha256=file_sha256(p)) for p in source.rglob("*") if p.is_file()])))
    images = tmp_path / "images.json"
    images.write_text(json.dumps(dict(dataset="Xin-Zhou-smu/SWE-RPG-Bench", revision="c" * 40, records=records)))
    snapshot = tmp_path / "snapshot"
    prepare_snapshot(source_root=source, source_manifest=authority, image_manifest=images, destination=snapshot)
    return snapshot, images, source, authority


def rpg_pce_config(tmp_path):
    snapshot, images, _, _ = rpg_source(tmp_path)
    raw = yaml.safe_load(Path("configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml").read_text())
    raw["mode"] = "swe_rpg_pce"
    raw["paths"].update(dataset_snapshot=str(snapshot), image_manifest=str(images), selection_manifest=None,
                         run_dir=str(tmp_path / "pce"))
    path = tmp_path / "pce.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def rpg_result(identifier, resolved=True):
    return {"status": "completed", "terminal_kind": "rpg_tests_resolved" if resolved else "rpg_tests_unresolved",
        "task_outcome": "resolved" if resolved else "unresolved", "evaluator_resolved": resolved,
        "code_patch_applied": True, "test_patch_applied": True, "rpg_parser": {"ok": True},
        "rpg_report": {"instance_id": identifier, "resolved": resolved}, "test_status": {"target": "pass" if resolved else "fail"},
        "stage_exit_codes": dict(setup=0, test_patch=0, code_patch=0, rebuild=0, test=0 if resolved else 1, print=0, parser=0)}


def test_rpg_source_preserved_and_agent_projection_has_no_reference_leak(tmp_path):
    snapshot, images, source, authority = rpg_source(tmp_path)
    assert (snapshot / "instances.jsonl").read_bytes() == (source / "data/benchmark.jsonl").read_bytes()
    cases, _, _ = load_swe_rpg_pce_cases(snapshot, images)
    assert len(cases) == 3
    assert cases[1].working_directory == "/testbed2"
    for case in cases:
        projection = json.dumps(case.agent_projection())
        assert "REFERENCE" not in projection and "FUTURE" not in projection
        assert "Original task body" in projection
        assert case.evaluator_input()["patch"] == "REFERENCE GOLD PATCH"
        assert type(case).from_dict(case.to_dict()) == case
    before = {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    prepare_snapshot(source_root=source, source_manifest=authority, image_manifest=images, destination=snapshot)
    assert before == {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}


@pytest.mark.parametrize("what", ["rows", "evaluator", "image", "workdir", "issue"])
def test_rpg_rejects_tampered_authority(tmp_path, what):
    snapshot, images, source, authority = rpg_source(tmp_path)
    if what in {"rows", "evaluator"}:
        path = snapshot / ("instances.jsonl" if what == "rows" else EVALUATOR_FILE)
        path.write_text(path.read_text() + "tamper")
    elif what == "image":
        images.write_text(images.read_text() + " ")
    else:
        rows = [json.loads(s) for s in (snapshot / "instances.jsonl").read_text().splitlines()]
        if what == "workdir":
            rows[0]["working_dir"] = "/elsewhere"
        else:
            rows[0]["issues"] = []
        (snapshot / "instances.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        manifest = json.loads((snapshot / "manifest.json").read_text())
        manifest["instances_sha256"] = file_sha256(snapshot / "instances.jsonl")
        (snapshot / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_swe_rpg_pce_cases(snapshot, images)


def test_rpg_reuses_parser_agent_backends_and_worker_array(tmp_path):
    from src.pce_dataset import repository_pce_dataset
    from src.swe_verified_pce.hpc_executor import SWEVerifiedPCEHPCExecutor
    path = rpg_pce_config(tmp_path)
    config = load_swe_rpg_pce_config(path, require_api_keys=False)
    dataset = repository_pce_dataset(config.mode)
    cases, _, _ = dataset.load_cases(config.dataset_snapshot, config.image_manifest)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=dataset)
    tasks = executor._prepare(tmp_path / "batch", "fingerprint", cases[:2])
    assert [json.loads(t.manifest_path.read_text())["mode"] for t in tasks] == ["swe_rpg_pce"] * 2
    from src.swe_verified_pce.hpc_executor import build_array_script
    script = build_array_script(config=config, batch_dir=tmp_path / "batch", indices=[0, 1], attempt=1,
                                worker_module=executor.worker_module)
    assert "--array=0,1%3" in script and "src.swe_verified_pce.worker" in script
    assert dataset.runner_class.__bases__ == (SWERPGPCERunner.__bases__[0],)
    assert config.plan.temperature == 1 and config.plan.thinking == "disabled"
    assert config.agent_backends == {}


def test_rpg_binds_case_workdir_without_changing_scientific_text(tmp_path, monkeypatch):
    config = load_swe_rpg_pce_config(rpg_pce_config(tmp_path), require_api_keys=False)
    case = load_swe_rpg_pce_cases(config.dataset_snapshot, config.image_manifest)[0][1]
    from src.swe_verified_pce.runner import SWEVerifiedPCERunner
    runner = SWERPGPCERunner(config, SimpleNamespace(), checkpoint_dir=tmp_path / "cp",
        checkpoint_identity="id", attempt_dir=tmp_path / "attempt")
    def run(self, received):
        assert received is case
        assert self.config.docker.workdir == "/testbed2"
        assert "`/testbed2`" in self.config.code_prompt
        assert self.config.plan == config.plan and self.config.code == config.code
        return {"plan": "  unchanged Plan\n", "patch": "unchanged patch\n"}
    monkeypatch.setattr(SWEVerifiedPCERunner, "run", run)
    assert runner.run(case) == {"plan": "  unchanged Plan\n", "patch": "unchanged patch\n"}
    assert runner.config is config
    monkeypatch.setattr("src.environment.repository_history.RepositoryHistoryCache.validate", lambda *a, **k: None)
    with pytest.raises(Exception, match="missing prepared"):
        runner._ensure_repository_history(case)


@pytest.mark.parametrize("resolved", [True, False])
def test_rpg_scoring_keeps_official_outcome(resolved):
    output = dict(status="completed", pce_status="completed", evaluator_result=rpg_result("task", resolved))
    assert outcome_score(output, mode="swe_rpg_pce", instance_id="task") == float(resolved)


@pytest.mark.parametrize("missing", ["rpg_report", "rpg_parser", "test_status", "stage_exit_codes", "code_patch_applied"])
def test_rpg_missing_evidence_cannot_be_scored_unresolved(missing):
    evaluator = rpg_result("task", False)
    evaluator.pop(missing)
    with pytest.raises(IncompletePCEEvaluation):
        outcome_score(dict(status="completed", pce_status="completed", evaluator_result=evaluator),
                      mode="swe_rpg_pce", instance_id="task")


@pytest.mark.parametrize("patch", [None, "Agent patch\n"])
def test_generated_rpg_shell_records_real_tsv(tmp_path, patch):
    snapshot, images, _, _ = rpg_source(tmp_path)
    case = load_swe_rpg_pce_cases(snapshot, images)[0][1]
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    outputs.mkdir()
    _inputs(inputs, case, patch, snapshot / EVALUATOR_FILE)
    script = inputs / "run.sh"
    subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)
    record = next(line for line in script.read_text().splitlines() if line.startswith("record()"))
    subprocess.run(["bash", "-c", 'OUT="$1"\n' + record + '\nrecord setup 0\nrecord test 1\n',
                    "rpg-stage-record-test", str(outputs)], check=True, capture_output=True)
    assert (outputs / "stage_exit_codes.tsv").read_bytes() == b"setup\t0\ntest\t1\n"


@pytest.mark.parametrize("bad_stage", [None, "test_patch", "rebuild", "parser", "missing_tests"])
def test_rpg_evaluator_preserves_patch_and_uses_shared_environment(tmp_path, monkeypatch, bad_stage):
    snapshot, images, _, _ = rpg_source(tmp_path)
    case = load_swe_rpg_pce_cases(snapshot, images)[0][1]
    observed = {}
    class Environment:
        def __init__(self, **kw):
            observed.update(kw)
            self.inputs = Path(kw["run_args"][-3].split(":")[0])
            self.outputs = Path(kw["run_args"][-1].split(":")[0])
        def execute(self, command, timeout=None):
            assert command == "/bin/bash /rpg-eval-input/run.sh" and timeout is None
            assert self.inputs.joinpath("code.patch").read_bytes() == b"Agent patch\n\n"
            assert b"REFERENCE GOLD" not in self.inputs.joinpath("code.patch").read_bytes()
            assert "REFERENCE" not in self.inputs.joinpath("task.json").read_text()
            assert "FUTURE" not in self.inputs.joinpath("task.json").read_text()
            (self.outputs / "print.log").write_text("fail")
            (self.outputs / "test.log").write_text("original log")
            from src.swe_rpg_pce.eval_driver import grade
            parsed = grade(self.inputs, self.outputs)
            if bad_stage == "missing_tests":
                parsed["test_status"] = {}
                (self.outputs / "parsed.json").write_text(json.dumps(parsed))
            codes = dict(setup=0, test_patch=0, code_patch=0, rebuild=0, test=1, print=0, parser=0)
            if bad_stage in codes:
                codes[bad_stage] = 127 if bad_stage == "rebuild" else 1
            (self.outputs / "stage_exit_codes.tsv").write_text("".join(f"{k}\t{v}\n" for k, v in codes.items()))
            return {"returncode": 0, "output": ""}
        def cleanup(self):
            observed["cleaned"] = True
    monkeypatch.setattr("src.swe_rpg_pce.evaluator.ApptainerEnvironment", Environment)
    monkeypatch.setattr("src.swe_rpg_pce.evaluator.verify_swebench_evaluator_repository", lambda *a, **k: {})
    kwargs = dict(snapshot=snapshot, container=SimpleNamespace(sif_cache_dir=tmp_path, writable_tmpfs=True),
        capacity_window=SimpleNamespace(), workdir=case.working_directory,
        phase_workdir=tmp_path / "workspace", command_timeout=30)
    if bad_stage:
        with pytest.raises(SWERPGEvaluatorOperationalError):
            evaluate_swe_rpg_apptainer("Agent patch\n\n", case, **kwargs)
    else:
        results = []
        result = evaluate_swe_rpg_apptainer("Agent patch\n\n", case, result_callback=results.append, **kwargs)
        assert result["evaluator_resolved"] is False and results == [result]
        assert result["raw_test_output"] == "original log"
    assert observed["cwd"] == "/testbed2" and observed["cleaned"]
    assert ":/rpg-eval-input:ro" in " ".join(observed["run_args"])


def test_rpg_config_import_does_not_require_gepa():
    proc = subprocess.run([sys.executable, "-c", "import sys; import src.swe_rpg_pce.config; assert 'gepa' not in sys.modules"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_rpg_empty_generation_is_not_scored_as_base(tmp_path, monkeypatch):
    snapshot, images, _, _ = rpg_source(tmp_path)
    case = load_swe_rpg_pce_cases(snapshot, images)[0][0]
    monkeypatch.setattr("src.swe_rpg_pce.evaluator.ApptainerEnvironment", lambda **k: pytest.fail("empty generation launched evaluator"))
    with pytest.raises(SWERPGEvaluatorOperationalError, match="rpg_empty_generation"):
        evaluate_swe_rpg_apptainer("", case, snapshot=snapshot,
            container=SimpleNamespace(), capacity_window=None, workdir=case.working_directory,
            phase_workdir=tmp_path / "workdir", command_timeout=30)


def test_rpg_native_config_reuses_existing_agent_switch(tmp_path, monkeypatch):
    path = rpg_pce_config(tmp_path)
    raw = yaml.safe_load(path.read_text())
    native = dict(executor="codex_cli", model="gpt-6-sol", reasoning_effort="high",
        codex_binary="/private/codex", codex_auth_file="/private/auth.json", codex_version="0.160.1")
    for role in ("plan", "code"):
        raw[role] = dict(raw[role], **native)
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = load_swe_rpg_pce_config(path)
    assert config.agent_backends == {"plan": native, "code": native}
    assert config.plan.api_key_env == config.code.api_key_env == ""


def test_rpg_worker_rejects_unknown_binding_without_agent_call(tmp_path):
    from src.swe_verified_pce.worker import run_task
    manifest = tmp_path / "task.json"
    manifest.write_text(json.dumps({"mode": "typo", "case": {}, "fingerprint": "x"}))
    output = tmp_path / "output.json"
    assert run_task(config_path=tmp_path / "absent.yaml", task_manifest_path=manifest, output_path=output,
        attempt_dir=tmp_path / "attempt", checkpoint_dir=tmp_path / "checkpoint", attempt=1) == 1
    failure = json.loads(output.read_text())
    assert failure["status"] == "blocking_failed" and failure["task_outcome"] == "unknown"


@pytest.mark.parametrize("status,expected", [({"a": "pass"}, True),
    ({"a": "pass", "b": "fail"}, False), ({"a": "skip"}, False),
    ({"a": "pass", "prior": "fail"}, False)])
def test_acquired_official_grader_semantics_are_not_tightened(tmp_path, status, expected):
    """Optional real-source parity, in addition to hermetic interface fixtures."""
    authority_path = Path("configs/frozen_swe_rpg/python78-v1-20261012/dataset-source-manifest.json")
    authority = json.loads(authority_path.read_text())
    source = Path(authority["source_root"]) / EVALUATOR_FILE
    if not source.is_file():
        pytest.skip("acquired official source is an external frozen artifact")
    expected_hash = next(entry["sha256"] for entry in authority["files"] if entry["path"] == EVALUATOR_FILE)
    assert file_sha256(source) == expected_hash
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    inputs.mkdir(); outputs.mkdir()
    (inputs / "official_evaluator.py").write_bytes(source.read_bytes())
    (inputs / "task.json").write_text(json.dumps(dict(instance_id="case", FAIL_TO_PASS=["a", "b"],
        PASS_TO_PASS=["prior"], log_parser="import json\ndef parser(log): return json.loads(log)")))
    (outputs / "print.log").write_text(json.dumps(status))
    from src.swe_rpg_pce.eval_driver import grade
    report = grade(inputs, outputs)["report"]
    assert report["resolved"] is expected
    if status == {"a": "pass"}:
        assert report["FAIL_TO_PASS"]["missing"] == ["b"]
        assert report["PASS_TO_PASS"]["missing"] == ["prior"]


def test_shared_history_preheater_accepts_rpg_without_new_builder(tmp_path, monkeypatch, capsys):
    import scripts.tools.preheat_polybench_repository_history as preheat
    path = rpg_pce_config(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["paths"]["selection_manifest"] = str(tmp_path / "snapshot/selection.json")
    path.write_text(yaml.safe_dump(raw))
    config = load_swe_rpg_pce_config(path, require_api_keys=False)
    case = load_swe_rpg_pce_cases(config.dataset_snapshot, config.image_manifest)[0][1]
    Path(case.image.sif_path).write_bytes(b"x")
    original_hash = preheat.file_sha256
    monkeypatch.setattr(preheat, "file_sha256", lambda p: case.image.sif_sha256 if p.suffix == ".sif" else original_hash(p))
    monkeypatch.setattr(preheat, "ApptainerSifCache", lambda *a: SimpleNamespace(sif_path=lambda ref: Path(case.image.sif_path)))
    monkeypatch.setattr(preheat, "configure_docker_capacity", lambda *a, **k: None)
    monkeypatch.setattr(preheat.RepositoryHistoryCache, "validate", lambda *a, **k: None)
    monkeypatch.setattr(preheat.RepositoryHistoryCache, "ensure", lambda *a, **k: pytest.fail("check-only built history"))
    monkeypatch.setattr(preheat, "ApptainerEnvironment", lambda **k: pytest.fail("check-only started container"))
    monkeypatch.setattr(sys, "argv", ["preheat", "--config", str(path), "--instance-id", case.instance_id, "--check-only"])
    assert preheat.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["records"] == [{"instance_id": case.instance_id, "status": "missing"}]


@pytest.mark.parametrize("base_fails_operationally", [False, True])
def test_calibration_reuses_evaluator_without_agents_and_keeps_both_modes(tmp_path, monkeypatch, base_fails_operationally):
    from scripts.tools.verify_swe_rpg_evaluator import verify
    config_path = rpg_pce_config(tmp_path)
    monkeypatch.setattr("scripts.tools.verify_swe_rpg_evaluator.configure_docker_capacity", lambda *a, **kw: None)
    monkeypatch.setattr(SWERPGPCERunner, "_verify_sif", lambda *a: None)
    calls = []
    def evaluator(patch, case, **kwargs):
        calls.append((patch, kwargs["phase_workdir"]))
        if patch is None and base_fails_operationally:
            raise RuntimeError("missing environment")
        result = rpg_result(case.instance_id, patch is not None)
        result["rpg_report"]["FAIL_TO_PASS"] = {"failure": ["target"] if patch is None else []}
        kwargs["result_callback"](result)
        return result
    original = SWERPGPCERunner.__init__
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.evaluator = evaluator
    monkeypatch.setattr(SWERPGPCERunner, "__init__", init)
    monkeypatch.setattr(SWERPGPCERunner, "run", lambda *a: pytest.fail("calibration called Planner/Coder"))
    result = verify(config_path=config_path, instance_id="owner__repo-2", output_dir=tmp_path / "calibration")
    assert result["model_calls"] == 0 and result["passed"] is (not base_fails_operationally)
    assert [call[0] for call in calls] == [None, "REFERENCE GOLD PATCH"]
    assert calls[0][1] != calls[1][1] and not any(call[1].exists() for call in calls)
    with pytest.raises(ValueError, match="already exists"):
        verify(config_path=config_path, instance_id="owner__repo-2", output_dir=tmp_path / "calibration")
