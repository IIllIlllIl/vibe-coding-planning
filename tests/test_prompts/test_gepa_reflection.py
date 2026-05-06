"""Tests for src/prompts/gepa_reflection.py."""

from src.prompts.gepa_reflection import (
    DEFAULT_REFLECTION_TEMPLATE,
    parse_output,
    render,
)


class TestRender:
    def test_default_template_fills_required_placeholders(self):
        """Default template uses {prompt_template} and
        {inputs_outputs_feedback}; both must be substituted."""
        plan = "Current plan content"
        feedback = "The plan failed because..."

        result = render(plan, feedback)

        assert plan in result
        assert feedback in result
        # Both required placeholders should be substituted away
        assert "{prompt_template}" not in result
        assert "{inputs_outputs_feedback}" not in result

    def test_default_template_drops_placeholder_constraint(self):
        """The default plan-optimization template no longer enforces
        GEPA's placeholder-preservation rule (we optimise plans, not
        prompt templates with placeholders)."""
        # The default body must not contain the old GEPA wording
        assert "must keep these exact placeholders intact" not in DEFAULT_REFLECTION_TEMPLATE
        # And must not contain a {placeholders} slot either
        assert "{placeholders}" not in DEFAULT_REFLECTION_TEMPLATE

    def test_default_template_requires_nrpv_structure(self):
        """The default template instructs the LLM to emit the
        N/R/P/V section structure required by downstream agents."""
        assert "Navigation" in DEFAULT_REFLECTION_TEMPLATE
        assert "Reproduction" in DEFAULT_REFLECTION_TEMPLATE
        assert "Patch" in DEFAULT_REFLECTION_TEMPLATE
        assert "Validation" in DEFAULT_REFLECTION_TEMPLATE
        # And requires fenced output
        assert "fenced block" in DEFAULT_REFLECTION_TEMPLATE

    def test_render_with_custom_template(self):
        """When the caller supplies an explicit template, render() must
        use it instead of DEFAULT_REFLECTION_TEMPLATE."""
        custom = (
            "<<CUSTOM>>\n"
            "Plan: {prompt_template}\n"
            "Feedback: {inputs_outputs_feedback}\n"
        )
        result = render("PLAN-X", "FEEDBACK-Y", template=custom)

        assert "<<CUSTOM>>" in result
        assert "PLAN-X" in result
        assert "FEEDBACK-Y" in result
        # Default template's hallmark wording must NOT bleed through
        assert "Navigation (N)" not in result

    def test_render_accepts_legacy_placeholders_kwarg(self):
        """`placeholders` kwarg is retained for backwards compatibility
        with custom templates that still reference {placeholders}."""
        custom = "Plan: {prompt_template}\nFB: {inputs_outputs_feedback}\nPH: {placeholders}"
        result = render("plan", "feedback", placeholders="{issue_description}", template=custom)
        assert "PH: {issue_description}" in result


class TestParseOutput:
    def test_extracts_first_code_block(self):
        response = """
Some preamble.

```
New improved plan here
```

Some postamble.
"""
        result = parse_output(response)
        assert result == "New improved plan here"

    def test_extracts_first_code_block_with_language(self):
        response = """
```markdown
Plan with markdown
```

```
Second block
```
"""
        result = parse_output(response)
        assert result == "Plan with markdown"

    def test_returns_full_response_when_no_code_block(self):
        response = "This is just plain text without any code blocks."
        result = parse_output(response)
        assert result == response.strip()

    def test_returns_full_response_on_empty_string(self):
        result = parse_output("")
        assert result == ""

    def test_extracts_multiline_code_block(self):
        response = """
```
Line 1
Line 2
Line 3
```
"""
        result = parse_output(response)
        assert "Line 1" in result
        assert "Line 2" in result
        assert "Line 3" in result
