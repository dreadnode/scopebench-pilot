"""The ``python`` tool: run Python code in a subprocess."""

import sys

from pydantic_ai import ModelRetry, RunContext

from agent_harness._edit import staleness_notes
from agent_harness._fs import clip_output
from agent_harness._subprocess import run
from agent_harness.deps import HarnessDeps

_DEFAULT_TIMEOUT = 120


def python(
    ctx: RunContext[HarnessDeps],
    code: str,
    timeout: int = _DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> str:
    """Execute Python code in a subprocess.

    The code runs with the current interpreter and relative to the session
    working directory. Results must be printed to stdout to be captured. Standard
    error is returned in a labelled ``[stderr]`` section (so tracebacks and
    ``logging`` output are visible), and a non-zero exit appends an ``[exit N]``
    marker rather than raising — the model reads the outcome and decides. If the
    code changes files already read this run, the output ends with a note naming
    them; read a changed file again before editing it.

    Args:
        ctx: The tool run context (injected by the runtime).
        code: The Python source to execute.
        timeout: Maximum seconds to allow before the code is killed.
        env: Extra environment variables to set for the subprocess.

    Returns:
        Standard output, plus a ``[stderr]`` section and ``[exit N]`` marker when
        those are non-empty / non-zero, and notes naming any read files the code
        changed.

    Raises:
        ModelRetry: If the code times out (no output to return).
    """
    try:
        result = run(
            [sys.executable, "-"],
            cwd=ctx.deps.cwd,
            timeout=timeout,
            env=env,
            input_text=code,
            merge_stderr=False,
        )
    except TimeoutError as exc:
        raise ModelRetry(str(exc)) from exc
    sections: list[str] = []
    if result.stdout:
        sections.append(clip_output(result.stdout.rstrip("\n")))
    if result.stderr.strip():
        sections.append(f"[stderr]\n{clip_output(result.stderr.rstrip())}")
    if result.returncode != 0:
        sections.append(f"[exit {result.returncode}]")
    sections.extend(staleness_notes(ctx.deps))
    return "\n".join(sections)
