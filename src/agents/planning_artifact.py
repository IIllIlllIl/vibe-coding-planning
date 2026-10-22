"""Planner instructions; application evidence stays in the original Plan."""


def rules_plan_action_protocol(base_protocol: str) -> str:
    """New rules path drops a redundant style clause; the frozen v5 stays intact.

    This composes prompt text only. Submission markers and the parser are
    unchanged, and Agent-authored Plan content never passes through this helper.
    """
    return base_protocol.replace(
        "The replacement may use\ntask-appropriate Markdown headings; no fixed subsection headings are required.",
        "Organize the replacement for the task.",
    )


PLANNING_ARTIFACT_INSTRUCTIONS = """
Use the supplied checklist to investigate and address potential flaws in the
proposed approach that would materially threaten correct implementation.
Each condition names a high-level concern; its guide explains how to investigate
and judge that concern. In the Plan, refer to each supplied rule_number and
briefly describe the evidence found and its consequence for the approach,
including when the concern is inapplicable. Organize this explanation as
appropriate for the task. Use the existing final-submission protocol.
""".strip()
