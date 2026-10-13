# Optional features and research

[Design](design.md) describes the implemented baseline, summarized in the 0-5 row below.
Phases 6-10 below retain the existing proposal labels, not a required release sequence. They are
future scope until requested; proposal language must not override current runtime contracts or
the [gameplay priorities](design.md#product-priorities). Research may build on a useful game later.

## Research question and candidate scope

Can cursor-grounded language and inspectable persistent automations reduce RTS control effort
while preserving human control, predictability, and correction? A later study could compare grounded
language with ordinary controls and ungrounded language, or constrained templates with unrestricted
plans. These comparisons are opportunities, not prerequisites for shipping gameplay improvements.

| Phase | Scope |
| --- | --- |
| 0-5 | Existing foundation, world, command/automation runtime, spatial interaction, economy/combat; see [runtime](milestones/runtime.md) |
| 6 | Provider interface, mock + LM Studio adapters, structured proposals, validation, at most one repair attempt, clarification; tests independent of a live model |
| 7 | Language creation/modification using selected entities and geometry, reference resolution, multi-region intent, grounding-conflict handling |
| 8 | Scouting coverage, safe return, observations, uncertain/last-known information, evidence-backed reports |
| 9 | Seeded scenarios, model/control comparisons, exported metrics, human studies |
| 10 | Optional voice, map editor, larger maps/units, MCP/external adapters; larger changes need separate scope |

Provider calls stay asynchronous and outside the tick loop. Tag input snapshots with tick/time and
revalidate returned proposals against current state. Give models only player-available information;
never let unsupported output execute arbitrary code or mutate the world. Explicit selections resolve
vague language; contradictions or materially different interpretations require clarification.
Simple reversible grounded actions should not need repeated confirmation. Voice is input, not a
separate command authority; an editor must use the same validated map format.

## Evaluation and deferred decisions

An optional demonstration could link factory production, selected bridge regions, patrol, requested
repair/return, and language modification while recording inputs, proposals, commands, relevant events,
failures, and outcomes. A study could measure success/time, action and correction counts, invalid proposals,
clarification, latency/token cost, losses/resource efficiency, report factuality, workload, trust,
and perceived control. Seeded scenarios, repeated runs, and visible failure evidence support
comparison without requiring bitwise-identical trajectories or complete event streams.

Current [movement](milestones/movement.md) and [save/replay](milestones/persistence.md) contracts
belong to their focused owners. Further movement research requires explicit scope and comparison on the unchanged
[crowd/FPS workloads](milestones/performance.md). These research references do not prescribe the
current implementation or establish benchmark evidence:
[Continuum Crowds](https://grail.cs.washington.edu/projects/crowd-flows/78-treuille.pdf),
[ORCA](https://gamma-web.iacs.umd.edu/ORCA/), and
[ClearPath](https://gamma-web.iacs.umd.edu/CA/).
Native extensions, workers, GPU simulation, dynamic terrain, multiplayer, and engine migration
follow measured need and the engineering change policy. They are not prerequisites for a game release.
