"""Reflection prompt template for plan optimization.

The structural skeleton (current artefact + per-example feedback +
fenced-block output) is borrowed from GEPA (gepa-ai/gepa).  GEPA itself
optimises *prompt templates* with placeholders; in this project we
optimise *plans* — natural-language workflows produced by the planning
agent — so the default template is reworded accordingly and the
placeholder-preservation constraint is dropped.

The default template lives at module scope as ``DEFAULT_REFLECTION_TEMPLATE``
so users can override it via ``config.prompts.reflection_prompt_template``
without touching Python source.
"""

import re

# Default reflection template.  Placeholders (Python ``str.format`` style):
#   {prompt_template}          -> current plan content (filled by render())
#   {inputs_outputs_feedback}  -> formatted execution feedback (filled by render())
#   {placeholders}             -> retained for backwards compatibility with
#                                custom templates; not used by the default
#                                wording.  render() always supplies it.
DEFAULT_REFLECTION_TEMPLATE = """I provided a planning agent with the following plan to guide a downstream code-generation agent toward fixing a software bug:

```
{prompt_template}
```

The following is feedback from the most recent execution of this plan. You will see:
- The original task (problem statement) given to the planning agent
- Trajectories from the plan, code, and (if any) reflect agents showing how each step was carried out
- Test results and the generated patch
- Any other diagnostic information collected during the run

{inputs_outputs_feedback}

Your task is to write a NEW plan that the planning agent can hand to the next round's code-generation agent. The plan you write replaces the previous plan in full.

Read the feedback carefully and identify:
- Where the previous plan misled the code agent or left it under-specified
- Which navigation / reproduction / patch / validation steps were wrong, missing, or out of order
- Niche, domain-specific facts revealed by the trajectories (file paths, function signatures, edge cases) that should be baked into the new plan so the code agent does not have to rediscover them
- Any generalisable strategy that worked and should be preserved

The new plan MUST follow this exact four-section structure (the downstream agents depend on it):

# Plan

## Navigation (N)
- Locate and identify the relevant source files, functions, classes, and modules involved in the issue.
- Explain how the codebase is structured around the problematic area.

## Reproduction (R)
- Provide concrete steps to reproduce the bug or verify the issue exists.
- Describe the expected vs. actual behaviour.

## Patch (P)
- List each file that needs modification and the exact change to make.
- Include specific code snippets or logic changes (do NOT write full file contents).

## Validation (V)
- Describe how to verify the fix is correct (e.g., run specific tests, check edge cases).
- Mention any regressions or side effects to watch for.

Important constraints:
- Write the improved plan to /tmp/plan.md and finish with: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
- Do not summarise the previous plan, do not explain your reasoning outside the plan file.
- The plan is natural-language guidance, not source code: do not paste full file contents.
"""


def render(
    current_plan: str,
    feedback_data: str,
    placeholders: str = "",
    template: str | None = None,
) -> str:
    """Render the reflection prompt template.

    Args:
        current_plan: The current Plan content (fills ``{prompt_template}``).
        feedback_data: Formatted feedback information (fills
            ``{inputs_outputs_feedback}``).
        placeholders: Comma-separated list of placeholders to preserve
            (fills ``{placeholders}``). Retained for backwards compatibility
            with custom templates that still use it; the default template
            ignores it.
        template: Optional override for the template body. When ``None``
            (default), :data:`DEFAULT_REFLECTION_TEMPLATE` is used.

    Returns:
        The fully rendered prompt string ready to send to the LLM.
    """
    body = template if template is not None else DEFAULT_REFLECTION_TEMPLATE
    return body.format(
        prompt_template=current_plan,
        inputs_outputs_feedback=feedback_data,
        placeholders=placeholders,
    )


def parse_output(llm_response: str) -> str:
    """Extract the first ```-fenced code block from the LLM response.

    If no code block is found, returns the full response text.

    Args:
        llm_response: The raw LLM response string.

    Returns:
        The extracted plan text (inside the first code block), or the full
        response if no code block exists.
    """
    # Match ```language\ncontent\n``` or just ```\ncontent\n```
    pattern = r"```(?:\w+)?\n(.*?)\n```"
    match = re.search(pattern, llm_response, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: no code block found, return the full response
    return llm_response.strip()
