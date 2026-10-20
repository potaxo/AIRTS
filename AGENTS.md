# AIRTS repository instructions

Before substantial work, read [docs/design.md](docs/design.md), then only task-relevant owners,
sections and code. Infer scope from the request and conversation; do not add roadmap phases.
User instructions override repository/skill guidance, subject to system/developer instructions
and tool permissions.

## Environment

- Work locally with native Windows, PowerShell, Python 3.13 and repository `.venv`.
  [README setup](README.md#requirements-and-setup) authorizes `py -3.13` to create `.venv` and
  installation of declared task-required dependencies there. Other global Python/pip or Conda
  requires explicit approval. Windows CI uses its isolated runner's configured Python.
- [pyproject.toml](pyproject.toml) owns dependencies: explain additions first; do not install
  undeclared/speculative packages. Use `pygame-ce`, never install `pygame` alongside it.

## Workflow

1. Read relevant code/docs and check `git status`. Briefly plan substantial work and identify actual
   design conflicts; simple edits need no formal plan.
2. Complete implementation through validation. Resolve routine choices from context, including
   boundaries/types/tests. Ask only for missing authorization or consequential correctness/scope
   choices unresolved by the conversation/contracts. Retain prior authorization and continue
   independent authorized work while awaiting answers.
3. Follow [test ownership](docs/architecture/engineering.md#test-ownership): reuse meaningful tests;
   add only missing coverage. Before replacing/deleting behavior, trace current callers, contracts,
   state, tests and docs; remove retired paths/descriptions in the same change.
4. Report results, key decisions, exact failed/blocked checks and unverified scope concisely in the
   user's language. Never claim an unrun check passed.

If blocked by an instruction, cite its file/wording and concrete conflict; distinguish requirements
from interpretation. Do not invent approval gates.

Preserve the public `Simulation` facade, shared command boundary, renderer-independent headless core
and [dependency direction](docs/architecture/engineering.md#ownership). Follow
[product priorities](docs/design.md#product-priorities) and
[change policy](docs/architecture/engineering.md#change-policy). Keep changes focused, fix root causes
and provide diagnostics; no speculative abstractions, silent error swallowing or concealing fallbacks.
Under [save/replay policy](docs/milestones/persistence.md#supported-formats), preserve current-format
restoration without old-save compatibility/migrations. Exact replay equality does not gate authorized
gameplay/performance changes; retain diagnostics and update affected tests. Document rewrite scope and
save/replay consequences; apply owner versioning criteria, not schema bumps for every cleanup.

## Git and outputs

Keep environments, caches, logs, weights and generated/experiment outputs out of Git; put validation
artifacts under ignored `outputs/`. Preserve user/other-agent changes, including untracked files.
A dirty tree is not a blocker: inspect overlaps and ask only if they cannot be safely reconciled.
No destructive Git commands; no branch/worktree creation or changes, commits, pushes or PRs unless
requested.

## Validation

Choose the smallest selection covering this task's changed behavior, callers and affected contracts,
including relevant integration/workload coverage. Ignore unrelated dirty files; source edits alone
do not require a full functional run. Packaging CI may run broader checks.

Run full functional pytest only when the user requests it, for full/release acceptance, or when shared
fixtures, collection/selection, execution or cross-system invariants can affect unselected systems
and focused checks cannot bound the risk. A shared module alone is insufficient. Explain affected
contracts before expanding validation.

| Change | Required validation |
| --- | --- |
| Documentation only | Verify affected facts/links and run the documentation structure test below |
| Executable source | Ruff lint/format, mypy and affected unit/integration/workload behavior tests |
| Tests only | Ruff lint/format and affected tests |
| Tool/dependency configuration | Affected tools and tests |
| Dependency or environment changes | Also `pip check` after installing/updating declared dependencies |

Ordinary local functional/scenario selections use `pytest-xdist`: `-n 4 --dist worksteal` on this
12-core Windows host; use fewer workers on smaller hosts. Parallelism never expands test scope.
Use `-n 0` for small unit-only selections, documentation, interactive GUI scenarios and **any selection
containing `performance`-marked timing tests**, including all-variant acceptance. Keep worker options
explicit, outside global pytest `addopts`, to prevent implicit parallel timing runs.

Commands from the repository root (apply the table and scope rules):

- `.\.venv\Scripts\python -m ruff check .`
- `.\.venv\Scripts\python -m ruff format --check .`
- `.\.venv\Scripts\python -m mypy src`
- `.\.venv\Scripts\python -m pytest -n 4 --dist worksteal <affected-paths>`
- `.\.venv\Scripts\python -m pip check`

Omit `<affected-paths>` only for a required full run. Documentation check:
`.\.venv\Scripts\python -m pytest -n 0 tests/architecture/test_documentation_structure.py`.

Batch related edits and focused checks before final validation; finish known corrections before a
full run. Reuse evidence only while code, workloads and environment remain applicable. Later small
corrections use affected tests; repeat full runs only under the criteria above. Report which runs
cover delivered changes; earlier results alone do not prove a later tree passed. Behavior changes
need integration or manual coverage; a relevant passing integration case suffices without a duplicate
manual session.

Default pytest excludes `performance`, even with an explicit path. Mixed workloads retain their
complete behavior variant, including collision/route operation budgets. For hot-path,
measured-workload or timing-configuration changes, and performance claims, run affected timing tests:
`.\.venv\Scripts\python -m pytest -n 0 tests/performance/<affected-file>.py -m performance`.
Use complete `-m performance` selection for broad performance impact or full acceptance; otherwise
exclude unrelated timing workloads. All variants use `-m "performance or not performance"`.
Hardware acceptance requires native Windows meeting the
[performance owner](docs/milestones/performance.md)'s requirements. Record the environment and mark
unavailable hardware checks unverified; packaging CI does not certify target hardware.

Run timing measurements serially without concurrent resource-heavy validation. Plan bounded,
comparable runs (usually two); retain each complete run, including failures, in separate ignored outputs.
Diagnose failures with a comparable baseline where needed; rerun known failures only for a new change
or hypothesis. A pre-existing failure is neither a pass nor proof of no new regression. Fix introduced
regressions; report unrelated failures without starting a separate optimization project.

For JUnit properties, use `-o junit_family=xunit1 --junitxml=outputs/<run-name>.xml` after creating the
parent directory. The performance owner defines gates; never change workloads, metrics or thresholds
to make cleanup pass.

## Documentation

Each fact has one owner: README owns setup/runtime/controls, this file contributor workflow, and
design indexes focused contracts. Update/condense the owner instead of duplicating specifications.
Revise these instructions when requested or when incorrect, contradictory or missing for safe work.
