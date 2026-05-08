"""Tests for src/prompts/gepa_reflection.py."""

import pytest

from src.prompts.gepa_reflection import parse_output, render


# A representative reflection template covering all four placeholders
# the YAML template uses.  The wording is irrelevant — the test focuses
# on placeholder substitution semantics.
SAMPLE_TEMPLATE = (
    "Plan:\n```\n{prompt_template}\n```\n\n"
    "{feedback_intro}\n\n"
    "{inputs_outputs_feedback}\n\n"
    "Use this structure:\n{nrpv_block}\n"
)


class TestRender:
    def test_fills_all_required_placeholders(self):
        """All four placeholders must be substituted."""
        result = render(
            current_plan="PLAN-MARKER",
            feedback_intro="INTRO-MARKER",
            feedback_body="BODY-MARKER",
            nrpv_block="NRPV-MARKER",
            template=SAMPLE_TEMPLATE,
        )

        assert "PLAN-MARKER" in result
        assert "INTRO-MARKER" in result
        assert "BODY-MARKER" in result
        assert "NRPV-MARKER" in result
        # All four placeholders must be substituted away
        assert "{prompt_template}" not in result
        assert "{feedback_intro}" not in result
        assert "{inputs_outputs_feedback}" not in result
        assert "{nrpv_block}" not in result

    def test_intro_distinct_from_body(self):
        """``feedback_intro`` and ``feedback_body`` must land in different
        slots (the YAML template wording is the contract — render() must
        keep them separable)."""
        result = render(
            current_plan="plan",
            feedback_intro="<<INTRO>>",
            feedback_body="<<BODY>>",
            nrpv_block="nrpv",
            template=SAMPLE_TEMPLATE,
        )
        intro_idx = result.index("<<INTRO>>")
        body_idx = result.index("<<BODY>>")
        # In the sample template, intro precedes body
        assert intro_idx < body_idx

    def test_keyword_only_arguments(self):
        """All arguments must be keyword-only — guards against accidental
        positional calls that silently pass arguments to the wrong slot."""
        with pytest.raises(TypeError):
            # Positional invocation should fail because the signature is
            # keyword-only.
            render("plan", "intro", "body", "nrpv", SAMPLE_TEMPLATE)  # type: ignore[misc]

    def test_custom_template_passed_through(self):
        """When the caller supplies a custom template, render() must use
        it directly without reaching for any built-in default."""
        custom = "<<CUSTOM>> P:{prompt_template} I:{feedback_intro} B:{inputs_outputs_feedback} N:{nrpv_block}"
        result = render(
            current_plan="P",
            feedback_intro="I",
            feedback_body="B",
            nrpv_block="N",
            template=custom,
        )
        assert result == "<<CUSTOM>> P:P I:I B:B N:N"

    def test_missing_placeholder_in_template_raises(self):
        """If the template references an unknown placeholder, str.format
        will raise KeyError — propagating loudly is the right behaviour
        because the YAML template is a configuration contract."""
        bad_template = "{prompt_template} {unknown_placeholder}"
        with pytest.raises(KeyError):
            render(
                current_plan="plan",
                feedback_intro="intro",
                feedback_body="body",
                nrpv_block="nrpv",
                template=bad_template,
            )


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
