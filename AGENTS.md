# AIRTS repository instructions

Read [docs/design.md](docs/design.md) before substantial work; use its task index to choose only
the relevant owner document/section and code. Do not load every linked document. Infer scope from
the user's request and conversation; do not add roadmap phases.
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
   [test ownership](docs/architecture/engineering.md#test-ownership). When replacing behavior,
   trace its callers, state, tests and documentation; remove retired paths and descriptions in
   the same change. Verify current callers/contracts before deleting code.
4. Run required validation. Report the outcome, key decisions, results, and remaining uncertainty
   concisely in the user's language.

If an instruction blocks work, cite its file and relevant wording and explain the concrete
conflict. Distinguish an explicit requirement from an interpretation; do not invent approval gates.

Preserve the public `Simulation` facade, shared command boundary, renderer-independent headless core,
and [dependency direction](docs/architecture/engineering.md#ownership). Keep changes focused;
no speculative abstractions, silent error swallowing, or fallbacks that conceal failure.
Follow [product priorities](docs/design.md#product-priorities), the
[engineering change policy](docs/architecture/engineering.md#change-policy), and the current-format-only
[save/replay policy](docs/milestones/persistence.md#supported-formats). Fix root causes and provide useful
diagnostics. Preserve current-format restoration; do not add old-save compatibility or migrations.
Exact replay equality is not a prerequisite for authorized gameplay/performance changes; retain useful
diagnostics and update affected tests under the owner policy. For a rewrite, document replacement scope
and save/replay consequences. Apply the owner's versioning criteria rather than bumping schemas for
every cleanup. Ask only for consequential choices unresolved by the request or existing contracts.

## Git and outputs

Keep environments, caches, logs, weights, and generated/experiment outputs out of Git.
Preserve existing user and other-agent changes, including untracked files. A dirty worktree alone
is not a blocker; inspect overlapping edits and ask only when they cannot be safely reconciled.
No destructive Git commands. Do not create/change branches or worktrees, commit, push, or open
PRs unless requested. Put validation artifacts under ignored `outputs/` when needed.

## Validation

Choose validation from the current task's changes and their impact, not unrelated pre-existing
worktree changes. Use these local requirements; packaging CI may run broader checks.

| Change | Required validation |
| --- | --- |
| Documentation only | Verify affected facts/links and run the documentation structure test below |
| Executable source | Ruff lint/format, mypy, focused tests while iterating, then a complete functional pytest run |
| Tests only | Ruff lint/format and affected tests; run full functional pytest for shared-fixture, collection or selection changes |
| Tool/dependency configuration | Run the affected tools and tests; use full functional pytest when test collection, shared execution or runtime behavior can change |
| Dependency or environment changes | Also run `pip check` after installing/updating declared dependencies |

Command reference, from the repository root (the table determines which apply):

- `.\.venv\Scripts\python -m ruff check .`
- `.\.venv\Scripts\python -m ruff format --check .`
- `.\.venv\Scripts\python -m mypy src`
- `.\.venv\Scripts\python -m pytest`
- `.\.venv\Scripts\python -m pip check`

Batch related edits and focused checks before the final full functional run. After that run,
small corrections may use affected tests; rerun the full suite when changes have cross-system
impact or focused checks cannot bound the risk. State which full run and subsequent targeted
checks cover the delivered changes; a run before later edits does not prove the final tree passed.

Default pytest excludes the `performance` marker. Mixed workload tests still run their complete
behavior variant, including collision/route operation budgets. For hot-path, measured-workload or
timing-configuration changes, and for performance claims, explicitly run the affected timing tests:
`.\.venv\Scripts\python -m pytest tests/performance/<affected-file>.py -m performance`.
Use the complete `-m performance` selection for broad performance impact or full acceptance;
do not run unrelated timing workloads automatically for every local edit. Hardware acceptance
uses native Windows meeting the [performance owner's requirements](docs/milestones/performance.md);
record the tested environment and report unavailable hardware checks as unverified. Packaging CI
does not certify target hardware. To include all variants, use `-m "performance or not performance"`;
selecting a path alone does not override the default marker filter.

Run timing measurements serially, without concurrent resource-heavy validation. Plan a bounded
set of comparable runs (usually two for repeatability); retain every result, not only the best.
Investigate a failure with diagnostics and a comparable baseline where needed. Reuse applicable
evidence while code, workload and environment remain comparable; do not repeatedly reproduce the
same known failure without a new change or hypothesis. A pre-existing failure is not a pass and
does not prove there is no new regression. Fix task-introduced regressions; report unrelated
remaining failures without automatically expanding the task into a separate optimization project.

Keep each complete performance run (including failures) in separate ignored outputs.
For JUnit properties use `-o junit_family=xunit1 --junitxml=outputs/<run-name>.xml`; the parent directory
must exist. The performance owner defines gates; do not change workloads, metrics or thresholds
to make cleanup pass.

Behavior changes need a relevant integration or manual test; an integration case already run
in the full suite suffices. Do not require an extra manual session for the same covered behavior.
Report exact failed/blocked checks and unverified behavior; do not claim an unrun check passed.

For documentation-only edits, verify affected facts/links and run
`.\.venv\Scripts\python -m pytest tests/architecture/test_documentation_structure.py`.
Unrelated dirty source files do not turn a documentation-only task into a coding task.

## Documentation

Follow [change policy](docs/architecture/engineering.md#change-policy): each fact has one owner.
README owns setup/runtime/controls, this file owns contributor workflow, and design links the
focused contracts. Condense or update the owner instead of adding repeated specifications.
Change these instructions when requested or when a rule is incorrect, contradictory, or missing
for safe completion.
