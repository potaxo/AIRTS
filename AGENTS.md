# AIRTS Repository Instructions

## Project purpose

AIRTS is an open-source research prototype for human-in-the-loop,
language-driven RTS automation.

Before planning or implementing any nontrivial feature, read
`docs/design.md`. Treat it as the authoritative source for:

- project behavior;
- research goals;
- architecture;
- scope;
- development phases;
- explicit non-goals.

The current implementation target is Phase 1 unless the user explicitly
requests another phase.

Do not copy the entire design specification into this file.

## Repository structure

Important paths:

- `docs/design.md`: product and architecture specification
- `src/airts/`: application source code
- `tests/`: automated tests
- `examples/`: example maps and scenarios
- `scripts/`: development and experiment scripts
- `pyproject.toml`: dependencies and Python tool configuration

## Development environment

- Develop and validate AIRTS in WSL2 Ubuntu.
- Use Python 3.13.
- Use the repository-local `.venv`.
- `pyproject.toml` is the authoritative dependency declaration.
- Install the project with `.venv/bin/python -m pip install -e ".[dev]"`.
- Prefer explicit `.venv/bin/...` commands in automated scripts.
- Do not depend on shell activation inside scripts.
- Do not use global Python packages.
- Do not use global Conda environments.
- Do not run `sudo`, `apt`, or global `pip` unless the user explicitly
  approves it.
- Do not install undeclared or speculative packages.
- Before adding a dependency, explain why the standard library and
  existing dependencies are insufficient.
- Never install both `pygame` and `pygame-ce`. AIRTS uses `pygame-ce`.

## Architectural invariants

- The simulation core must not depend on Pygame.
- The simulation core must not depend on the graphical UI.
- The simulation core must not depend on LM Studio, MCP, or any language
  model provider.
- UI and integration layers may depend on the simulation core, but the
  simulation core must not depend on them.
- All human, scripted, UI, and future AI inputs must use shared command
  and automation interfaces.
- Language-model output must never mutate world state directly.
- Exact spatial geometry must come from player input or deterministic
  game logic.
- The simulation is authoritative for entity existence, movement,
  pathfinding, resources, visibility, combat, and state transitions.
- Persistent automations must be serializable and inspectable.
- Domain logic must be runnable and testable without opening a graphical
  window.
- Preserve deterministic behavior for the same initial state and random
  seed.
- Do not introduce later-phase features into the current milestone
  without explicit approval.

## Engineering principles

- Fail fast.
- Do not silently swallow exceptions.
- Do not add fallback behavior that conceals a real failure.
- Fix root causes rather than adding narrow symptom-level patches.
- When available evidence is insufficient, add logging, assertions,
  tests, or a reproducible case.
- Do not claim a problem is fixed without verification.
- Important command, automation, and state transitions must be
  observable.
- Avoid broad `except Exception` handlers unless meaningful context is
  added and the exception is re-raised.
- Prefer explicit types, dataclasses, enums, and focused modules.
- Use classes for domain concepts with identity or state.
- Use functions for stateless transformations and calculations.
- Avoid factories, registries, inheritance hierarchies, and speculative
  abstractions unless current complexity clearly requires them.
- Prefer low coupling and clear dependency direction.
- Do not fabricate benchmark results, experiment outcomes, test results,
  or validation evidence.

## Codex autonomy

Codex may choose internal implementation details within the requested
milestone, including:

- module boundaries;
- classes and functions;
- internal data structures;
- helper utilities;
- test organization.

Before implementing a substantial milestone, Codex must explain its
proposed architecture and identify important assumptions.

Do not ask the user to decide every class or helper method when the
decision can be made safely from `docs/design.md` and existing code.

## Working procedure

Before editing:

1. Read `AGENTS.md`.
2. Read relevant sections of `docs/design.md`.
3. Inspect the current repository and tests.
4. Run `git status`.
5. For a nontrivial task, present a concise implementation plan.
6. Report assumptions or conflicts with the design before coding.

During implementation:

- Keep changes within the requested milestone.
- Do not modify unrelated files.
- Preserve existing public behavior unless the task intentionally changes
  it.
- Add or update tests alongside domain behavior.
- Add structured diagnostics for important failure paths.
- Keep generated files, caches, logs, model weights, experiment outputs,
  and `.venv` out of Git.
- Do not create, switch, delete, or rewrite branches or worktrees unless
  explicitly requested.
- Do not commit, push, or open a pull request unless explicitly requested.
- Do not use destructive Git commands without explicit approval.

## Required validation

Run the relevant checks before declaring a coding task complete:

- `.venv/bin/ruff check .`
- `.venv/bin/ruff format --check .`
- `.venv/bin/mypy src`
- `.venv/bin/python -m pytest`
- `.venv/bin/python -m pip check`

When application behavior is affected, also run the smallest relevant
manual or integration test.

Do not claim that validation passed unless the commands were actually
executed.

If validation cannot run, report:

- the exact command;
- the complete failure;
- whether the failure is caused by the environment or implementation;
- what remains unverified.

## Documentation discipline

Update documentation in the same change when any of these change:

- setup;
- commands;
- dependencies;
- architecture;
- user-visible behavior;
- assumptions;
- project scope;
- development phase.

Keep these files mutually consistent:

- `docs/design.md`
- `AGENTS.md`
- `README.md`
- `pyproject.toml`

When Codex repeatedly makes the same mistake, propose a concise update to
the nearest relevant `AGENTS.md`.

## Completion report

At the end of each task, report:

1. what changed;
2. why it changed;
3. which files changed;
4. which validation commands were run;
5. the actual results;
6. remaining uncertainty;
7. deliberately deferred work.
