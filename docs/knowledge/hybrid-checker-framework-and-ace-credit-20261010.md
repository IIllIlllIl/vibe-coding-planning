# Hybrid Checker framework and ACE credit audit

## Curator context update

`configs/prompts/swe_chat_hybrid_ace_v2.yaml` preserves the v1 Checker,
and Refiner prompts. Its Curator task adds a self-contained description
of Checker inputs, item evaluation, Host any-trigger aggregation and final-output
constraints. It does not reference a conversation, an unseen human example, or
an assumed prior explanation. The completed smoke config still selects v1;
do not alter its frozen prompt or learned candidates retroactively.

The motivation is a recorded conflict: the learned shared guide requested
citations both for triggers and for cleared concerns, whereas Checker requires
`finding: null, evidence: []` for untriggered items. v2 tells Curator to optimize
search and judgment within this fixed output contract. A missing final citation
on an untriggered item is not, by itself, an investigation failure; inspect its
tool trajectory instead.

## ACE source and counting interpretation

Primary source: [ACE paper, arXiv v3](https://arxiv.org/pdf/2510.04618),
section 3.1 and Appendix D, Figures 10 and 13.
The paper tracks helpful/harmful markings and describes feedback about useful
or misleading bullets. Reflector prompts ask for helpful/harmful/neutral tags
in relation to generating a correct answer. It does not specify a binary
trigger/non-trigger counting table for a Plan classifier. Such a table would
be a project-specific adaptation, not a directly prescribed ACE policy.

Observed smoke Reflector attributions for the same correctly untriggered
placeholder item:

| Training case | Tag | Recorded rationale |
|---|---|---|
| WAL cleanup, human accepted | helpful | Correct non-trigger contributed to human-aligned acceptance. |
| N1/N2 experiments, human accepted | helpful | Substantive Plan supported correct non-rejection. |
| OAuth E2E, human rejected | harmful | Sole-rule coverage gap contributed to mistaken overall acceptance. |
| Workflow restructure, human rejected | neutral | Placeholder judgment was sound; intent mismatch required a separate condition. |

The OAuth and workflow reports apply different attribution scopes. Separating
existing-item influence from missing ruleset coverage is a proposed remedy;
The user subsequently approved the attribution clarification: v2 Reflector now
uses demonstrated helpful/harmful effects, makes correct non-trigger insufficient
by itself, separates missing coverage and Checker execution defects from item
defects, and requires recorded evidence for non-neutral tags. Host counting,
deduplication and historical ledgers remain unchanged; this applies to new runs.
Correct non-trigger alone establishes accurate item application, not necessarily
decision-level usefulness. Conversely, a triggered item can be useful even
when another item causes the overall Plan decision to be wrong. Preserve both
the item evidence and final outcome when discussing a future policy.

## rg image preparation

Reuse `scripts/tools/build_codex_isolation_sif.py`, the shared cache writer lock,
atomic publication and the existing `ulhpc-submit` transport. The new optional
base-SIF mode unpacks a hash-pinned image, copies only the regular `rg` executable
from a hash-pinned official release archive, and repacks with one compression
worker. It avoids apt/fakeroot UID transitions that failed in the earlier build.
It must run on a compute node and refuses to overwrite an existing image.
Python, Git and rg version checks must pass before publication. This is not
the full Codex isolation startup check; that remains a separate gate before
using the new image for inference.

Base SIF SHA-256:
`4977b9bbc50118d671a98023f0c6e6dc90c3125308b5c3e9ee6ad0ed57a86345`.
Official rg 14.1.1 x86_64 musl archive:
`https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz`.
Downloaded archive SHA-256:
`4cf9f2741e6c465ffdb7c26f38056a59e2a2544b51f7cc128ef28337eeae4d8e`.
This is a recorded acquisition hash, not an independently verified release signature.
New image identity: `codex-inspection:python312-rg14-v1`.
Operation authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/codex-isolation-rg-v1-20261010`.
Old SIFs and completed experiment configs remain unchanged. A new SIF changes
prepared-history cache identity; prepare/verify those bundles before a future run.
