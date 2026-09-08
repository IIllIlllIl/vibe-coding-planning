from scripts.tools import finalize_swe_chat_rq1_feasibility as finalize


def test_evidence_table_separates_decision_strata() -> None:
    rows = [
        {
            "observed_decision": "ACCEPT",
            "original_task_available": "True",
            "pre_p1_repository_observations_available": "True",
            "later_repository_reads_available": "True",
            "later_code_changes_available": "True",
            "later_validation_evidence_available": "True",
            "immediate_followup_repository_paired_result_count": "1",
            "immediate_followup_code_change_paired_result_count": "1",
            "immediate_followup_validation_paired_result_count": "1",
            "checkpoint_commit_diff_evidence_available": "True",
            "exact_p1_time_worktree_recoverable": "False",
            "repository_proxy_available": "True",
            "developer_feedback_available": "False",
            "later_plan_revision_available": "False",
            "objective_task_success_signal_available": "False",
        },
        {
            "observed_decision": "DO_NOT_ACCEPT",
            "original_task_available": "True",
            "pre_p1_repository_observations_available": "True",
            "later_repository_reads_available": "False",
            "later_code_changes_available": "False",
            "later_validation_evidence_available": "False",
            "immediate_followup_repository_paired_result_count": "0",
            "immediate_followup_code_change_paired_result_count": "0",
            "immediate_followup_validation_paired_result_count": "0",
            "checkpoint_commit_diff_evidence_available": "True",
            "exact_p1_time_worktree_recoverable": "False",
            "repository_proxy_available": "True",
            "developer_feedback_available": "True",
            "later_plan_revision_available": "True",
            "objective_task_success_signal_available": "False",
        },
    ]

    table = finalize.evidence_table(rows)

    assert "| Later code changes (whole session) | 1/2 (50.0%) | 1/1 (100.0%) | 0/1 (0.0%) |" in table
    assert "| Developer technical feedback | 1/2 (50.0%) | 0/1 (0.0%) | 1/1 (100.0%) |" in table


def test_positive_count_counts_cases_not_events() -> None:
    rows = [{"count": "0"}, {"count": "1"}, {"count": "9"}]
    assert finalize.positive_count(rows, "count") == 2
