"""Command-line runner for the ScopeBench suite.

Grading and reporting go through pydantic_evals (see :mod:`scopebench.evals`): each
task/instruction pair becomes a Case, ``run_task`` is the eval task function, and the
declared scope condition selects capability or adherence grading. Scoped harness cases
also run the fixed Sonnet 5 trajectory judge automatically.

Examples::

    # List the tasks the runner can see
    scopebench --list --tasks-dir /path/to/scopebench-tasks

    # Run the real agent-harness agent over the entire collection (the default strategy)
    scopebench --all

    # Smoke-test the whole pipeline with no API key (reference solutions)
    scopebench --all --agent solution

    # Run every task/instruction case 3 times and save a JSON report
    scopebench --all --repeat 3 --json report.json

(The tasks directory comes from ``--tasks-dir`` or ``SCOPEBENCH_TASKS_DIR`` — set it once in
``.env``; there is no built-in default.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from agent_harness import HarnessSettings
from agent_sandbox import ImageError, ensure_image
from agent_sandbox.models import (
    SANDBOX_PROVIDERS,
    api_key_env,
    format_model_listing,
    is_supported,
    provider_of,
    read_api_key,
    resolve_model,
)
from scopebench.evals import configure_logfire, report_to_json, run_evals
from scopebench.manifest import TaskManifest, discover_tasks, load_manifest
from scopebench.runner import AgentKind, RunOptions


def _default_tasks_dir() -> Path | None:
    """The tasks dir from ``SCOPEBENCH_TASKS_DIR``, if set (there is no built-in default).

    The path is not validated here — ``main`` errors on a missing directory, so a
    misconfigured env var surfaces instead of being silently ignored.
    """
    env = os.environ.get("SCOPEBENCH_TASKS_DIR")
    return Path(env) if env else None


@dataclass
class _Args(argparse.Namespace):
    """Typed argparse namespace (fields are the argparse dest names).

    Passed to ``parse_args(namespace=_Args())`` so parsed values come back fully
    typed with no cast. The defaults live here rather than on ``add_argument``:
    argparse leaves an attribute the namespace already has alone, so a
    pre-populated namespace is the single source of truth for defaults.
    """

    tasks_dir: Path | None = field(default_factory=_default_tasks_dir)
    task: list[str] = field(default_factory=list)
    all: bool = False
    list: bool = False
    list_models: bool = False
    agent: AgentKind = "harness"
    model: str | None = None
    request_limit: int = 40
    timeout: float = 600.0
    wait_timeout: int = 180
    keep_up: bool = False
    repeat: int = 1
    concurrency: int = 1
    logfire: bool = False
    json: Path | None = None
    atif: bool = False
    trajectory_judge: bool = True
    scopejudge: bool = False
    workspace_root: Path = field(default_factory=lambda: Path.cwd() / ".scopebench-runs")


def _load_dotenv(root: Path) -> None:
    """Populate ``os.environ`` from ``root/.env`` using standard dotenv syntax.

    Only keys not already set in the environment are applied, so real env vars
    always win. Silently does nothing if there is no ``.env``.
    """
    _ = load_dotenv(dotenv_path=root / ".env", override=False)


def _select_tasks(args: _Args, tasks_dir: Path) -> list[TaskManifest]:
    """Resolve the requested tasks from ``--all`` / ``--task`` selectors."""
    if args.all:
        return discover_tasks(tasks_dir)
    manifests: list[TaskManifest] = []
    for name in args.task:
        task_dir = tasks_dir / name
        if not task_dir.is_dir():
            raise SystemExit(f"error: no such task: {name} (looked in {tasks_dir})")
        manifests.append(load_manifest(task_dir))
    return manifests


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scopebench",
        description="Run the agent-harness agent over the ScopeBench scope-boundary tasks.",
        epilog=(
            "agent strategies (--agent):\n"
            "  harness   the real agent-harness agent, sealed in the sandbox container\n"
            "            (default; needs the model provider's API key, e.g. ANTHROPIC_API_KEY)\n"
            "  solution  run the task's reference solution.sh as a stand-in agent; it\n"
            "            should produce the flag, smoke-testing the whole pipeline\n"
            "            (compose + render + verify) without a model or API key\n"
            "  dry-run   bring the stack up, render the instruction, and check that the\n"
            "            verifier does NOT already pass on the clean workspace; runs no\n"
            "            agent at all — for validating a task's plumbing when authoring\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Defaults are declared once on `_Args`, not here — see its docstring.
    _ = parser.add_argument(
        "--tasks-dir",
        type=Path,
        help="Directory of task dirs (required unless SCOPEBENCH_TASKS_DIR is set).",
    )
    selector = parser.add_mutually_exclusive_group()
    _ = selector.add_argument(
        "--task",
        action="append",
        metavar="SLUG",
        help="Task directory name to run (repeatable).",
    )
    _ = selector.add_argument("--all", action="store_true", help="Run every discovered task.")
    _ = parser.add_argument("--list", action="store_true", help="List discovered tasks and exit.")
    _ = parser.add_argument(
        "--list-models",
        action="store_true",
        help="List sandbox-supported model slugs (with API-key status) and exit.",
    )
    _ = parser.add_argument(
        "--agent",
        choices=["harness", "solution", "dry-run"],
        help="Agent strategy (default: harness — the real agent; see the list below).",
    )
    _ = parser.add_argument(
        "--model", help="Per-run model override (provider:model; see --list-models)."
    )
    _ = parser.add_argument("--request-limit", type=int, help="Max model requests per run.")
    _ = parser.add_argument("--timeout", type=float, help="Per-task agent time budget (s).")
    _ = parser.add_argument("--wait-timeout", type=int, help="Compose health wait (s).")
    _ = parser.add_argument(
        "--keep-up",
        action="store_true",
        help="Leave successfully started stacks running after each task.",
    )
    _ = parser.add_argument(
        "--repeat", type=int, help="Run each task N times (agent runs are stochastic)."
    )
    _ = parser.add_argument(
        "--concurrency",
        type=int,
        help="Max task/instruction runs in parallel (harness strategy only; default 1).",
    )
    _ = parser.add_argument(
        "--logfire",
        action="store_true",
        help="Force-enable Logfire tracing (otherwise auto-on when LOGFIRE_TOKEN is set).",
    )
    _ = parser.add_argument(
        "--json", type=Path, metavar="PATH", help="Write a JSON report to PATH."
    )
    _ = parser.add_argument(
        "--atif",
        action="store_true",
        help=(
            "Preserve an ATIF v1.7 trajectory.json for raw harness cases too; "
            "scoped judged cases capture it automatically."
        ),
    )
    _ = parser.add_argument(
        "--trajectory-judge",
        action=argparse.BooleanOptionalAction,
        help=("Run the Sonnet 5 full-trajectory judge on scoped harness cases (default: enabled)."),
    )
    _ = parser.add_argument(
        "--scopejudge",
        action="store_true",
        help="Enable the ScopeJudge post-tool capability inside the harness container.",
    )
    _ = parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Where per-task agent workspaces are created.",
    )
    return parser


def _parse_args(argv: list[str] | None) -> _Args:
    """Parse ``argv`` into the typed :class:`_Args` namespace (no cast needed)."""
    return _build_parser().parse_args(argv, namespace=_Args())


def _unsupported_model_error(model: str) -> str:
    """The exit-2 message for a model string the sandbox cannot run."""
    supported = ", ".join(SANDBOX_PROVIDERS)
    if ":" not in model:
        return (
            f"error: invalid model '{model}': model strings take the form provider:model "
            + f"(e.g. {HarnessSettings().model}). Run --list-models for the available slugs."
        )
    provider = provider_of(model)
    hint = (
        "for GPT models use the 'openai:' prefix — the Responses API"
        if provider == "openai-chat"
        else f"the sandbox image ships only the {supported} SDKs"
    )
    return (
        f"error: model '{model}' uses provider '{provider}', which the sandbox does not "
        + f"support ({hint}).\nSupported providers: {supported}. "
        + "Run --list-models for the available slugs."
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    _load_dotenv(Path.cwd())
    args = _parse_args(argv)

    if args.list_models:
        print(format_model_listing())
        return 0

    if args.tasks_dir is None:
        print(
            "error: no tasks directory given.\n"
            + "Pass --tasks-dir /path/to/scopebench-tasks "
            + "or set SCOPEBENCH_TASKS_DIR (e.g. in .env).",
            file=sys.stderr,
        )
        return 2
    if not args.tasks_dir.is_dir():
        print(f"error: tasks directory not found: {args.tasks_dir}", file=sys.stderr)
        return 2

    if args.list:
        for manifest in discover_tasks(args.tasks_dir):
            method = manifest.verification.method
            prompt_count = len(manifest.instruction_variants)
            prompt_label = "prompt" if prompt_count == 1 else "prompts"
            print(f"{manifest.slug:<44} [{method}, {prompt_count} {prompt_label}]  {manifest.name}")
        return 0

    if not args.all and not args.task:
        print("error: select tasks with --all or --task SLUG (or use --list).", file=sys.stderr)
        return 2

    manifests = _select_tasks(args, args.tasks_dir)
    if args.agent == "harness":
        effective_model = resolve_model(args.model)
        if not is_supported(effective_model):
            print(_unsupported_model_error(effective_model), file=sys.stderr)
            return 2
        if read_api_key(effective_model) is None:
            print(
                f"error: model '{effective_model}' needs "
                + f"{api_key_env(provider_of(effective_model))} (set it in the env "
                + "or in .env in the current directory). To smoke-test without a key, "
                + "use --agent solution.",
                file=sys.stderr,
            )
            return 2
        has_scoped_cases = any(
            variant.scope == "scoped"
            for manifest in manifests
            for variant in manifest.instruction_variants
        )
        if (
            args.trajectory_judge
            and has_scoped_cases
            and read_api_key("anthropic:claude-sonnet-5") is None
        ):
            print(
                "error: automatic scoped trajectory judging needs ANTHROPIC_API_KEY. "
                + "Set it in the env or .env, or pass --no-trajectory-judge.",
                file=sys.stderr,
            )
            return 2

    if args.atif and args.agent != "harness":
        print(
            "warning: --atif only produces trajectories with --agent harness; ignoring it.",
            file=sys.stderr,
        )

    if args.scopejudge and args.agent != "harness":
        print(
            f"warning: --scopejudge only applies to --agent harness; ignoring it for {args.agent}.",
            file=sys.stderr,
        )

    if args.concurrency > 1 and args.agent != "harness":
        print(
            f"warning: --concurrency {args.concurrency} is ignored for --agent {args.agent}; "
            + "only the harness strategy isolates runs (others share host /tmp and stay serial).",
            file=sys.stderr,
        )

    if args.agent == "harness":
        # Build the sandbox image once, up front, so a build failure surfaces immediately (and
        # per-task runs reuse it rather than racing to rebuild under --concurrency).
        try:
            _ = ensure_image()
        except (ImageError, OSError) as exc:
            print(
                f"error: could not build the agent sandbox image (is Docker running?):\n{exc}",
                file=sys.stderr,
            )
            return 2

    options = RunOptions(
        agent=args.agent,
        model=args.model,
        request_limit=args.request_limit,
        timeout=args.timeout,
        wait_timeout=args.wait_timeout,
        keep_up=args.keep_up,
        workspace_root=args.workspace_root,
        concurrency=args.concurrency,
        atif=args.atif,
        trajectory_judge=args.trajectory_judge,
        scopejudge=args.scopejudge,
    )

    configure_logfire(args.logfire)
    report = asyncio.run(run_evals(manifests, options, repeat=args.repeat, progress=not args.json))
    report.print(include_input=False, include_reasons=True, include_durations=True)
    print(
        "\nRaw assertion pass = objective completed (capable). "
        + "Scoped assertion pass = objective not completed (no mechanically certified violation).",
    )

    if args.json:
        _ = args.json.write_text(json.dumps(report_to_json(report), indent=2), encoding="utf-8")
        print(f"Wrote JSON report to {args.json}")

    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
