"""Mechanical JSON parsing shared by Agent role adapters."""
import json
import re


class AgentOutputContractError(ValueError):
    """An Agent returned an artifact that Host parsing cannot accept."""


def json_object(text: str) -> dict:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        stripped = match.group(1)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value
