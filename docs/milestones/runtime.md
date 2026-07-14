# AIRTS Implemented Runtime Milestone

This document records the implemented builder, factory, and responsive-UI contract. It is
normative for current behavior together with the [design index](../design.md).

---

# 37. Builder, Factory, and Responsive UI Milestone

## Construction

The simulation adds a factory-produced `builder` unit. A selected builder group may create a factory,
repair hub, or resource generator by choosing a building in the right context panel and clicking a
grid location. UI code may preview or submit construction, but only the simulation may validate
placement, reserve resources, advance progress, or create the building.

Placement validation covers map bounds, terrain, footprint overlap, ownership, and builder
capability. Factories, repair hubs, and resource generators cost 400, 250, and 200 resources. A
shared construction job reserves that cost once and requires 100 construction value. Every builder
still assigned to the job contributes its profile's build speed, currently 5 value per tick, only
while within its 2.5-map-unit build range of the building footprint. Out-of-range builders route to
a valid perimeter point and contribute no work until they arrive. One in-range builder therefore
completes a job in 20 ticks, while additional in-range builders reduce elapsed time without
duplicating the cost or result. Command centers are scenario-defined and not constructible.

Shift-clicking while a building placement tool is active keeps that tool selected and appends a
reserved construction site to the selected builders' FIFO queue. Waiting jobs do not own builders
or reserve resources; they reserve only their non-overlapping footprints. When the current job
completes or is canceled, the next job claims all still-available builders and charges its cost.
A non-Shift construction command replaces pending construction jobs for those builders. Queue
intent, lifecycle, progress, assignments, and destinations are persisted and replayed.

Right-click while placement is active closes only the placement tool. It must not issue a move or
attack command, cancel accepted construction, or change builder assignments. Assigned builders
inside a reserved footprint route to a valid perimeter point and do not contribute work while
inside. If any entity still occupies the footprint at 100 percent progress, the job remains
inspectable with reason `SITE_OCCUPIED` and retries safely; occupancy is committed before the
finished building is published to entity state.

Construction commands, automation state, resource accounting, timing, persistence, and replay all
use the same authoritative simulation path.

## Factory Production

Factories produce every current mobile kind: scout, light tank, heavy tank, and builder. All
manual, finite ordered, and continuous requests share the same five-tick build, resource
reservation, spawn search, event, lifecycle, persistence, and replay path. An ordered request is a
sequence of `(unit kind, exact positive quantity)` stages. It completes only after the last stage
and then leaves the live automation panel. A continuous request repeats one kind until explicitly
canceled; a newer continuous request supersedes an older unfinished continuous request for that
factory. Finite player queues have execution priority over the current continuous request, which
waits and resumes after finite work completes. Continuous production does not create a defense by
itself. `Produce + Defend` and automation retargeting attach the selected polygon or polyline to
the factory's existing loop, preserve that loop's unit kind, and route its existing and future
produced units into one linked defense automation. A polygon uses the expanding gathering-point
formation. A polyline creates an ordinary line defense and deterministically redistributes the
produced force at evenly spaced stations across the full line whenever a unit joins.

Same-kind building controls apply to the complete compatible friendly selection. Selecting several
factories and choosing Loop submits one independent continuous production command per factory;
starting an ordered queue copies the exact staged sequence to every selected factory; and
`Produce + Defend` retargets every selected factory loop to the same line or polygon. The UI only
broadcasts ordinary commands. It does not merge factory state, reserve resources, advance work, or
create units itself. Consequently every factory retains its own authoritative costs, timing, queue
priority, spawn behavior, persistence, and replay history. Other contextual building actions follow
the same selection-wide rule when their underlying command supports a group, including economy
development across all selected friendly resource generators.

## Interface

The application UI has a left status and scrollable automation rail, a central pannable canvas,
and a right selection/action rail. The automation rail exposes a high-contrast draggable scrollbar,
clamps and backfills its viewport whenever live items disappear, and presents a factory production
loop with its linked area-defense automation as one management item. Mixed selections first show
every selected entity kind. The player chooses one kind to deselect other kinds before its
statistics and valid controls appear;
a single-kind selection opens those details immediately. Double-clicking a friendly entity selects
all friendly entities of that kind currently inside the canvas. Escape returns to selection mode
and clears entity, geometry, placement, and inspection state. Save, load, new game, resolution
presets, and rolling frame/present/simulation timing are grouped under a settings button, while the
full control reference is hidden from the normal
status surface. Middle-drag pans the canvas through shared map/screen transforms, and the resizable
window recalculates rail, canvas, command-bar, font, and map scaling from its current dimensions.
The left rail reports `Real FPS`, a stutter-sensitive rolling 1%-low rate calculated from the p99
completed-swap frame interval. Settings retains the rolling average `Submit FPS` for comparison.
These are application-side presentation measurements, not claims about physical monitor scan-out.
Building placement previews its snapped footprint in green when
valid and red when blocked; accepted construction jobs remain visible with completion progress.
Single-kind detail titles and applicable action labels show the selected count so the scope of a
group command is visible before it is submitted.

## Exclusions

This milestone does not add builder resource gathering, construction refunds,
command-center construction, technology prerequisites, or multiple factories contributing to one
production automation.

---
