"""Reflection prompt rendering helper.

The reflection prompt template body lives in ``config.yaml`` (single
source of truth) under ``prompts.reflection_prompt_template``. This
module only:

* substitutes the four runtime placeholders the YAML template expects
  (``{prompt_template}``, ``{feedback_intro}``,
  ``{inputs_outputs_feedback}``, ``{nrpv_block}``); and
* extracts the first ``\\`\\`\\`-fenced code block from the LLM's response
  (``parse_output``), used as a fallback when the agent returns plan
  text in its message rather than writing it to ``/tmp/plan.md``.

Wording, structure, and design choices for the template itself are
documented in the YAML comments above
``prompts.reflection_prompt_template``.
"""

import re


def render(
    *,
    current_plan: str,
    feedback_intro: str,
    feedback_body: str,
    nrpv_block: str,
    template: str,
) -> str:
    """Render the reflection system prompt.

    Substitutes the four placeholders the YAML reflection template
    expects, in a single ``str.format`` pass. The template, NRPV block,
    and feedback strings are all passed in by the caller — this function
    has no defaults of its own.

    Args:
        current_plan: Previous round's plan
            (fills ``{prompt_template}``).
        feedback_intro: Level-aware paragraph describing which feedback
            fields are present this round
            (fills ``{feedback_intro}``).
        feedback_body: Assembled trajectories / test results / patch
            (fills ``{inputs_outputs_feedback}``).
        nrpv_block: Shared NRPV section text
            (fills ``{nrpv_block}``).
        template: Raw reflection template body from
            ``config.prompts.reflection_prompt_template``.

    Returns:
        The fully rendered system prompt string ready for DefaultAgent.
    """
    return template.format(
        prompt_template=current_plan,
        feedback_intro=feedback_intro,
        inputs_outputs_feedback=feedback_body,
        nrpv_block=nrpv_block,
    )


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
