# AIRTS repository instructions

Read [docs/design.md](docs/design.md) before substantial work, then only the relevant owner docs
and code. Infer scope from the user's request and conversation; do not add roadmap phases.
User instructions take precedence over repository and skill guidance, subject to higher-priority
system/developer instructions and tool permissions.

## Environment

- Use native Windows, PowerShell, Python 3.13, and repository-local `.venv` for project work.
  Follow [README setup](README.md#requirements-and-setup): creating `.venv` with `py -3.13`
  and installing declared task-required dependencies into it are authorized.
  Other global Python/pip use or Conda requires explicit approval.
  These interpreter restrictions apply to local work; the existing Windows CI workflow uses
  its isolated runner's configured Python environment.
- [pyproject.toml](pyproject.toml) owns dependencies. Explain new dependencies before adding them;
  do not install undeclared/speculative packages. Use `pygame-ce`, never install it with `pygame`.

## Workflow

1. Read relevant code/docs and check `git status`. For substantial work, briefly state the plan
   and any actual design conflicts; simple edits need no formal plan.
2. Carry implementation requests through validation; resolve routine choices from context.
   Ask only when information material to correctness/scope cannot be inferred or required
   authorization is absent. Boundaries/types/tests need no approval. Continue independent
   authorized work while awaiting an answer; retain authorization already given in the conversation.
3. Reuse meaningful tests; add coverage only for uncovered behavior. Follow
   [test ownership](docs/architecture/engineering.md#test-ownership).
4. Run required validation. Report the outcome, key decisions, results, and remaining uncertainty
   concisely in the user's language.

If an instruction blocks work, cite its file and relevant wording and explain the concrete
conflict. Distinguish an explicit requirement from an interpretation; do not invent approval gates.

Preserve the public `Simulation` facade, shared command boundary, deterministic headless core,
and [dependency direction](docs/architecture/engineering.md#ownership). Keep changes focused;
no speculative abstractions, silent error swallowing, or fallbacks that conceal failure.
Fix root causes and provide useful diagnostics. For a requested rewrite, define replacement scope
and compatibility/save/replay migration in the owning document as part of the work; ask only for
consequential choices that the request and existing contracts do not resolve.

## Git and outputs

Keep environments, caches, logs, weights, and generated/experiment outputs out of Git.
Preserve existing user and other-agent changes, including untracked files. A dirty worktree alone
is not a blocker; inspect overlapping edits and ask only when they cannot be safely reconciled.
No destructive Git commands. Do not create/change branches or worktrees, commit, push, or open
PRs unless requested. Put validation artifacts under ignored `outputs/` when needed.

## Validation

Choose validation based on this task's changes, not unrelated pre-existing worktree changes.
For coding or test/config changes, run from the repository root:

- `.\.venv\Scripts\python -m ruff check .`
- `.\.venv\Scripts\python -m ruff format --check .`
- `.\.venv\Scripts\python -m mypy src`
- `.\.venv\Scripts\python -m pytest`
- `.\.venv\Scripts\python -m pip check`

Default pytest selection excludes the `performance` marker. Mixed workload tests still run their
full behavior variant, including collision/route operation budgets; hardware and wall-clock gates
run explicitly with `.\.venv\Scripts\python -m pytest -m performance` on the designated native-Windows
acceptance machine. Run this selection when a change affects measured workloads, timing or their
configuration; otherwise report hardware performance as unverified. To run every functional and
performance variant together, use `-m "performance or not performance"`. Selecting a path alone
does not override the default marker filter.

For performance evidence, keep each complete run (including failures) in separate ignored outputs.
For JUnit properties use `-o junit_family=xunit1 --junitxml=outputs/<run-name>.xml`; the parent directory
must exist. Report repeated-run results rather than retrying until one passes or taking only the best.
The performance owner defines gates; generic Windows packaging CI does not certify target hardware.

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
for safe completion.
