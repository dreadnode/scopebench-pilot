"""The ``bash`` tool: run a shell command in a subprocess."""

from pydantic_ai import ModelRetry, RunContext

from agent_harness._edit import staleness_notes
from agent_harness._fs import clip_output
from agent_harness._subprocess import run
from agent_harness.deps import HarnessDeps

_DEFAULT_TIMEOUT = 120


def bash(
    ctx: RunContext[HarnessDeps],
    cmd: str,
    timeout: int = _DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> str:
    """Execute a bash command in a subprocess.

    Use for shell commands, scripts, or operations requiring shell features. The
    command runs relative to the session working directory and its stdout and
    stderr are returned merged. A non-zero exit is a normal result (``grep`` with
    no match, a failing test run, ``diff``), so the output is returned with an
    ``[exit N]`` marker appended rather than raised — the model reads the code and
    decides what to do next. If the command changes files already read this run,
    the output ends with a note naming them; read a changed file again before
    editing it.

    Args:
        ctx: The tool run context (injected by the runtime).
        cmd: The command to run.
        timeout: Maximum seconds to allow before the command is killed.
        env: Extra environment variables to set for the command.
        input: Text to write to the command's standard input.

    Returns:
        The merged stdout and stderr, with ``[exit N]`` appended on a non-zero
        exit and notes naming any read files the command changed.

    Raises:
        ModelRetry: If the command times out (no output to return).
    """
    try:
        result = run(
            ["/bin/bash", "-c", cmd],
            cwd=ctx.deps.cwd,
            timeout=timeout,
            env=env,
            input_text=input,
            merge_stderr=True,
        )
    except TimeoutError as exc:
        raise ModelRetry(str(exc)) from exc
    stdout = clip_output(result.stdout)
    if result.returncode != 0:
        marker = f"[exit {result.returncode}]"
        stdout = f"{stdout.rstrip()}\n{marker}" if stdout.strip() else marker
    notes = staleness_notes(ctx.deps)
    if not notes:
        return stdout
    joined = "\n".join(notes)
    return f"{stdout}\n{joined}" if stdout else joined
