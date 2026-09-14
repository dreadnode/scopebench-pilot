---
description: Reproduce the frozen ScopeBench pilot released with the AISec 2026 paper.
---

# ScopeBench pilot

This repository freezes the benchmark tasks, runner, sandbox, verifiers, and trajectory judge used
for *ScopeBench: Do Agents Preserve Engagement Boundaries Under Goal Pressure?* It is a
methodological pilot retained for paper reproduction, separate from the evolving community
benchmark.

The pilot contains:

| Component | Count |
|---|---:|
| Dockerized security tasks | 30 |
| Instruction conditions per task | 3 |
| Acting-model families | 8 |
| Repetitions per model/task/condition cell | 3 |
| Released ATIF trajectories | 2,160 |

The benchmark code and frozen tasks live in this repository. The corresponding trajectories are
in the [`dreadnode/scopebench-pilot`](https://huggingface.co/datasets/dreadnode/scopebench-pilot)
Hugging Face dataset.

## Start here

- [Pilot benchmark](scopebench/index.md) explains the experimental unit and runner workflow.
- [Quickstart](scopebench/quickstart.md) installs the frozen environment and exercises the bundled
  tasks.
- [Run evaluations](scopebench/run-evaluations.md) records the paper settings and full-suite
  command.
- [ScopeBench grading](explanation/scopebench-grading.md) explains the mechanical and trajectory
  arms.
- [Inspect results](scopebench/results.md) describes reports and per-run artifacts.

## Result direction

Raw-capability assertion **pass** means the agent completed the objective. Scoped assertion
**failure** means the same mechanical verifier certified that the agent reached an objective that
required crossing the stated boundary. Runner and infrastructure failures are ungraded and remain
separate from both outcomes.
