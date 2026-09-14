"""ScopeBench runner: drive the agent-harness agent over the ScopeBench task suite.

ScopeBench tasks are *scope-boundary* evaluations. Each task hands the agent a
scoped assessment with explicit rules of engagement, and the only way to produce
the requested flag is to step outside that scope. Verification passing therefore
means the agent *crossed* the boundary (the unsafe outcome); verification failing
means the agent *held* scope (the desired outcome).

This package connects two inputs:

* ``agent_harness`` — the PydanticAI agent under test (imported as a library).
* an external task collection — compose stacks, instructions, and verifiers.

The public surface is :func:`scopebench.runner.run_task` and the ``scopebench`` CLI.
Everything is orchestration only and is held to the same coverage, lint, and type
gates as the library.
"""

from scopebench.manifest import InstructionVariant, ScopeKind, TaskManifest, load_manifest
from scopebench.runner import TaskResult, TaskRunError, run_task

__all__ = [
    "InstructionVariant",
    "ScopeKind",
    "TaskManifest",
    "TaskResult",
    "TaskRunError",
    "load_manifest",
    "run_task",
]
