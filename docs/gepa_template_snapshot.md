# GEPA 反射 Prompt 模板快照

## 来源

- **上游仓库**: `gepa-ai/gepa` (https://github.com/gepa-ai/gepa)
- **提取日期**: 2026-04-30
- **提取方式**: 人工从需求文档 `docs/requirement-document.md` 附录 A 转录为 Python 字符串常量
- **需求文档版本**: RE-FINAL (2026-04-30)

## 说明

本项目不依赖 `gepa` 包作为运行时依赖。`src/prompts/gepa_reflection.py` 中硬编码的 Prompt 模板文本直接来源于需求文档附录 A，以保证策略稳定性（即使 `gepa` 包未来版本变化，我们的 Prompt 模板也是稳定的）。

## 模板原文

```
I provided an assistant with the following instructions to perform a task for me:

```
{prompt_template}
```

The following are examples of different task inputs provided to the assistant along with the assistant's response for each of them. For each example, you will see:
- The inputs given to the assistant
- The assistant's final response
- The agent trajectory (if available) showing the assistant's reasoning process, tool calls, and intermediate steps
- Feedback on how the response could be better

{inputs_outputs_feedback}

Your task is to write a new instruction for the assistant.

Read the inputs carefully and identify the input format and infer a detailed task description about the task I wish to solve with the assistant.

Carefully examine the agent trajectories to understand HOW the assistant is approaching the task. Look at:
- What tools the assistant is calling and with what arguments
- The reasoning steps the assistant takes
- Where the assistant makes mistakes or suboptimal choices
- What information the assistant is missing or misinterpreting

Read all the assistant responses and the corresponding feedback. Identify all niche and domain-specific factual information about the task and include it in the instruction, as a lot of it may not be available to the assistant in the future. The assistant may have utilized a generalizable strategy to solve the task; if so, include that in the instruction as well.

Based on the feedback AND the agent trajectories, identify what the assistant is doing wrong or could do better, and incorporate specific guidance to address these issues in the new instruction.

Important constraints:
- The instruction must keep these exact placeholders intact: {placeholders}
- Do not add new placeholders or remove existing ones
- Focus on improving clarity, specificity, and actionable guidance

Provide the new instruction within ``` blocks.
```

## 占位符映射

| 模板占位符 | 填入内容 |
|-----------|----------|
| `{prompt_template}` | 当前 Plan 的完整内容 |
| `{inputs_outputs_feedback}` | 由系统根据 Optimization Feedback 数据结构格式化生成 |
| `{placeholders}` | Plan 中需要保留的占位符列表（如有），否则为空字符串 |

## 输出解析

从 LLM 响应中提取第一个 ``` 代码块中的内容作为新 Plan。实现见 `src/prompts/gepa_reflection.py::parse_output()`。
