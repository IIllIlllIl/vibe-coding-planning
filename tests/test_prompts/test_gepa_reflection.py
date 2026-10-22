"""Tests for src/prompts/gepa_reflection.py.

The reflection template is now rendered by mini-swe-agent's
``DefaultAgent`` via Jinja2 + StrictUndefined, with the four
placeholders supplied as ``extra_template_vars`` at ``agent.run()``
time. The host-side ``render()`` helper has therefore been removed —
only the lossless ``parse_output`` compatibility helper remains.
"""

from src.prompts.gepa_reflection import parse_output


class TestParseOutput:
    def test_preserves_complete_response_around_code_block(self):
        response = """
Some preamble.

```
New improved plan here
```

Some postamble.
"""
        result = parse_output(response)
        assert result == response

    def test_preserves_all_fenced_blocks(self):
        response = """
```markdown
Plan with markdown
```

```
Second block
```
"""
        result = parse_output(response)
        assert result == response

    def test_returns_full_response_when_no_code_block(self):
        response = "This is just plain text without any code blocks."
        result = parse_output(response)
        assert result == response

    def test_returns_full_response_on_empty_string(self):
        result = parse_output("")
        assert result == ""

    def test_preserves_multiline_code_block(self):
        response = """
```
Line 1
Line 2
Line 3
```
"""
        result = parse_output(response)
        assert result == response
        assert "Line 1" in result
        assert "Line 2" in result
        assert "Line 3" in result

    def test_plan_with_internal_code_example_keeps_prose_and_tail(self):
        response = (" \n# Plan\nInvestigate the complete behavior.\n"
                    "```python\nx = 1\n```\n"
                    + "Supporting observation.\n" * 5000
                    + "Final required validation: preserve {{ task }}.\n\n")
        assert parse_output(response) == response
