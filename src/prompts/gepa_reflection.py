"""Reflection prompt helpers.

The reflection prompt template body lives in ``config.yaml`` (single
source of truth) under ``prompts.reflection_prompt_template``. It uses
Jinja2 placeholders (``{{prompt_template}}``, ``{{feedback_intro}}``,
``{{inputs_outputs_feedback}}``, ``{{nrpv_block}}``) that mini-swe-agent's
``DefaultAgent`` renders at ``agent.run()`` time — the host MUST pass
these values via ``agent.run(**kwargs)``, NOT inline them into the
template source.

The only host-side helper kept here is ``parse_output``, used as a
fallback to extract a ``\\`\\`\\`-fenced plan block from the LLM's submission
message when ``/tmp/plan.md`` is missing.
"""

import re


def parse_output(llm_response: str) -> str:
    """Extract the first ``\\`\\`\\`-fenced code block from the LLM response.

    If no code block is found, returns the full response text. Used by
    ``reflect_agent`` as a fallback when ``/tmp/plan.md`` is missing and
    the agent returned the plan inline in its submission message.

    Args:
        llm_response: The raw LLM response string.

    Returns:
        The extracted plan text (inside the first code block), or the
        full response if no code block exists.
    """
    # Match ```language\ncontent\n``` or just ```\ncontent\n```
    pattern = r"```(?:\w+)?\n(.*?)\n```"
    match = re.search(pattern, llm_response, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: no code block found, return the full response
    return llm_response.strip()
