"""Tests for src/prompts/gepa_reflection.py."""

from src.prompts.gepa_reflection import _GEPA_REFLECTION_TEMPLATE, parse_output, render


class TestRender:
    def test_fills_all_placeholders(self):
        plan = "Current plan content"
        feedback = "The plan failed because..."
        placeholders = "{issue_description}"

        result = render(plan, feedback, placeholders)

        assert plan in result
        assert feedback in result
        assert placeholders in result
        assert "{prompt_template}" not in result
        assert "{inputs_outputs_feedback}" not in result
        assert "{placeholders}" not in result

    def test_empty_placeholders(self):
        result = render("plan", "feedback", "")
        assert "plan" in result
        assert "feedback" in result
        assert "The instruction must keep these exact placeholders intact: " in result

    def test_template_content_matches_requirement_doc(self):
        """Verify the hard-coded template contains key phrases from Appendix A."""
        assert "I provided an assistant with the following instructions" in _GEPA_REFLECTION_TEMPLATE
        assert "Carefully examine the agent trajectories" in _GEPA_REFLECTION_TEMPLATE
        assert "Do not add new placeholders or remove existing ones" in _GEPA_REFLECTION_TEMPLATE
        assert "Provide the new instruction within ``` blocks." in _GEPA_REFLECTION_TEMPLATE


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
