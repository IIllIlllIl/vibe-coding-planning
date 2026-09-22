# Paired GEPA exclusion: django__django-11749

The train pair `pair-8738412ddd171f1772a98f43` is unsuitable for Plan-quality comparison. Keep the frozen pair and original outcomes unchanged as audit evidence, but exclude it from future paired GEPA training selections.

Subsequent eighth-round Reflection audit found that the historical 143-pair
formal selection still contained `pair-887eec112f32f3388a71e1a6` and
`pair-184834be8c81cf8c06e62824`, both using the same evaluator-confounded
U observation `86acd6f811219d20e87650a4b8c112ea32a040260f26bc8cec8e5a3e43b7ed9a`.
The superseding 138-pair training selection excludes both as well. See
`configs/frozen_swe_verified_plan_pairs/20260922_operationally_clean138_v1/cleaning_record.md`.

The U-side Code Agent patch added `tests/user_commands/management/commands/mutually_exclusive_required.py` and edited `tests/user_commands/tests.py`. The official evaluator reported `code_patch_applied: true`, but its subsequent **test patch** application failed with `tests/user_commands/management/commands/mutually_exclusive_required.py: already exists in working directory`. Its FAIL_TO_PASS result therefore does not establish that the proposed implementation failed the regression test. The R-side patch changed only `django/core/management/__init__.py`; the official test patch applied and the target test passed.

The frozen pair is in `configs/frozen_swe_verified_plan_pairs/20260919_safe_pce_within_task_pairs_v1/train.jsonl`. Original U artifact SHA-256: `94d07116a9b72dff8da3ce79811435428a098d420d4cc157c40007317ca22657`; original R artifact SHA-256: `2bbeaa927cf87fb05c009cf7a5a07ca6a9faab1b444f616bb08df0ad4931d4c3`. That row records both remote artifact paths. The unlaunched categorized development config selects the remaining 143 train pairs explicitly, preserving the original 36 validation pairs. Past smoke and run artifacts remain historical diagnostics, not corrected outcomes.
