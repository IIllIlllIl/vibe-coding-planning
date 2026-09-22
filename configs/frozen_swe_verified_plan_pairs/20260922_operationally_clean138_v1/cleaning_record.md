# Paired training selection: operational correction (2026-09-22)

This is a new selection over the unchanged 2026-09-19 frozen pair snapshot,
not a rewrite of its plans, outcomes, evidence, or historical GEPA run. Its
source manifest SHA-256 is
`cc1c3488c20a90340d88f419212c2f28b0d60e9a53ab15b84c7e9809751cc736`.
The previous formal run selected 143 train pairs after excluding
`pair-8738412ddd171f1772a98f43`. This selection removes five additional
train pairs, leaving **138 train pairs**; it preserves the original 36-pair
validation split without claiming that split has received a new quality audit.

| Task | Excluded pair IDs | Shared U observation | Reason |
| --- | --- | --- | --- |
| `psf__requests-1921` | `pair-2c766632ba43e42dda0a5cf1`, `pair-fbb28215393b0c5f23dd27a9`, `pair-3906f2cafbe294308f603a0a` | `940dd5940d03630f42dc7db2e7b152886f63c765caee50066c95cc2c4476f016` | Official FAIL_TO_PASS reports `test_basicauth_with_netrc` with an external httpbin 502 instead of expected 401. The pair contrast cannot attribute this runtime HTTP failure to either Plan. |
| `django__django-11749` | `pair-887eec112f32f3388a71e1a6`, `pair-184834be8c81cf8c06e62824` | `86acd6f811219d20e87650a4b8c112ea32a040260f26bc8cec8e5a3e43b7ed9a` | U Code staged a fixture at a path also added by the official test patch. That test patch failed to apply, so the target regression was not actually evaluated. The earlier exclusion missed these two pairs using the same U observation. |

Evidence for this correction is the frozen pair observation identity and the
completed eighth-round Reflector records plus an exhaustive join of the shared
U observation ID against the prior 143-pair train selection. Direct inspection
of the retained raw evaluator output confirmed `502 == 401` and the Django
`already exists in working directory` test-patch error. A boolean official evaluator result
alone was insufficient to detect these semantic evaluation failures.

Do **not** exclude pairs merely because two Code Agents acted differently. For
example, `pair-62b15a8e6978206c8f08a33c` and
`pair-78109f69ff82bbd16ec960be` remain selected: their outcome contrast is
weak Plan-ranking evidence, but Coder compensation may inform nonblocking
Level-0/1 analysis. `pytest-dev__pytest-10356` is flagged for future manual
assessment of hidden exact-signature grading, not silently relabeled or
excluded here. This correction is conservative, not a completed exhaustive
audit of all 139 train or 36 validation pairs.

Selection authority: `selection.json` in this directory, SHA-256
`2c005a8e20246acbef4aa226860e0c7d054859612d69eeaad7e4dab11316358b`.
No new experiment is authorized by this selection alone.
