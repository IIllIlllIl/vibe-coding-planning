# mini-SWE / Native Codex command-policy parity

## Scope and engineering baseline

This repair unifies source-command decisions and immediate refusal feedback,
not the complete Agent harness or experimental network policy. The baseline is
the existing Safe PCE Runner and its shared Native SIF transport, documented in
[the PCE backend acceptance](safe-pce-codex-agent-acceptance-20261023.md) and
[the shared-entry acceptance](agent-runtime-entry-acceptance-20261023.md).
The completed DeepSeek smoke `safe-pce-ace-smoke-v1-20261022` supplies the
control-path baseline. No Controller, Supervisor, phase graph, evaluator,
checkpoint, scoring, or retry implementation is replaced.

The Native v3 smoke exposed the difference: Flask Planner/Coder attempts tried
remote `pip` acquisition. mini-SWE would refuse before execution and return an
observation inside its current Agent loop. Native previously executed commands,
then invalidated the completed attempt from raw events. A fresh PCE attempt did
not receive that refusal as corrective feedback. That historical run remains
blocked evidence; this repair does not resume or rewrite it.

## Shared implementation

- `src/environment/source_access.py`: the existing classifier remains the
  policy authority. Common helpers normalize an outer shell envelope and
  construct one refusal observation containing the reason and policy code 126.
- `src/environment/apptainer_env.py`: retains mini-SWE's existing
  pre-execution interception and returns that common observation.
- `src/agents/codex_hooks.py`: a thin Native `PreToolUse` Bash adapter. The
  read-only bundle includes the same policy and existing `bashlex` parser;
  nothing is installed into a case SIF. Allowed commands are not rewritten.
- `src/optimization/codex_cli_runtime.py`: retains the existing SIF/private
  state/sandbox, adds policy mounts and no-model preflights, then validates
  protected hook receipts against the unmodified Native command events.
- `src/agents/pce_backend.py` and the existing repository Agent adapter enable
  the guard where the corresponding source policy is already part of the
  mini-SWE workflow. Other callers do not silently inherit a new policy.
- Affected execution identities include the shared implementation bytes, so
  old post-audit completions cannot masquerade as new guarded executions.

Native uses the [supported hook denial](https://learn.chatgpt.com/docs/hooks),
not an invented tool-result format. The reason contains `returncode=126` and
the Host policy receipt records 126; **no rejected shell process is started,
so Native has no real process exit code to replace**. The hook itself must
not exit 126. It returns `permissionDecision: deny`. Only trust for this
Host-generated hook bundle is bypassed; the inner sandbox remains enabled.

A refusal can be followed by a corrected command in the same Native session.
It does not itself make the Plan scientifically invalid. Missing, malformed,
inconsistent or uncovered command receipts instead block completion as an
operational error. Original Plan, patch, final text and Native events are
preserved; Host receipt records are separate. Hook exceptions explicitly deny.
Native hook-startup/timeout failures can still fail open at CLI level; coverage
validation prevents accepting such an uncovered completion, not retroactively
preventing a command. This is not a complete network or dynamic-code firewall.

## Acceptance evidence

`tests/test_agents/test_codex_command_policy.py` contributes 33 tests, covering
common reasons/126, blocked commands never reaching mini-SWE subprocess
execution, the portable zipapp without site packages, denial and correction,
allowed input preservation, malformed/missing coverage, raw trace preservation,
real production transport wiring and pre-inference failures. The transport
wiring tests mock external subprocesses, not the Host orchestration.

The final targeted union passed **710 tests**, with the same two historical
assertions excluded as in the previous acceptance: PolyBench Coder prompt
equality and a Verified frozen semantic hash. Historical authorities are not
updated to make those assertions pass. Compilation and `git diff --check`
also passed. This is a targeted regression union, not the entire test suite.

The reusable no-model verifier is
`scripts/tools/verify_codex_command_policy.py`. It runs the real CLI against a
loopback-only scripted provider, with empty private HOME/state and no real
credentials. A synthetic command contains an unreachable `pip` invocation and
a forbidden marker; even a broken guard cannot perform the synthetic download.
The marker tests whether the original shell command ran. The next scripted
command writes a permitted marker in the same call.

| Real CLI | Environment | Requests | Forbidden marker | Corrected marker | Refusal in next request | Receipt audit |
|---|---|---:|---|---|---|---|
| 0.164.0 | Local macOS, normal Native sandbox | 3 loopback | Absent | Present | Reason + 126 | Passed |
| 0.160.1 | Iris login, pinned experiment CLI | 3 loopback | Absent | Present | Reason + 126 | Passed |

The final pinned report and exact uploaded bundle/verifier are retained at
`/scratch/users/twang/vibe-coding-planning/operations/codex-command-policy-no-model-20261023-v2/`.
Temporary CLI homes/workspaces were removed. No model quota, Slurm job or
Apptainer invocation was used. This proves the pinned CLI's actual refusal /
feedback / recovery behavior, **not the new hook mount under a compute-node
SIF**. The latter still requires separately authorized production-boundary
verification; local mocks and login evidence are not a substitute.

## Remaining differences

The follow-up PCE correction aligns final-submission feedback and removes Host
Agent budgets. Earlier PCE Plan validation was post-call on **both** backends;
only the general mini-SWE JSON path already supported full in-loop validation.
The original difference summary must not be read as proof that every mini-SWE
PCE Plan-format error had already been recoverable.

The pure existing Plan validators were extracted into
`src/agents/plan_submission.py`, with old imports preserved for compatibility.
mini-SWE uses its existing non-terminating format-error loop. Native uses the
official synchronous `Stop` continuation, with no extra Host Agent loop. A
missing/invalid final Stop receipt is operationally rejected; original invalid
Plan text, final text and patch bytes remain unmodified.

Ten additional tests in `test_pce_submission_feedback.py` exercise real
mini-SWE Plan/Coder correction (including a legacy stdout submission), identical Plan error observations, Native Stop
staging verification, post-validation patch tampering, both-backend budget
removal and retained Host command timeouts. Real CLI 0.164.0 locally and the
pinned 0.160.1 on Iris login each passed the extended no-model probe: four
loopback requests, invalid Plan -> specific feedback -> corrected Plan inside
the same session, with both original submissions retained. The pinned report
is `operations/codex-submission-policy-no-model-20261023-v2/report.json` under
the same remote project root. It retains complete Native events and confirms
no top-level `error` / `turn.failed` event, so recoverable hook refusals are
not confused with failed PCE calls. Native hook-trust warning items remain
unchanged in those events. No production SIF or model inference was used.

| Area | mini-SWE | Native Codex | Assessment |
|---|---|---|---|
| Source-command refusal | Shared classifier; tool observation with returncode 126 | Same classifier/reason; supported hook deny, Host policy code 126 | Unified policy and in-session feedback; native envelopes intentionally differ |
| Experimental network isolation | Existing PCE Agent network flag controls Apptainer; hybrid/evidence roles disable networking | Same caller policy reaches the inner tool profile through `tool_network_disabled`; model/auth traffic remains outside it | Implemented, with matching preflight/inference options and a permission-denial probe before inference. Compute-node/SIF acceptance is separate |
| PCE terminal-format feedback | Common Plan checks and existing format-error loop; empty patch correction uses the established submission validator | Same contract/feedback through Stop; accepted exact final response/patch receipt required | Aligned for current PCE; backend-specific submission transport remains |
| Tools and command lifetime | Existing single-bash action protocol and environment execution | Native tools, structured calls, streaming/async execution | Intentional harness difference. Input to an already running Native shell session is not another checked new command |
| Host Agent limits / accounting | Step/cost, command and phase limits disabled for current Safe PCE; existing usage records remain | No added Host Agent limits; original Native usage events retained | Same no-Host-budget policy; usage formats are not forced into artificial equivalence |
| Sampling/model controls | DS temperature and thinking settings | Codex model and reasoning effort | Intentional provider difference; not a quality-equivalent substitution |
| Authentication/concurrency | Private provider-key environment | Private per-call CLI state and read-only auth snapshots; no whole-call lock under the 2026-10-24 temporary policy | Calls may overlap; refresh persistence/rotation risks are explicitly accepted, not solved. See [decision and evidence](codex-parallel-auth-20261024.md) |
| Scientific/control interfaces | Existing Runner, exact Plan, staged implementation, fresh evaluator, checkpoints and retries | Same Runner/interfaces; different native submission transport and raw trajectory shape | Shared workflow unchanged; no transcript conversion or Host-generated Plan/patch |

The source guard is deliberately partial on both backends. It recognizes the
existing command/source patterns and task URL policy; it is neither syscall
monitoring nor a guarantee against dynamically generated acquisition. The
SWE-chat behavioral path did not previously enable this source-command policy
and is not silently changed by the PCE repair.

## Tool-network policy and its acceptance boundary

The [official permission-profile contract](https://learn.chatgpt.com/docs/permissions)
allows command networking to be disabled independently of the Native control
process's model requests. A whole-container `--network none` would also cut
off the Native CLI model transport and is therefore not an equivalent adapter.
The shared Native runtime now maps the caller's tool-network policy to the
inner permission profile, keeping the source guard and hosted-web-search
control separate. Both PCE backends use `container.network_disabled`; Verified
now loads the same Agent flag already present in PolyBench. Defaults remain
network-enabled for PCE; the v4 smoke explicitly selects disabled networking.
Evaluator behavior is unchanged.

The existing `_permission_options` helper supplies identical configuration to
preflight and inference. Explicit read-only profiles retain read-only files;
writable profiles retain their original repository/Git-index/output roots.
mini-SWE network-disabled evidence/hybrid roles have matching Native policies;
repository JSON Agents retain their mini-SWE counterpart's enabled networking.
Unspecified Native policy preserves its existing role default.

For a disabled policy, the production preflight attempts a local socket in the
actual tool sandbox. Only permission denial counts as success; an unreachable
service or timeout is not accepted as proof of isolation. A failed check stops
before inference. The existing startup verifier accepts
`--tool-network-disabled` and uses the same helper, not a parallel permission
implementation. Twenty-one new no-model tests cover mapping, parsing, both
filesystem modes, preflight failure and success, both PCE roles, resolved
configuration/fingerprint preservation and the frozen v4 smoke contract.

A no-model Iris login probe used the **fixed CLI 0.160.1** and a loopback TCP
service. With the same writable profile and `network.enabled=true`, the
sandboxed process received `PONG`. With `network.enabled=false`, its connection
failed with `Operation not permitted`. Host connections succeeded before and
after both checks. The report is retained at
`operations/codex-submission-policy-no-model-20261023-v1/network-report.json`.
This establishes the pinned CLI's Linux mechanism, not its behavior inside
the production nested SIF boundary. No host network configuration was changed.

The hybrid SWE-chat Native path's missing **outer** network flag alone does
not establish a leak: its inner read-only sandbox also has network controls.
The former PCE gap was its network-enabled writable profile ignoring
`agent_network_disabled`; that mapping is now implemented. The prepared
[archived v4 smoke protocol](../archive/integration/safe-pce-codex-policy-smoke-v4-20261023.md) verifies denied
tool connections and working CLI transport together inside the existing SIF.
No compute-node or model run has been performed by this implementation task.
