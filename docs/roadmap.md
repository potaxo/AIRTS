# AIRTS Research Roadmap

**Status:** Future work only

The [design overview](design.md) describes the implemented runtime. This roadmap owns research
questions and unimplemented capabilities; nothing here is a claim about current behavior.

## Research direction

AIRTS asks whether players can combine conventional RTS controls, exact cursor grounding, and
natural language to create reliable persistent automations without surrendering tactical control to
a continuously running model.

The primary question is:

> Can cursor-grounded language automation reduce RTS control burden while remaining predictable,
> inspectable, and easy to correct?

Supporting questions include:

- How reliably can a local model translate grounded instructions into supported command schemas?
- How much do selected entities, points, routes, and regions reduce spatial ambiguity?
- When should the system execute immediately, request clarification, or ask for confirmation?
- Are fixed automation templates more reliable than unrestricted plans or generated code?
- Does an inspectable automation panel improve trust and error recovery?
- How should structured observations express uncertainty and incomplete map coverage?
- Which responsibilities belong to the player, model, automation runtime, and low-level simulation?
- How should workload, task success, corrections, latency, and factuality be measured?

## Next milestone: provider boundary

Introduce language interpretation without coupling it to the simulation core:

- a provider protocol;
- a deterministic mock provider;
- an LM Studio adapter;
- compact grounded request context;
- structured command or automation proposals;
- schema and reference validation;
- at most one model repair attempt;
- explicit clarification and human-facing failure results;
- tests that do not require a live model.

Model output must enter through the same command boundary as the UI and replay. It must never mutate
entities, resources, paths, or automations directly.

## Grounded language automation

After the provider boundary is stable:

- resolve selected entities, points, routes, and regions in model requests;
- detect contradictions between language and explicit grounding;
- create and modify supported persistent automations;
- expose the resulting proposal, validation result, and provenance to the player;
- add multi-region grounding only with explicit deterministic allocation semantics.

Simple, reversible, well-grounded instructions should execute without confirmation. Materially
ambiguous, destructive, or contradictory instructions should stop for clarification.

## Scouting and reporting

A later milestone may add:

- deterministic region coverage and search planning;
- last-known observations with timestamps;
- risk and return policies;
- structured evidence for visible entities and unobserved subregions;
- natural-language summaries that cannot introduce facts absent from the evidence;
- factuality and coverage evaluation.

This work depends on a clear information-authority model. Normal model requests must not receive
hidden simulation state.

## Evaluation

Candidate comparisons include direct control, ungrounded language, cursor-grounded language, and
persistent automation. Useful measures include task success, completion time, player actions,
invalid proposals, clarifications, manual corrections, automation failures, unit losses, resource
efficiency, inference latency, workload, trust, and factual accuracy.

Evaluation scenarios should be deterministic and replayable. Scenario, map, seed, commands, model
inputs, raw outputs, validated proposals, failures, and final state should be retained when relevant.

## Later possibilities

These ideas are deliberately outside the next milestone:

- voice input;
- a map editor and map-defined semantic regions;
- larger maps and additional unit types;
- destructible bridges or dynamic terrain;
- asynchronous strategic review;
- MCP or external-agent exposure;
- cloud providers;
- Unity or another frontend;
- multiplayer, air units, or naval units.

They should be added only when a concrete research question needs them.

## End-to-end research demonstration

A representative future demonstration is:

1. The player selects one or more factories and grounds two bridge regions.
2. The player requests continuous tank production, defense of both regions, and repair of damaged
   units.
3. A model produces a supported structured proposal.
4. AIRTS validates references, capabilities, geometry, paths, resources, and conflicts.
5. The player inspects the resulting automation and later modifies or cancels it.
6. Replay data records the grounding, model exchange, validated command, transitions, failures, and
   final state.

The demonstration is successful only if the deterministic runtime continues to work without a
model and invalid output cannot change authoritative state.

## Continuing non-goals

AIRTS does not pursue unrestricted generated executable code, continuous model-controlled micro,
autonomous multi-agent warfare, commercial graphics, distributed services, or nondeterministic
simulation work scheduled across workers.
