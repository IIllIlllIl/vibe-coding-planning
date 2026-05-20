"""Prompt template management.

Reads prompt templates from the configuration file. The templates are
passed verbatim to mini-swe-agent's ``DefaultAgent``, which Jinja-renders
both ``system_template`` and ``instance_template`` through
``Template(..., undefined=StrictUndefined).render(**extra_template_vars)``
at run() time (see ``minisweagent.agents.default.DefaultAgent.render_template``).

All LLM-generated and user-controlled content (the approved ``plan``,
the ``nrpv_block``, the ``task`` issue description, etc.) is delivered
as Jinja **variable values** via ``agent.run(task=..., plan=..., ...)``
— never inlined into the template source on the host side. This keeps
mini-swe-agent's single-pass non-recursive render safe even when the
content contains Jinja-looking fragments like ``{{var}}`` or
``{% tag %}`` (as Django/Sphinx/Sympy bug plans regularly do).

The reflection template lives in its own module
(:mod:`src.prompts.gepa_reflection`) only because it ships a
``parse_output`` helper for extracting fenced plan content; the
rendering path is identical to the plan and code agents.
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
