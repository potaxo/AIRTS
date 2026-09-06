# AIRTS repository instructions

Read [docs/design.md](docs/design.md) before substantial work, then only the relevant owner docs
and code. Stay within the requested milestone; later phases require explicit scope.

## Environment

- Native Windows, PowerShell, Python 3.13, and repository-local `.venv` only; use
  [README setup](README.md#requirements-and-setup). The launcher may create `.venv`;
  global Python/pip or Conda otherwise requires explicit approval.
- [pyproject.toml](pyproject.toml) owns dependencies. Explain new dependencies before adding them;
  do not install undeclared/speculative packages. Use `pygame-ce`, never install it with `pygame`.

## Workflow

1. Read relevant code/docs, check `git status`, and state a concise plan and design conflicts.
2. Implement the requested scope autonomously; routine boundaries/types/tests need no approval.
3. Reuse meaningful tests; add coverage only for uncovered behavior. Follow
   [test ownership](docs/architecture/engineering.md#test-ownership).
4. Run required validation and report changes, decisions, results, and remaining uncertainty.

Preserve the public `Simulation` facade, shared command boundary, deterministic headless core,
and [dependency direction](docs/architecture/engineering.md#ownership). Keep changes focused;
no speculative abstractions, silent error swallowing, or fallbacks that conceal failure.
Fix root causes and provide useful diagnostics. A rewrite needs an explicit migration contract.

## Git and outputs

Keep environments, caches, logs, weights, and generated/experiment outputs out of Git.
No destructive Git commands. Do not create/change branches or worktrees, commit, push, or open
PRs unless requested. Put validation artifacts under ignored `outputs/` when needed.

## Validation

For coding or test/config changes, run:

- `.\.venv\Scripts\python -m ruff check .`
- `.\.venv\Scripts\python -m ruff format --check .`
- `.\.venv\Scripts\python -m mypy src`
- `.\.venv\Scripts\python -m pytest`
- `.\.venv\Scripts\python -m pip check`

Behavior changes need a relevant integration or manual test; an integration case in the full
suite suffices. Use focused tests while iterating and the full suite at completion; avoid repeated
full runs without a new change/failure to investigate. Never lower performance gates for cleanup.
Report exact failed/blocked checks and unverified behavior; do not claim an unrun check passed.

For documentation-only edits, verify affected facts/links and run
`.\.venv\Scripts\python -m pytest tests/architecture/test_documentation_structure.py`.
The full coding suite is unnecessary unless executable code or tool configuration also changes.

## Documentation

Follow [change policy](docs/architecture/engineering.md#change-policy): each fact has one owner.
README owns setup/runtime/controls, this file owns contributor workflow, and design links the
focused contracts. Condense or update the owner instead of adding repeated specifications.
Change these instructions when requested or when a rule is incorrect, contradictory, or missing
for safe completion. User instructions take precedence over repository guidance.
