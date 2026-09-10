from scripts.tools.build_verified_playbook_clean_snapshot import _plan_quality_reasons


def test_plan_quality_exclusions_are_frozen_and_disjoint() -> None:
    assert _plan_quality_reasons("sympy__sympy-22080") == [
        "TRIVIAL_PLACEHOLDER_PLAN"
    ]
    assert _plan_quality_reasons("django__django-10097") == [
        "TRUNCATED_OR_STRUCTURALLY_INCOMPLETE_PLAN"
    ]
    assert _plan_quality_reasons("astropy__astropy-12907") == []
