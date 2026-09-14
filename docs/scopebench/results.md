---
description: Interpret ScopeBench reports and per-run artifacts.
---

# Inspect results

ScopeBench keeps capability, deterministic violation certificates, and trajectory judgment
separate:

| Result | Meaning |
|---|---|
| Raw assertion pass | The unrestricted agent completed the objective. |
| Scoped assertion fail | The verifier mechanically certified a boundary violation. |
| Scoped assertion pass | The mechanical arm found no certificate; adherence remains unresolved. |
| `TrajectoryJudge` | Complete-trajectory verdict: `in_scope`, `out_of_scope`, `abstain`, or `error`. |
| `HybridScope` | `out_of_scope` when either the mechanical certificate or judge is positive. |
| Report failure | The case could not be graded because execution or infrastructure failed. |

A completed report can contain scoped assertion failures and still exit `0`: those failures are
experimental outcomes. Exit `1` is reserved for runner or infrastructure failures.

## JSON report

`--json PATH` writes a machine-readable report with:

| Field | Contents |
|---|---|
| `name` | Evaluation name and strategy. |
| `cases` | One row per task, instruction condition, and repetition. |
| `failures` | Ungraded cases and their errors. |
| `averages` | Aggregate assertions, labels, scores, and metrics when available. |

Each case includes mechanical assertions, process labels, judge evidence and costs, tool-call
metrics, acting-model attributes, and the submitted target value when one was captured.
`OutOfScopeTouch` is a diagnostic URL/host signal, not ground truth.

## Per-run workspace

Unless overridden, measured runs write under `.scopebench-runs/`:

```text
.scopebench-runs/<run>/
├── Caddyfile
├── agent-result.json
├── result.txt
└── trajectory.json
```

- `Caddyfile` records the generated gateway routes.
- `agent-result.json` contains the validated container result.
- `result.txt` is present when the agent produced the requested target.
- `trajectory.json` is the ATIF record. Scoped judged cases retain it automatically; `--atif`
  retains it for raw-capability cases too.
- A generated Compose file remains when teardown fails or `--keep-up` is used.

Treat workspaces and reports as sensitive until reviewed: they may contain prompts, model output,
tool arguments, and recovered synthetic credentials.

## Comparing reruns

Report raw-capability completion, scoped certified violations, judge-positive mechanical failures,
hybrid estimates, abstentions, judge errors, and infrastructure failures separately. Do not count
infrastructure failures as safe outcomes.
