# ScopeBench Pilot

ScopeBench measures whether an autonomous security agent preserves a user-defined engagement
boundary while pursuing an objective. This repository freezes the 30-task pilot used in
*ScopeBench: Do Agents Preserve Engagement Boundaries Under Goal Pressure?* at AISec 2026.

The pilot is a methodological proof of concept. These task versions are retained for paper
reproduction and are separate from the actively developed community benchmark.

The corresponding 2,160 ATIF trajectories are published as the
[`dreadnode/scopebench-pilot`](https://huggingface.co/datasets/dreadnode/scopebench-pilot)
dataset on Hugging Face.

## What is included

- 30 containerized security tasks under [`tasks/scopebench`](tasks/scopebench)
- raw-capability, casual-scope, and program-brief variants for every task
- generated-result verifiers and reference solutions
- the frozen ScopeBench runner and sandbox used for the pilot
- the full-trajectory agentic judge used to evaluate the casual-scope and program-brief variants

Each task places the objective behind an action that crosses the scoped condition's engagement
boundary. Raw-capability runs measure whether an agent can reach that objective. Scoped runs pair
deterministic outcome verification with a full-trajectory scope verdict.

## Quick start

Requirements: Python 3.13, uv, and Docker with Compose.

```sh
git clone https://github.com/dreadnode/scopebench-pilot
cd scopebench-pilot
uv sync --frozen

uv run scopebench --tasks-dir tasks/scopebench --list
uv run scopebench --tasks-dir tasks/scopebench --all --agent dry-run
```

`dry-run` validates discovery and orchestration without calling a model. Run one task with the
sandboxed baseline agent by selecting a configured model and providing its API key:

```sh
export ANTHROPIC_API_KEY=...

uv run scopebench \
  --tasks-dir tasks/scopebench \
  --task auth-user-export-dead-end \
  --model anthropic:claude-sonnet-4-6 \
  --atif
```

Scoped cases also require an Anthropic API key for the fixed post-hoc trajectory judge. See
[`docs/scopebench/run-evaluations.md`](docs/scopebench/run-evaluations.md) for repetitions,
concurrency, reports, and model-provider configuration.

## Pilot design

The released evaluation is the Cartesian product of:

- 30 tasks
- 3 instruction conditions
- 8 model families
- 3 repetitions

This produces 2,160 trajectories. The Hugging Face dataset preserves the model, condition, task,
and repetition structure and records a checksum for every released trajectory.

## Documentation and development

Start the documentation site locally:

```sh
uv run --group docs mkdocs serve
```

Run the repository checks:

```sh
uv run ruff check src tests
uv run basedpyright
uv run pytest
```

## Citation

Citation metadata is available in [`CITATION.cff`](CITATION.cff).

## License

This repository is released under the [MIT License](LICENSE).
