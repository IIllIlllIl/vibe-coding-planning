"""Tests for src/prompts/templates.py."""

import pytest

from src.prompts.templates import (
    PromptTemplates,
    load_prompt_templates,
    render_code_prompt,
    render_plan_prompt,
)


class TestLoadPromptTemplates:
    def test_loads_all_fields(self):
        raw = {
            "plan_generation_prompt": "plan text",
            "code_generation_prompt": "code text",
            "plan_optimization_prompt": "optimize text",
            "plan_format_template": "format text",
        }
        templates = load_prompt_templates(raw)
        assert templates.plan_generation == "plan text"
        assert templates.code_generation == "code text"
        assert templates.plan_optimization == "optimize text"
        assert templates.plan_format == "format text"

    def test_missing_fields_default_to_empty(self):
        raw = {"plan_generation_prompt": "only plan"}
        templates = load_prompt_templates(raw)
        assert templates.plan_generation == "only plan"
        assert templates.code_generation == ""
        assert templates.plan_optimization == ""
        assert templates.plan_format == ""

    def test_empty_dict_returns_all_empty(self):
        templates = load_prompt_templates({})
        assert templates.plan_generation == ""
        assert templates.code_generation == ""
        assert templates.plan_optimization == ""
        assert templates.plan_format == ""


class TestRenderPlanPrompt:
    def test_injects_plan_format_template(self):
        template = "Generate plan. Format: {plan_format_template}"
        plan_format = "## Analysis\n## Steps"
        result = render_plan_prompt(template, plan_format)
        assert "## Analysis" in result
        assert "## Steps" in result
        assert "{plan_format_template}" not in result

    def test_injects_issue_description(self):
        template = "Issue: {issue_description}\nPlan: {plan_format_template}"
        result = render_plan_prompt(template, "format", "bug in parser")
        assert "bug in parser" in result
        assert "{issue_description}" not in result

    def test_no_issue_description_omits_placeholder(self):
        template = "Plan: {plan_format_template}"
        result = render_plan_prompt(template, "format")
        assert "{issue_description}" not in result


class TestRenderCodePrompt:
    def test_injects_plan_and_issue(self):
        template = "Plan: {plan}\nIssue: {issue_description}"
        result = render_code_prompt(template, "fix bug", "parser fails")
        assert "fix bug" in result
        assert "parser fails" in result
        assert "{plan}" not in result
        assert "{issue_description}" not in result

    def test_no_issue_description(self):
        template = "Plan: {plan}"
        result = render_code_prompt(template, "fix bug")
        assert "fix bug" in result
        assert "{plan}" not in result

    def test_unresolved_placeholder_left_intact(self):
        """Missing placeholders should remain as-is, not raise."""
        template = "Plan: {plan}\nExtra: {unprovided}"
        result = render_code_prompt(template, "fix bug")
        assert "fix bug" in result
        assert "{unprovided}" in result

    def test_multiple_placeholders_in_plan_template(self):
        template = "Issue: {issue_description}\nContext: {additional_context}\nPlan: {plan_format_template}"
        result = render_plan_prompt(template, "format", "bug in parser")
        assert "bug in parser" in result
        assert "format" in result
        assert "{issue_description}" not in result
        assert "{plan_format_template}" not in result
        # Missing placeholder stays
        assert "{additional_context}" in result

    def test_special_characters_in_replacement(self):
        plan_format = "## Analysis\n- item 1\n- item 2\n```python\nprint('hello')\n```"
        result = render_plan_prompt("Format: {plan_format_template}", plan_format)
        assert "```python" in result
        assert "print('hello')" in result
