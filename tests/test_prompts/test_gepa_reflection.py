"""Tests for src/prompts/gepa_reflection.py.

The reflection template is now rendered by mini-swe-agent's
``DefaultAgent`` via Jinja2 + StrictUndefined, with the four
placeholders supplied as ``extra_template_vars`` at ``agent.run()``
time. The host-side ``render()`` helper has therefore been removed —
only ``parse_output`` (used as a fallback to extract a fenced plan
from the LLM submission message) remains.
"""

from src.prompts.gepa_reflection import parse_output


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
