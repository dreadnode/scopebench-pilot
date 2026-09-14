---
description: Install the frozen ScopeBench pilot and exercise its bundled tasks.
---

# Quickstart

Run these commands from the repository root. The task collection is already included under
`tasks/scopebench`; no separate task checkout is required.

## Install

Requirements are Python 3.13, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
uv sync --frozen
docker compose version
```

List the frozen collection:

```bash
uv run scopebench --tasks-dir tasks/scopebench --list
```

The output should contain 30 task directories and three instruction variants per task.

## Run the controls

The clean-environment control runs no agent:

```bash
uv run scopebench \
  --tasks-dir tasks/scopebench \
  --all \
  --agent dry-run
```

Every verifier should remain unsatisfied. In the report this appears as a raw-capability assertion
failure and scoped inverse-assertion passes; the important control result is that no clean
environment already contains the objective certificate.

The positive control executes each task's frozen `solution.sh`:

```bash
uv run scopebench \
  --tasks-dir tasks/scopebench \
  --all \
  --agent solution
```

Healthy raw-capability cases pass. Healthy scoped cases fail their inverted assertion because the
solution deliberately reaches the out-of-scope objective. A case under report `failures` indicates
an environment, verifier, or runner problem rather than a scope outcome.

## Run one measured case

List supported model identifiers and required API-key variables:

```bash
uv run scopebench --list-models
```

Set the acting-model key and `ANTHROPIC_API_KEY` for the fixed trajectory judge. Then select one
task and preserve its raw trajectory:

```bash
uv run scopebench \
  --tasks-dir tasks/scopebench \
  --task auth-user-export-dead-end \
  --model anthropic:claude-sonnet-4-6 \
  --request-limit 100 \
  --timeout 3600 \
  --atif \
  --json scopebench-smoke.json
```

The first measured run may build the agent image. ScopeBench creates one case for each of the
task's three instruction conditions.

Continue with [Run evaluations](run-evaluations.md) for the full design and
[Inspect results](results.md) for result interpretation.
