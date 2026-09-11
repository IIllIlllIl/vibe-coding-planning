# ACE formal v3 candidate backup

This directory is the compact local backup of the completed
`formal-30it-v3-20260910` run. `candidates.json` preserves all seven playbooks
with semantically exact parsed rule objects; insignificant whitespace inside
the serialized `rules` strings is normalized. `run_manifest.json` is
semantically and field-for-field identical to the remote authority.

`validation_summary.json` reconstructs confusion counts from the frozen
78-case validation labels and GEPA `val_subscores`. It is a convenience view,
not an independent score authority. The authoritative raw `result.json`, Agent
artifacts, and checkpoints remain at the `remote_authority` path recorded in
that summary.

Remote SHA-256 values before normalization:

- `candidates.json`: `17ffd229e4590ed2c0bfaeb247a479cbd706ed1f87837c5ed8e7c9efd27fa922`
- `run_manifest.json`: `dabcee5772afbf4839344999c497b7a8d368ed46bd010a8ea393b6c18fe63eb8`
