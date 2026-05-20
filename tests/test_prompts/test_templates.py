"""Tests for src/prompts/templates.py."""


from src.prompts.templates import load_prompt_templates


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
