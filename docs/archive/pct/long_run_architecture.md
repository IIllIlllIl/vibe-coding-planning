# Long-Run Architecture: 5-Day Unsupervised PCT Batch

> Historical, non-authoritative. Current reusable decisions live in `../../knowledge/`.

> For running the full 500-instance SWE-bench Verified batch without human presence.
>
> Status: local/macOS watchdog design. This is not the current HPC execution
> path. For HPC, use `docs/hpc-submit.md` and validate with
> `scripts/hpc_smoke_check.sh`; Slurm manages job lifetime, so `caffeinate`,
> tmux watchdog supervision, and Claude repair sessions are not required by
> default.

## Design Philosophy

**Keep the watchdog simple and the repair smart.**

- 95% of problems are routine (API rate limit, Docker hiccup, instance-level failure).
  → A 200-line Python script handles these without any LLM involvement.
- 5% of problems need intelligence (unexpected code bug, new error pattern).
  → Claude Code CLI is spawned on-demand as a "repair agent", fixes the code, exits.
- Never lose progress. State is persisted to disk every cycle.

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 0: macOS Sleep Prevention (caffeinate -i -s -d)          │
│  Runs the entire watchdog tree. Never lets the Mac sleep.       │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1: Watchdog (scripts/long_run_watchdog.py)               │
│  • Persistent Python process, main event loop                   │
│  • Reads/writes output/.watchdog_state.json                     │
│  • Parses logs/batch_run.log every 5 min                        │
│  • State machine: running | api_cooldown | repairing | done     │
│  • Kills/restarts the batch tmux session as needed              │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: Batch Runner (tmux session "pct-batch")               │
│  • bash scripts/run_batch_verified.sh                           │
│  • Writes per-instance logs to logs/batch/*.log                 │
│  • Writes master log to logs/batch_run.log                      │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: Repair Agent (tmux session "pct-repair", on-demand)   │
│  • Claude Code CLI invoked with: claude -p                      │
│  • Pre-loaded with error context + system prompt                │
│  • Reads src/, edits code, runs pytest, exits                   │
│  • Watchdog detects tmux session end → resumes batch            │
└─────────────────────────────────────────────────────────────────┘
```

---

## State Persistence

`output/.watchdog_state.json` is the single source of truth:

```json
{
  "batch_id": "run4-full-500",
  "total_instances": 500,
  "completed": 47,
  "current_instance": "django__django-11292",
  "status": "running",
  "api_cooldown_until": null,
  "last_error": null,
  "last_error_time": null,
  "last_heartbeat": "2026-05-12T15:30:00Z",
  "claude_repair_count": 0
}
```

The watchdog re-reads this file on every loop iteration. If the Mac reboots or the watchdog crashes, simply re-starting the watchdog resumes from the exact same state — no manual intervention needed.

---

## Error Classification & Recovery Actions

| Error Pattern | Detected By | Recovery | Claude Involved? |
|---------------|-------------|----------|-----------------|
| **DeepSeek 429 / quota exhausted** | Regex on batch log | Kill batch, sleep 5h, restart | No |
| **DeepSeek 401 (bad key)** | Regex on batch log | Stop forever (fatal) | No |
| **Docker daemon not running** | Regex on batch log | Retry every 5 min × 12, then fatal | No |
| **CodeAgent LimitsExceeded** | Regex on batch log | Normal failure — skip instance, continue | No |
| **Empty plan submission** | Regex on batch log | Normal failure — skip instance, continue | No |
| **ContextWindowExceeded** | Regex on batch log | Skip instance, continue (rare at n=3) | No |
| **Batch process hang** | Log mtime > 2h | Kill batch, restart from current | No |
| **Unexpected Python exception** | Non-zero exit, unknown error | **Invoke Claude Code repair** | **Yes** |
| **New Jinja/template error** | Unknown render error | **Invoke Claude Code repair** | **Yes** |

---

## The 5-Hour API Cooldown Flow

```
Watchdog sees "429 Too Many Requests" in logs
        │
        ▼
  Kill tmux session "pct-batch"
        │
        ▼
  Write state: status="api_cooldown",
              api_cooldown_until = now + 5h
        │
        ▼
  Every 60 s: check if now > cooldown_until
        │
        ▼
  Yes ──► status="running", start_batch()
```

The batch script's existing SKIP logic (`result.json` exists) means restarting the batch is safe — already-completed instances are automatically skipped.

---

## Claude Code Repair Flow

```
Watchdog sees an unknown / code-level error
        │
        ▼
  Collect: error message + last 100 lines of
           batch log + last 50 lines of per-instance log
        │
        ▼
  Spawn tmux "pct-repair":
    claude -p \
      --permission-mode bypassPermissions \
      --allowed-tools "Bash,Edit,Read,Grep,Write" \
      --system-prompt "Autonomous repair agent..." \
      "Error: ... Context: ... Fix and test."
        │
        ▼
  Watchdog enters status="repairing"
        │
        ▼
  Every 60 s: check if tmux "pct-repair" still alive
        │
        ▼
  Dead ──► run pytest to verify fix
         └──► status="running", start_batch()
```

**Why `-p` (print mode)?** Claude receives the full error context in one prompt, uses tools (Read/Edit/Bash) within that single turn to fix and test, prints the result, and exits. No human interaction required.

**Risk:** If the repair requires multi-turn reasoning (e.g., fix → test fails → need different fix), `-p` handles it in one turn because Claude can call tools multiple times within that turn. If the fix is genuinely beyond single-turn, the watchdog will detect pytest failure and invoke Claude again.

---

## Comparison with Existing Harnesses

| Harness | Approach | Why We Differ |
|---------|----------|---------------|
| **OpenHands eval** | Docker-native restart, per-task containers | Our pipeline already has Docker isolation; we need inter-task orchestration |
| **SWE-agent official eval** | Bash loop, manual restart on crash | No auto-recovery, no state persistence, no repair agent |
| **Moatless Tools** | Structured evaluation with retry counts | Too heavy; we need lightweight 5-day autonomy |
| **supervisord/pm2** | Process managers with auto-restart | No understanding of PCT-specific error patterns |
| **Our design** | Domain-aware watchdog + LLM repair agent | Lightweight + understands PCT semantics (429 vs LimitsExceeded vs code bug) |

---

## Launch Commands

```bash
# 1. Generate 500-instance sample file (one-time)
python -c "
import json, random
from datasets import load_dataset
ds = load_dataset('SWE-bench/SWE-bench_Verified', split='train')
ids = [x['instance_id'] for x in ds]
random.seed(42)
random.shuffle(ids)
with open('output/SWE-bench_Verified/run4-full-500/sampled_instances.json', 'w') as f:
    json.dump({'instances': ids[:500]}, f)
"

# 2. Set batch_id in config.yaml
#    system.batch_id: "run4-full-500"
#    system.n: 3
#    system.skip_completed_rounds: true

# 3. Start the 5-day run
export DEEPSEEK_API_KEY="..."
export ANTHROPIC_API_KEY="..."  # for Claude repair agent
caffeinate -i -s -d \
  bash -c 'conda activate mini-swe && python scripts/long_run_watchdog.py'
```

The `caffeinate` flags:
- `-i`: Prevent idle sleep (critical)
- `-s`: Prevent system sleep when on AC power
- `-d`: Prevent display sleep

---

## Monitoring Remotely (Optional)

If you want to check progress without SSH:

```bash
# From your laptop, scp the state file
cat output/.watchdog_state.json

# Or tail the watchdog log
tail -f logs/watchdog.log

# Check how many instances are done
ls output/SWE-bench_Verified/run4-full-500/*/result.json | wc -l
```

---

## Failure Scenarios & Mitigations

| Scenario | Mitigation |
|----------|-----------|
| **Mac reboots** | Re-run the same launch command. Watchdog reads state.json and resumes. |
| **Watchdog crashes** | Same as above — state is on disk, not in memory. |
| **Claude repair makes things worse** | Watchdog runs pytest before resuming. If tests fail, it reverts (git checkout) and tries again. |
| **DeepSeek down for > 5h** | Watchdog will hit 429 again, re-enter cooldown. Loops until API recovers. |
| **Disk fills up** | Watchdog monitors disk usage. If < 10 GB free, pauses batch and logs alert. |
| **Docker daemon dies and stays dead** | After 12 retries (60 min), watchdog sets status="fatal" and stops. Manual fix required. |

---

## Known Limitations

1. **Claude Code CLI requires ANTHROPIC_API_KEY** for the repair agent. This is separate from DEEPSEEK_API_KEY. Budget: expect ~$1–5 per repair invocation (rare events).
2. **`-p` mode repairs are single-turn.** Complex multi-file refactors may need human intervention. In practice, post-Jinja-fix, we don't expect such bugs.
3. **No network notifications.** If you want Slack/email alerts on fatal errors, wire them into `on_fatal()` in the watchdog.
