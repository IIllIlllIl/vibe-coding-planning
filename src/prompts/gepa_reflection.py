"""Reflection prompt helpers.

The reflection prompt template body lives in ``config.yaml`` (single
source of truth) under ``prompts.reflection_prompt_template``. It uses
Jinja2 placeholders (``{{prompt_template}}``, ``{{feedback_intro}}``,
``{{inputs_outputs_feedback}}``, ``{{nrpv_block}}``) that mini-swe-agent's
``DefaultAgent`` renders at ``agent.run()`` time — the host MUST pass
these values via ``agent.run(**kwargs)``, NOT inline them into the
template source.

The compatibility helper ``parse_output`` preserves the complete submitted
Plan when ``/tmp/plan.md`` is missing. Markdown fences are Plan content, not
an instruction for the Host to select an excerpt.
"""


def parse_output(llm_response: str) -> str:
    """Return the original submission verbatim; never infer a Plan excerpt."""
    return llm_response
