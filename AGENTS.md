# AIRTS Repository Instructions

## Project

AIRTS is an open-source research prototype for human-in-the-loop,
language-driven RTS automation.

Before substantial work, read `docs/design.md`. It is the authoritative
source for project behavior, architecture, scope, phases, and non-goals.

Follow the phase or milestone specified in the current task. Do not add
later-phase features unless explicitly requested.

## Environment

- Develop and validate AIRTS natively on Windows with Python 3.13 and PowerShell.
- Use the repository-local `.venv` for development and validation. Follow
  [README setup](README.md#requirements-and-setup) to create it and install the project.
- Treat `pyproject.toml` as the source of truth for dependencies.
- The Python launcher may create `.venv` as documented in README. Otherwise,
  do not use global Python, Conda, or global `pip` without explicit approval.
- Do not install undeclared or speculative dependencies.
- Explain why a new dependency is necessary before adding it.
- AIRTS uses `pygame-ce`, not `pygame`. Never install both.

## Architecture

Follow the invariants and module ownership in [docs/design.md](docs/design.md) and
[engineering architecture](docs/architecture/engineering.md). Preserve the public `Simulation`
facade, deterministic behavior, shared command boundary, and headless simulation core.

## Working Style

Codex may choose module boundaries, classes, functions, data structures,
and test organization within the requested milestone.

For substantial tasks:

1. Read the relevant documentation and inspect the current code.
2. Check `git status`.
3. Present a concise plan before editing.
4. Identify important assumptions or conflicts with the design.
5. Implement the requested scope autonomously; routine implementation choices need no extra approval.
6. Reuse existing tests when they cover the change; add or update tests for uncovered behavior.
7. Run the required validation.

Keep changes focused. Do not modify unrelated files or introduce
speculative abstractions.

Fail fast and fix root causes. Do not silently swallow errors or add
fallback behavior that conceals failures. Add useful diagnostics when a
problem cannot be understood from existing evidence.

## Git and Generated Files

- Keep `.venv`, caches, logs, model weights, experiment outputs, and
  generated runtime files out of Git.
- Do not use destructive Git commands.
- Do not create, switch, delete, or rewrite branches or worktrees unless
  requested.
- Do not commit, push, or open a pull request unless requested.

## Validation

Before declaring a coding task complete, run:

- `.\.venv\Scripts\python -m ruff check .`
- `.\.venv\Scripts\python -m ruff format --check .`
- `.\.venv\Scripts\python -m mypy src`
- `.\.venv\Scripts\python -m pytest`
- `.\.venv\Scripts\python -m pip check`

When behavior changes, ensure a relevant integration or manual test exercises it.
An integration test included in the full suite satisfies this requirement.

For documentation-only changes, check affected links and consistency with their authoritative
sources; run the documentation tests. The full coding suite is not required unless executable
code or tool configuration also changes. Follow the
[test ownership policy](docs/architecture/engineering.md#313-test-ownership-and-fixture-scope)
when adding, merging, or reorganizing tests.

Do not claim that validation passed unless it was actually run. If a check
cannot run, report the exact failure and what remains unverified.

## Documentation

Follow the [engineering change policy](docs/architecture/engineering.md#31-engineering-change-policy).
Keep README focused on setup, runtime, controls, and entry points; keep dependencies in
`pyproject.toml` and contributor workflow here. Link to authoritative facts instead of repeating
whole specifications. Change these instructions when requested or when a rule is incorrect,
contradictory, or missing for safe completion of the task.

## Completion Report

At the end of a task, briefly report:

- what changed;
- important design decisions;
- validation performed and its results;
- remaining limitations or uncertainty.
