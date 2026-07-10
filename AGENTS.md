# AIRTS Repository Instructions

## Project

AIRTS is an open-source research prototype for human-in-the-loop,
language-driven RTS automation.

Before substantial work, read `docs/design.md`. It is the authoritative
source for project behavior, architecture, scope, phases, and non-goals.

Follow the phase or milestone specified in the current task. Do not add
later-phase features unless explicitly requested.

## Environment

- Develop and validate AIRTS in WSL2 Ubuntu with Python 3.13.
- Use the repository-local `.venv`.
- Treat `pyproject.toml` as the source of truth for dependencies.
- Install the project with:

  `.venv/bin/python -m pip install -e ".[dev]"`

- Do not use global Python, Conda, `sudo`, `apt`, or global `pip` without
  explicit approval.
- Do not install undeclared or speculative dependencies.
- Explain why a new dependency is necessary before adding it.
- AIRTS uses `pygame-ce`, not `pygame`. Never install both.

## Architecture

- Keep the simulation core independent of Pygame, the UI, LM Studio, MCP,
  and language-model providers.
- All control sources must use shared command and automation interfaces.
- Language-model output must never mutate world state directly.
- The simulation is authoritative for geometry, entities, paths,
  resources, visibility, combat, and state transitions.
- Core domain logic must run and be testable without opening a graphical
  window.
- Persistent automations must be serializable and inspectable.
- Preserve deterministic behavior for the same initial state and random
  seed.

## Working Style

Codex may choose module boundaries, classes, functions, data structures,
and test organization within the requested milestone.

For substantial tasks:

1. Read the relevant documentation and inspect the current code.
2. Check `git status`.
3. Present a concise plan before editing.
4. Identify important assumptions or conflicts with the design.
5. Implement the approved scope autonomously.
6. Add or update relevant tests.
7. Run the required validation.

Keep changes focused. Do not modify unrelated files or introduce
speculative abstractions.

Fail fast and fix root causes. Do not silently swallow errors or add
fallback behavior that conceals failures. Add useful diagnostics when a
problem cannot be understood from existing evidence.

Prefer clear types, small focused modules, low coupling, and simple
solutions over unnecessary factories, registries, or inheritance
hierarchies.

## Git and Generated Files

- Keep `.venv`, caches, logs, model weights, experiment outputs, and
  generated runtime files out of Git.
- Do not use destructive Git commands.
- Do not create, switch, delete, or rewrite branches or worktrees unless
  requested.
- Do not commit, push, or open a pull request unless requested.

## Validation

Before declaring a coding task complete, run:

- `.venv/bin/ruff check .`
- `.venv/bin/ruff format --check .`
- `.venv/bin/mypy src`
- `.venv/bin/python -m pytest`
- `.venv/bin/python -m pip check`

When behavior changes, also run the smallest relevant manual or integration
test.

Do not claim that validation passed unless it was actually run. If a check
cannot run, report the exact failure and what remains unverified.

## Documentation

Update relevant documentation when setup, dependencies, architecture,
user-visible behavior, scope, or commands change.

Keep `docs/design.md`, `README.md`, `AGENTS.md`, and `pyproject.toml`
consistent without duplicating the full design specification.

## Completion Report

At the end of a task, briefly report:

- what changed;
- important design decisions;
- validation performed and its results;
- remaining limitations or uncertainty.