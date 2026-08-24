# PolyBench frozen-SIF base-commit audit (2026-08-24)

Scope: all 113 image-available members of the frozen Python exact-`v1.1`
source snapshot used by the PolyBench PCE workflow.

For each case, a read-only Apptainer invocation checked the dataset-declared
`base_commit` with `git cat-file -e <base_commit>^{commit}`, resolved that
object, read `HEAD`, and inspected `git status --porcelain=v1
--untracked-files=all` inside `/testbed`.

Results:

- 113/113 SIFs contain the declared commit;
- 113/113 resolve `HEAD` to the declared commit before an Agent starts;
- 113/113 have at least one non-ignored working-tree entry before reset.

The second result does not imply a clean repository. The third establishes
that fresh container isolation alone is insufficient for this dataset. Current
PCE/PCCE therefore restores every Agent repository with
`git reset --hard <base_commit> && git clean -fd`, then verifies exact `HEAD`
and an empty porcelain status. The before/after evidence is stored outside the
repository workspace so the record cannot itself dirty the Agent environment.

This audit establishes image compatibility with the declared revisions. It
does not validate task labels, plans, patches, or guideline quality.
