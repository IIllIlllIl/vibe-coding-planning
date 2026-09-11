# SWE-Verified Plan/outcome solvability development audit

> Development diagnostic, 2026-09-11. This is not a frozen label set or a
> prevalence estimate.

## Question and sample

This audit asks two separate questions: whether the historical Plan artifact
is trustworthy, and whether a reviewer could form a useful Plan-stage judgment
from the issue and Plan. It samples 40 cases from clean375: 20 historical
resolved and 20 historical unresolved, spread across the available
repositories. Selection used the historical outcome and is therefore not a
blind evaluation. Repository evidence, Code trajectory, hidden tests, and gold
patch were not used for the initial reading recorded below.

`sufficient` means the proposed mechanism is concrete and internally
plausible. `blocker` means the issue and Plan expose a material contradiction,
over-broad change, missing scope, or incomplete mechanism. `uncertain` means
repository or domain evidence would be needed. These are development readings,
not ground truth.

## Resolved sample

| Case | Artifact | Plan-stage reading | Note |
|---|---|---|---|
| astropy-12907 | trustworthy | sufficient | Exact nested-matrix overwrite and discriminating reproduction. |
| astropy-13453 | trustworthy | sufficient | Missing format-setup call is concrete and locally testable. |
| django-10880 | trustworthy | sufficient | Preserves the range key instead of replacing the SQL context. |
| django-10914 | trustworthy | sufficient | Default permission change and affected tests are explicit. |
| django-11066 | trustworthy | sufficient | Missing `using=db` matches the described multi-DB path. |
| matplotlib-13989 | trustworthy | sufficient | Identifies dictionary overwrite that drops `range`. |
| matplotlib-20859 | trustworthy | sufficient | Broadens the legend parent to the common Figure base. |
| flask-5014 | trustworthy | blocker | Says whitespace-only names are rejected, but `if not name` does not do so. |
| xarray-2905 | trustworthy | uncertain | Proposed type list is conceptual and leaves the compatibility boundary open. |
| xarray-3151 | trustworthy | sufficient | Restricts monotonicity checks to actual concatenation dimensions. |
| pylint-6903 | trustworthy | sufficient | Prevents cgroup-derived worker count from reaching zero. |
| pytest-10051 | trustworthy | sufficient | Preserves the shared records-list identity with in-place clear. |
| pytest-5631 | trustworthy | sufficient | Replaces array equality with sentinel identity. |
| sklearn-10297 | trustworthy | blocker | The proposed CV-value contract omits the classifier-specific output shape. |
| sklearn-10844 | trustworthy | uncertain | The stated reproducer does not actually construct the large contingency case. |
| sphinx-10466 | trustworthy | uncertain | Deduplicating locations without reasoning about parallel UUID metadata is risky. |
| sphinx-11445 | trustworthy | sufficient | Separates docinfo syntax from an inline domain role. |
| sympy-11618 | trustworthy | uncertain | Treating missing dimensions as zero is plausible but is an API policy choice. |
| sympy-12096 | trustworthy | uncertain | Recursive eval is plausible; precision and exception behavior remain open. |
| sympy-12419 | trustworthy | sufficient | Symbolic identity entries require `KroneckerDelta`. |

Thirteen of twenty resolved cases look sufficient from the supplied inputs,
two still expose material Plan concerns, and five require more repository or
domain evidence. A resolved implementation therefore does not certify the Plan
as an unqualified good-plan example.

## Unresolved sample

| Case | Artifact | Plan-stage reading | Note |
|---|---|---|---|
| astropy-13033 | corrupted | exclude | Embedded Python was damaged while serializing `/tmp/plan.md`. |
| astropy-13236 | trustworthy | blocker | Reproduction expects immediate removal while the patch only adds a warning. |
| django-10554 | trustworthy | sufficient | Copy-before-mutation mechanism is concrete; U is not explained by the Plan alone. |
| django-10999 | trustworthy | sufficient | Issue supplies the regex fix; a validation arithmetic typo is not a mechanism blocker. |
| django-11087 | trustworthy | blocker | `.only(pk, fk)` does not bound fields later needed by arbitrary cascade logic. |
| matplotlib-20488 | trustworthy | blocker | Adds two overlapping policies without defining all-masked normalization semantics. |
| matplotlib-20676 | trustworthy | blocker | Assumes direct `add_line()` cannot affect data limits without establishing that invariant. |
| requests-2317 | trustworthy | blocker | Leaves the real conversion owner unresolved and proposes conditional edits in two locations. |
| requests-6028 | trustworthy | blocker | Changes a shared auth helper without bounding non-proxy consumers. |
| xarray-6461 | trustworthy | blocker | `attrs[1]` still depends on filtered argument ordering and fallback semantics are unspecified. |
| xarray-6599 | trustworthy | plausible blocker | Replaces DataArray data with its coordinate index without bounding ordinary DataArray behavior. |
| pylint-8898 | trustworthy | plausible blocker | CSV parsing is proposed for a regex-list grammar without establishing quote preservation. |
| pytest-5840 | trustworthy | sufficient | Case-preserving path/key separation is coherent; U likely needs implementation evidence. |
| sklearn-12973 | trustworthy | blocker | Removes a public `fit(copy_X=...)` parameter while calling that backward compatible. |
| sklearn-13124 | trustworthy | blocker | Adds a second shuffle rather than correcting the existing per-class RNG construction. |
| sphinx-10435 | trustworthy | plausible blocker | Offers competing output formats instead of one exact LaTeX contract. |
| sphinx-10449 | trustworthy | plausible blocker | Relies on a narrow string `None` condition and a suspected stale variable path. |
| sympy-12489 | trustworthy | blocker | Explicitly acknowledges that other constructors still return the base class. |
| sympy-13615 | trustworthy | plausible blocker | Complement direction and unknown-containment behavior need a discriminating contract. |
| seaborn-3187 | trustworthy | plausible blocker | Appending one formatter offset to every label is not justified across formatter types. |

Among the 19 artifact-trustworthy unresolved cases, 12 expose a strong
Plan-stage blocker, five expose a plausible but repository-dependent blocker,
and two look sufficient from task and Plan alone. This enriched, outcome-aware
sample shows that strongly interpretable unresolved examples exist, but it
cannot estimate their prevalence in all 116 clean375 unresolved rows. A future
estimate requires selection without reading outcomes during annotation and
must first remove transport-corrupted artifacts.

## Consequences

- Safe PCE improves authority and provenance but does not make R/U a Plan
  quality label.
- Both outcome classes contain informative counterexamples: unresolved cases
  with visible blockers and resolved cases with visible Plan deficiencies.
- A formal solvability estimate should stratify by repository, hide R/U during
  review, and reveal Code/evaluator evidence only after the Plan-stage judgment
  is frozen.
