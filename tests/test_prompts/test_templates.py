"""Tests for src/prompts/templates.py."""


from src.prompts.templates import (
    load_prompt_templates,
    render_code_prompt,
    render_plan_prompt,
)


class TestLoadPromptTemplates:
    def test_loads_all_fields(self):
        raw = {
            "plan_generation_prompt": "plan text",
            "plan_instance_template": "plan instance",
            "code_generation_prompt": "code text",
            "code_instance_template": "code instance",
            "reflection_prompt_template": "reflection text",
            "reflect_instance_template": "reflect instance",
            "nrpv_block": "nrpv text",
        }
        templates = load_prompt_templates(raw)
        assert templates.plan_generation == "plan text"
        assert templates.plan_instance == "plan instance"
        assert templates.code_generation == "code text"
        assert templates.code_instance == "code instance"
        assert templates.reflection == "reflection text"
        assert templates.reflect_instance == "reflect instance"
        assert templates.nrpv_block == "nrpv text"

    def test_missing_fields_default_to_empty(self):
        raw = {"plan_generation_prompt": "only plan"}
        templates = load_prompt_templates(raw)
        assert templates.plan_generation == "only plan"
        assert templates.plan_instance == ""
        assert templates.code_generation == ""
        assert templates.code_instance == ""
        assert templates.reflection == ""
        assert templates.reflect_instance == ""
        assert templates.nrpv_block == ""

    def test_empty_dict_returns_all_empty(self):
        templates = load_prompt_templates({})
        assert templates.plan_generation == ""
        assert templates.plan_instance == ""
        assert templates.code_generation == ""
        assert templates.code_instance == ""
        assert templates.reflection == ""
        assert templates.reflect_instance == ""
        assert templates.nrpv_block == ""


class TestRenderPlanPrompt:
    def test_injects_nrpv_block(self):
        template = "Generate plan. Format:\n{nrpv_block}"
        nrpv = "## Navigation\n## Reproduction\n## Patch\n## Validation"
        result = render_plan_prompt(template, nrpv)
        assert "## Navigation" in result
        assert "## Validation" in result
        assert "{nrpv_block}" not in result

    def test_no_nrpv_placeholder_passes_through(self):
        """Templates without the placeholder are returned untouched."""
        template = "Just instructions, no NRPV reference."
        result = render_plan_prompt(template, "irrelevant")
        assert result == template

    def test_does_not_substitute_issue_description(self):
        """Issue text is delivered via instance_template now; the system
        template renderer must NOT substitute ``{issue_description}``."""
        template = "Plan: {nrpv_block}\nIssue: {issue_description}"
        result = render_plan_prompt(template, "nrpv")
        assert "nrpv" in result
        # The issue placeholder must remain untouched — DefaultAgent's
        # Jinja-rendered instance_template handles issue delivery.
        assert "{issue_description}" in result


class TestRenderCodePrompt:
    def test_injects_plan(self):
        template = "Plan: {plan}"
        result = render_code_prompt(template, "fix bug")
        assert "fix bug" in result
        assert "{plan}" not in result

    def test_does_not_substitute_issue_description(self):
        """Like render_plan_prompt, render_code_prompt must NOT touch
        ``{issue_description}`` — the SWE-official instance_template
        delivers the issue via ``task``."""
        template = "Plan: {plan}\nIssue: {issue_description}"
        result = render_code_prompt(template, "fix bug")
        assert "fix bug" in result
        assert "{issue_description}" in result

    def test_unresolved_placeholder_left_intact(self):
        """Missing placeholders should remain as-is, not raise."""
        template = "Plan: {plan}\nExtra: {unprovided}"
        result = render_code_prompt(template, "fix bug")
        assert "fix bug" in result
        assert "{unprovided}" in result

    def test_special_characters_in_replacement(self):
        nrpv = "## Analysis\n- item 1\n- item 2\n```python\nprint('hello')\n```"
        result = render_plan_prompt("Format: {nrpv_block}", nrpv)
        assert "```python" in result
        assert "print('hello')" in result
