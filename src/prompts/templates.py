"""Prompt template management.

Reads prompt templates from the configuration file and provides rendering
utilities for placeholder substitution. The reflection template lives in
its own module (:mod:`src.prompts.gepa_reflection`) because it has a
richer placeholder set; this module covers the plan and code agents.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplates:
    """Collection of all prompt templates used by the system.

    Mirrors :class:`src.config.PromptConfig` field-for-field so callers
    that loaded the raw ``prompts:`` dict (e.g. tests or tooling) can use
    the same accessors as code paths that go through ``Config``.
    """

    plan_generation: str = ""
    plan_instance: str = ""
    code_generation: str = ""
    code_instance: str = ""
    reflection: str = ""
    reflect_instance: str = ""
    nrpv_block: str = ""


def load_prompt_templates(config_prompts: dict) -> PromptTemplates:
    """Load prompt templates from the raw config dict.

    Args:
        config_prompts: The ``prompts`` section from the loaded config YAML.

    Returns:
        A ``PromptTemplates`` dataclass with all templates populated.
    """
    return PromptTemplates(
        plan_generation=config_prompts.get("plan_generation_prompt", ""),
        plan_instance=config_prompts.get("plan_instance_template", ""),
        code_generation=config_prompts.get("code_generation_prompt", ""),
        code_instance=config_prompts.get("code_instance_template", ""),
        reflection=config_prompts.get("reflection_prompt_template", ""),
        reflect_instance=config_prompts.get("reflect_instance_template", ""),
        nrpv_block=config_prompts.get("nrpv_block", ""),
    )


def render_plan_prompt(template: str, nrpv_block: str) -> str:
    """Render the plan generation system prompt.

    Substitutes ``{nrpv_block}`` in the plan agent's system template with
    the shared NRPV definition. The issue description is delivered to the
    agent via the separate ``plan_instance_template``, not via this
    system-prompt renderer.

    Args:
        template: Raw plan generation system template
            (``config.prompts.plan_generation_prompt``).
        nrpv_block: Shared NRPV section text
            (``config.prompts.nrpv_block``).

    Returns:
        The fully rendered system prompt string.
    """
    return template.replace("{nrpv_block}", nrpv_block)


def render_code_prompt(template: str, plan: str) -> str:
    """Render the code generation system prompt.

    Substitutes ``{plan}`` with the approved plan text. The issue
    description is delivered separately through the SWE-official
    ``code_instance_template`` (Jinja-rendered with the ``task`` kwarg by
    DefaultAgent), so it is intentionally NOT a parameter here.

    Args:
        template: Raw code generation system template
            (``config.prompts.code_generation_prompt``).
        plan: The approved plan content to inject.

    Returns:
        The fully rendered system prompt string.
    """
    return template.replace("{plan}", plan)
