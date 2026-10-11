import pytest

from src.exceptions import ControllerYield
from src.optimization.ace_transaction import run_ace_transaction
from src.optimization.hpc.worker_contract import worker_failure


@pytest.mark.parametrize("failure_at", [None, "reflect", "validate", "curate", "finalize"])
def test_transaction_order_and_commit_boundary(failure_at):
    calls = []
    def step(name, result):
        def callback(*args, **kwargs):
            calls.append(name)
            if name == failure_at:
                raise ValueError(name)
            return result
        return callback
    class Ledger:
        apply_reviews = staticmethod(step("count", ("counted", "pending")))
        feedback_for = staticmethod(step("history", "history"))
        commit = staticmethod(step("commit", None))
    def run():
        return run_ace_transaction(ledger=Ledger(), parent="parent", records=["record"],
            reflect=step("reflect", ["review"]), validate_review=step("validate", "valid"),
            curate=step("curate", "delta"), finalize=step("finalize", "candidate"))
    if failure_at:
        with pytest.raises(ValueError, match=failure_at):
            run()
        assert "commit" not in calls
    else:
        assert run() == "candidate"
        assert calls == ["reflect", "validate", "count", "history", "curate", "finalize", "commit"]


def test_yield_is_not_swallowed_or_committed():
    class Ledger:
        def apply_reviews(self, *args):
            pytest.fail("yield must precede counters")
    def reflect(rows):
        raise ControllerYield(batch_dir="batch", job_id=None, reason="waiting")
    with pytest.raises(ControllerYield):
        run_ace_transaction(ledger=Ledger(), parent=None, records=[1], reflect=reflect,
                            validate_review=None, curate=None, finalize=None)


def test_worker_failure_classification_keeps_identity():
    manifest = dict(role="curator", fingerprint="f", task_index=2, instance_id="case")
    for contract in (False, True):
        result = worker_failure(manifest, "start", "validation", ValueError("original"),
                                contract_error=contract)
        assert result["failure_kind"] == ("agent_output_contract" if contract else "operational")
        assert result["error"] == "original"
        assert all(result[key] == value for key, value in manifest.items())
