"""Single-call prompt runtime used inside reject-playbook workers."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined

from src.agents._deps import build_model, import_minisweagent


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        stripped = match.group(1)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


class PromptModel:
    def __init__(self, model_config: Mapping[str, Any]) -> None:
        _, LitellmModel, _ = import_minisweagent()
        key_env = str(model_config.get("api_key_env", "DEEPSEEK_API_KEY"))
        api_key = os.environ.get(key_env)
        if not api_key:
            raise ValueError(f"environment variable {key_env} is not set")
        self.model = build_model(
            LitellmModel,
            str(model_config["model"]),
            api_key,
            str(model_config.get("api_base", "https://api.deepseek.com")),
            float(model_config.get("temperature", 0.0)),
        )

    def __call__(self, system: str, user: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        response = self.model.query(messages)
        assistant = {"role": "assistant", "content": response["content"], "extra": response.get("extra", {})}
        trajectory = [*messages, assistant]
        return _json_object(response["content"]), trajectory


def _render(template: str, **values: Any) -> str:
    return Environment(undefined=StrictUndefined, autoescape=False).from_string(template).render(**values)
