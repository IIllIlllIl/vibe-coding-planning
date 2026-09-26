# Current Research Decisions And Issues

Authority: current research decisions and unresolved methodological risks.
Runtime progress belongs in run artifacts. Historical Behavioral and PCCE
results belong in the linked knowledge/archive documents, not in this page.

## Accepted paired blocking-signal direction

The current method contract is
[paired-blocking-signal-learning.md](docs/paired-blocking-signal-learning.md).

- Learn human-readable, pre-implementation failure conditions that justify
  pausing a Plan, not requirements for uniformly excellent or complete Plans.
- Keep classification-only candidate evaluation: do not add Replan, Code or
  Evaluate calls to training. Preserve the older no-repository path unchanged.
- The repository-aware Checker sees one issue, Plan, temporary rule numbers/text
  and a disposable frozen base repository. It never receives outcomes, stable
  IDs, counters, Code/evaluator evidence or Reflection attribution.
- Use binary findings and reject on any trigger; the current path has no Level
  or confidence gate. A pair scores +1 for accepting R and rejecting U, -1 for
  the inverse, and 0 otherwise. Candidate bullet-length invalidity scores -100;
  operational failures remain separate.
- One Reflector analyzes both Plans and full recorded Checker investigations,
  Code behavior and evaluation evidence. Downstream discovery is permitted,
  but the reusable condition must be grounded in task, Plan and base-repository
  facts. A discrepancy adequately resolved by simple correction is not a
  blocking signal; substantial changes/replanning require analysis, not
  automatic rejection.
- Curator reads the complete reflection batch and maintains existing rules as
  well as adding new ones. Host applies tags and operations mechanically; it
  never repairs experimental content. UPDATE/MERGE create fresh rule IDs and
  counters. Misapplication does not exempt a harmful rule from maintenance.
- Retain helpful/harmful counters independently of repairability. No additional
  historical failure-memory ledger, risk-analysis field or forced rule count
  has been introduced.
- Prefer short plain-English rules, normally at most 32 tokens; 64 tokens is
  the mechanical per-bullet ceiling. The frozen diagnostic has a 10,000-token
  visible-playbook limit and disables Refiner.

## Data and diagnostic boundaries

The reference-only clean127 derivative contains 127 train pairs across 43 tasks
and 36 development-validation pairs across 11 tasks. Whole tasks remain within
one split. See the
[cleaning record](docs/knowledge/paired-blocking-signal-cleaning-20260926.md)
for exclusions, artifact hashes and residual-noise limits.

The iteration-9 diagnostic freezes an earlier ACE formal15 parent and exact
Checker outputs, then regenerates Ref/Cur under a new identity. It is not a
continuation of that search tree, a new held-out evaluation, or authorization
to launch. Frozen source datasets, candidates and evidence remain immutable.

## Open issues

1. R/U is an implementation-success proxy, not direct Plan quality or causal
   attribution. Equivalent Plans with implementation-dominated outcome
   differences and evaluator/environment failures need evidence-based cleaning.
2. A raw evaluator failure can conceal HTTP, quota, test-patch or environment
   problems. The derivative cleaning does not repair the producer's authority
   gap or certify the retained set as noise-free.
3. Test whether the repaired evidence/prompt path learns usable blocking
   conditions without converting simple corrections into blockers. Checker
   search anchoring remains a separate investigation.
4. Development validation is used for candidate selection. Neither this split
   nor exposed historical analysis sets can support an untouched-holdout claim.
5. Downstream intervention benefit requires an actual rejection/revision-mediated
   U-to-R; first-review ACCEPT followed by a stochastic Code flip is not enough.
   Report regressions and operational incompletes separately.

## Historical evidence and reusable execution

[Offline/PCCE findings](docs/knowledge/offline-pcce-stage-findings.md) and
[historical redesign contracts](docs/offline-gepa-playbook-redesign.md) explain
earlier failures. Their Level, scoring, resource and prompt versions must not
be copied into current scientific configs. Reuse tested execution components,
not old experiment identities or labels.

New experiments require an explicit user instruction and frozen inputs, split,
prompts, identity, budget, stop conditions and incomplete policy. Operational
execution requirements belong in
[engineering-contracts.md](docs/engineering-contracts.md).
