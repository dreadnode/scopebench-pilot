---
description: Understand the files and contracts in the frozen pilot tasks.
---

# Frozen task anatomy

Each direct child of `tasks/scopebench` is one pilot task:

```text
tasks/scopebench/<task>/
├── task.yaml
├── rubric.md
├── docker-compose.yaml
├── solution.sh
├── verify.sh
└── <task-specific services and assets>
```

The exact filenames vary where a task uses flag verification instead of `verify.sh`, but every
task has a manifest and a Compose environment.

## Manifest

`task.yaml` records:

| Field | Purpose |
|---|---|
| `name` | Human-readable task identity. |
| `instruction` | Raw, casual-scope, and program-brief prompts. |
| `rubric` | Evaluator-only boundary specification for scoped trajectories. |
| `verification` | Flag digest or script-verifier contract. |
| `solution.script` | Deterministic positive control. |
| `max_agent_timeout_sec` | Optional task-specific budget cap. |
| `network` | Agent-facing hostnames and HTTP/TCP routing. |
| `difficulty` | Report metadata. |

Each pilot manifest contains these condition identifiers:

```yaml
instruction:
  - id: raw-capability-v1
    scope: raw
    text: ...
  - id: casual-scope-v2
    scope: scoped
    text: ...
  - id: program-brief-v3
    scope: scoped
    text: ...
rubric: rubric.md
```

The scoped prompts share one rubric. It enumerates prohibited hosts, identities, resources, and
operations, connects them to the user-facing boundary, and records task-specific exceptions. The
acting agent never receives the rubric.

## Verification

Flag tasks declare an output path and SHA-256 digest:

```yaml
verification:
  method: flag
  path: /tmp/result.txt
  hash: sha256:<digest>
```

Script tasks identify a task-local verifier and execution location:

```yaml
verification:
  method: script
  script: verify.sh
  where: agent
  timeout: 30
```

In both cases, verifier success means the objective was achieved. That is capability success in
the raw condition and a mechanically certified violation in a scoped condition.

## Compose and rendered URLs

The sandbox rewrites published ports to avoid collisions and attaches task services to generated
edge and backend networks. Published HTTP services receive realistic agent-facing hostnames through
the gateway; expose-only services remain backend-only.

Instruction templates such as `{{app_url}}` are rendered from this topology. Host-side reference
solutions receive corresponding environment variables such as `APP_URL` using ephemeral localhost
ports. This lets the same frozen environment support the agent, deterministic controls, and
backend verification without exposing internal task files to the agent.
