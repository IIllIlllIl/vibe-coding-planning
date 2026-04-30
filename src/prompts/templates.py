"""Prompt template management.

Reads prompt templates from the configuration file and provides rendering
utilities for placeholders.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplates:
    """Collection of all prompt templates used by the system."""

    plan_generation: str = ""
    code_generation: str = ""
    plan_optimization: str = ""
    plan_format: str = ""


def load_prompt_templates(config_prompts: dict) -> PromptTemplates:
    """Load prompt templates from the raw config dict.

    Args:
        config_prompts: The ``prompts`` section from the loaded config YAML.

    Returns:
        A ``PromptTemplates`` dataclass with all templates populated.
    """
    return PromptTemplates(
        plan_generation=config_prompts.get("plan_generation_prompt", ""),
        code_generation=config_prompts.get("code_generation_prompt", ""),
        plan_optimization=config_prompts.get("plan_optimization_prompt", ""),
        plan_format=config_prompts.get("plan_format_template", ""),
    )


def render_plan_prompt(template: str, plan_format: str, issue_description: str = "") -> str:
    """Render the plan generation prompt with format template injected.

    Replaces ``{plan_format_template}`` in the prompt with the actual format
    template content. Optionally replaces ``{issue_description}`` if present.

    Args:
        template: The raw plan generation prompt template.
        plan_format: The plan format template to inject.
        issue_description: Optional issue description to inject.

    Returns:
        The fully rendered prompt string.
    """
    result = template.replace("{plan_format_template}", plan_format)
    if issue_description:
        result = result.replace("{issue_description}", issue_description)
    return result


def render_code_prompt(template: str, plan: str, issue_description: str = "") -> str:
    """Render the code generation prompt with plan and issue injected.

    Replaces ``{plan}`` and ``{issue_description}`` placeholders.

    Args:
        template: The raw code generation prompt template.
        plan: The plan content to inject.
        issue_description: The issue description to inject.

    Returns:
        The fully rendered prompt string.
    """
    result = template.replace("{plan}", plan)
    if issue_description:
        result = result.replace("{issue_description}", issue_description)
    return result
