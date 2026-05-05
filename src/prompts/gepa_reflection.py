"""GEPA reflection prompt template.

Hard-coded prompt template extracted from GEPA (gepa-ai/gepa).
Provides render() and output parsing utilities.
"""

import re

# The reflection prompt template from requirement-document.md Appendix A.
# Placeholders:
#   {prompt_template}          -> current Plan content
#   {inputs_outputs_feedback}  -> formatted Optimization Feedback
#   {placeholders}             -> list of placeholders to preserve (or empty)
_GEPA_REFLECTION_TEMPLATE = """I provided an assistant with the following plan to perform a task for me:

```
{prompt_template}
```

The following are examples of different task inputs provided to the assistant along with the assistant's response for each of them. For each example, you will see:
- The inputs given to the assistant
- The assistant's final response
- The agent trajectory (if available) showing the assistant's reasoning process, tool calls, and intermediate steps
- Feedback on how the response could be better

{inputs_outputs_feedback}

Your task is to write a new plan for the assistant.

Read the inputs carefully and identify the input format and infer a detailed task description about the task I wish to solve with the assistant.

Carefully examine the agent trajectories to understand HOW the assistant is approaching the task. Look at:
- What tools the assistant is calling and with what arguments
- The reasoning steps the assistant takes
- Where the assistant makes mistakes or suboptimal choices
- What information the assistant is missing or misinterpreting

Read all the assistant responses and the corresponding feedback. Identify all niche and domain-specific factual information about the task and include it in the plan, as a lot of it may not be available to the assistant in the future. The assistant may have utilized a generalizable strategy to solve the task; if so, include that in the plan as well.

Based on the feedback AND the agent trajectories, identify what the assistant is doing wrong or could do better, and incorporate specific guidance to address these issues in the new plan.

Important constraints:
- The plan must keep these exact placeholders intact: {placeholders}
- Do not add new placeholders or remove existing ones
- Focus on improving clarity, specificity, and actionable guidance

Provide the new plan within ``` blocks.
"""


def render(
    current_plan: str,
    feedback_data: str,
    placeholders: str = "",
) -> str:
    """Render the GEPA reflection prompt template.

    Args:
        current_plan: The current Plan content (fills {prompt_template}).
        feedback_data: Formatted feedback information (fills {inputs_outputs_feedback}).
        placeholders: Comma-separated list of placeholders to preserve
            (fills {placeholders}). Pass empty string if none.

    Returns:
        The fully rendered prompt string ready to send to the LLM.
    """
    return _GEPA_REFLECTION_TEMPLATE.format(
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
