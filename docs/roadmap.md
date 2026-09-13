# Research roadmap

[Design](design.md) describes the implemented baseline, summarized in the 0-5 row below.
Phases 6-10 and the deferred proposals are future scope until explicitly requested; proposal
language must not override current runtime contracts.

## Question and sequence

Can cursor-grounded language and inspectable persistent automations reduce RTS control effort
while preserving human control, predictability, and correction? Compare grounded language with
ordinary controls and ungrounded language; compare constrained templates with unrestricted plans.

| Phase | Scope |
| --- | --- |
| 0-5 | Existing foundation, world, command/automation runtime, spatial interaction, economy/combat; see [runtime](milestones/runtime.md) |
| 6 | Provider interface, mock + LM Studio adapters, structured proposals, validation, at most one repair attempt, clarification; tests independent of a live model |
| 7 | Language creation/modification using selected entities and geometry, reference resolution, multi-region intent, grounding-conflict handling |
| 8 | Scouting coverage, safe return, observations, uncertain/last-known information, evidence-backed reports |
| 9 | Replayable benchmarks, model/control comparisons, exported metrics, human studies |
| 10 | Optional voice, map editor, larger maps/units, MCP/external adapters; larger changes need separate scope |

Provider calls stay asynchronous and outside the tick loop. Tag input snapshots with tick/time and
revalidate returned proposals against current state. Give models only player-available information;
never let unsupported output execute arbitrary code or mutate the world. Explicit selections resolve
vague language; contradictions or materially different interpretations require clarification.
Simple reversible grounded actions should not need repeated confirmation. Voice is input, not a
separate command authority; an editor must use the same validated map format.

## Evaluation and deferred decisions

The eventual demonstration links factory production, selected bridge regions, patrol, requested
repair/return, and language modification while recording inputs, proposals, commands, events,
failures, and outcomes. Measure success/time, action and correction counts, invalid proposals,
clarification, latency/token cost, losses/resource efficiency, report factuality, workload, trust,
and perceived control. Seeded scenarios and visible failure evidence support comparison.

Movement replacement is a separate task: reproduce failures, define clearance/arrival/queue and
save/replay contracts, then compare candidates on unchanged crowd/FPS workloads. Historical
proposals for clearance-aware routes, stable formation slots, and reciprocal avoidance are not
implemented requirements or current benchmark evidence. Retain relevant research entry points:
[Continuum Crowds](https://grail.cs.washington.edu/projects/crowd-flows/78-treuille.pdf),
[ORCA](https://gamma-web.iacs.umd.edu/ORCA/), and
[ClearPath](https://gamma-web.iacs.umd.edu/CA/).
Native extensions, workers, GPU simulation, dynamic terrain, multiplayer, and engine migration
require demonstrated need and explicit scope. They are not prerequisites for the prototype.
