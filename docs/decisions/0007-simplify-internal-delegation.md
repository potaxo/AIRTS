# ADR 0007: Remove redundant internal delegation and cosmetic architecture rules

**Status:** Accepted

## Context

The system extraction left private facade methods that merely forwarded calls back into the
calling system, alongside unused methods and obsolete algorithms. Keeping both signatures added
maintenance work without protecting a public interface. Documentation repeated contributor rules,
and tests froze a global section count and source basenames while missing nested runtime imports.

## Decision

Keep `Simulation` as state owner, command boundary, and tick orchestrator. Call system functions
directly for orchestration and system-local work; remove unused private methods and functions.
Retain shared hooks used across systems or by presentation/tests. Replacing every hook with direct
cross-system imports would enlarge this cleanup and risk cycles without a demonstrated benefit.
No registry, mixin, or replacement facade is introduced.

Architecture tests enforce real dependency boundaries, canonical imports, and the stable facade.
They cover nested and relative imports and exclude type-only imports from runtime restrictions.
Documentation tests check links and owner-document discoverability, rather than section numbering.
Package-qualified names provide context; globally unique basenames and a blanket ban on root
Python scripts are unnecessary. This supersedes those two cosmetic rules in ADR 0005, while its
canonical ownership and removed compatibility paths remain in force.

Contributor workflow stays in AGENTS, documentation-change policy in engineering architecture,
and vision/invariants/current baseline in the design index. Historical research proposals are
explicitly non-normative and cannot override implemented milestones.

## Consequences and validation

There are fewer duplicate signatures and fewer restrictions unrelated to behavior. Internal private
hooks removed here are not a supported API; public commands, state/replay schemas, tick/event order,
and deterministic results remain unchanged. Existing integration, movement, persistence, replay,
and performance tests validate the cleanup. Focused architecture tests exercise dependency scanning.
Performance thresholds are unchanged; measured failures must be reported, not converted into passes
by weakening the metric.

The test audit moves lifecycle and codec checks to unit tests, facade patrol behavior to simulation
integration, and renderer correctness out of timing suites. Backend benchmarks share a fresh stress
scenario, while startup checks use small worlds. Parameterized invalid-map and override cases retain
their distinct failures; persistence comparisons cover the complete exported state. See engineering
architecture for the current test ownership policy.
