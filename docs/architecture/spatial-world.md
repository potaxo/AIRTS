# AIRTS Spatial and World Architecture

This document owns spatial grounding, regions, maps, entities, movement, and world-state representation.

[Design index](../design.md) · [Roadmap](../roadmap.md)

---

# 8. Spatial Input and Grounding

## 8.1 Player-facing spatial tools

AIRTS should support four primary spatial input tools:

* point placement;
* line or polyline drawing;
* rectangular selection;
* freehand area selection.

These tools should be sufficient for the first prototype.

## 8.2 Canonical internal geometry

Internally, the four UI methods are normalized into three geometry types:

```text
PointTarget
PolylineTarget
PolygonRegion
```

A rectangle is represented as a polygon after input processing.

A freehand area is simplified into a valid polygon.

## 8.3 Geometry interpretation

Different automations may interpret the same geometry differently.

| Geometry          | Possible interpretation                                           |
| ----------------- | ----------------------------------------------------------------- |
| Point             | Move to, guard, build near, observe, or patrol within a radius    |
| Polyline          | Follow, patrol, scout, or defend along a route                    |
| Polygon           | Patrol, search, defend, build, gather, or operate within an area  |
| Multiple polygons | Distribute forces, alternate patrol areas, or cover several zones |

For patrol:

* a point means patrol or guard around the point;
* a line means repeatedly follow the route;
* a polygon means patrol or search within the area;
* multiple regions mean patrol or distribute forces across those regions.

## 8.4 Spatial reference types

AIRTS distinguishes between regions, entities, points, and paths.

Initial reference types include:

```text
MapRegionRef
UserRegionRef
EntityRef
PointRef
PolylineRef
```

Examples:

```text
MapRegionRef("dark_mountain")
UserRegionRef("eastern_defense_zone")
EntityRef("factory_01")
PointRef("point_014")
PolylineRef("patrol_route_003")
```

An entity is not treated as a region.

The game resolves an entity reference to:

* its current position;
* its occupied cells;
* its interaction location;
* its valid rally or repair point.

---

# 9. Region Model

## 9.1 Map-defined regions

Map-defined regions are created with the map.

Each map-defined region receives:

* a stable ID;
* a display name;
* polygonal geometry;
* optional semantic labels;
* optional strategic metadata.

Examples:

* Big Grassland;
* Dark Mountain;
* North Bridge;
* Southern Forest;
* Western Resource Field;
* Main Base;
* Northern Approach.

Every important map region should have a default name.

## 9.2 User-defined regions

The player may draw a region during gameplay.

A user-created region is temporary by default.

The player may explicitly assign it a name. A named user region persists and can be referenced in later instructions.

Examples:

```text
Eastern Defense Zone
Tank Assembly Area
Unsafe Forest
Bridge Patrol Area
```

An unnamed region may be discarded when:

* its associated instruction completes;
* its automation is canceled;
* the player clears temporary selections;
* the match ends.

## 9.3 Overlapping regions

Regions may overlap.

A tile may simultaneously belong to:

* Big Grassland;
* Northwest Sector;
* Bridge Approach;
* Player Defense Zone.

Overlapping regions are useful because different labels can represent different semantic meanings.

## 9.4 Spatial relationships

Relationships should be calculated by deterministic code.

Supported relationships may include:

* north of;
* south of;
* east of;
* west of;
* adjacent to;
* inside;
* intersecting;
* connected to;
* reachable from;
* nearest to;
* across a bridge from;
* within a specified distance;
* separated by an obstacle.

The language model may reason using these relationships, but it should not calculate precise geometry itself.

---

# 10. Map Representation

## 10.1 Grid model

The simulation uses a 2D grid.

Each cell has a coordinate:

```text
(x, y)
```

## 10.2 Static terrain layer

Static terrain information may include:

* terrain type;
* movement cost;
* ground passability;
* vehicle passability;
* cover value;
* elevation;
* water depth;
* bridge membership;
* road membership.

Initial terrain types may include:

* grass;
* road;
* forest;
* shallow water;
* deep water;
* bridge;
* rocky or impassable terrain.

## 10.3 Semantic layer

The semantic layer contains:

* region membership;
* region IDs;
* region names;
* landmark IDs;
* bridge IDs;
* choke-point labels;
* resource-zone labels;
* base-zone labels;
* user-defined labels.

## 10.4 Dynamic entity layer

Dynamic state includes:

* units;
* buildings;
* resources;
* construction sites;
* temporary obstacles;
* occupancy.

Dynamic state should not be permanently copied into static terrain definitions.

## 10.5 Visibility and memory layer

Visibility information must be player-specific.

Possible visibility states:

* currently visible;
* previously explored;
* unexplored;
* last-known observation;
* last observation time.

## 10.6 Strategic-analysis layer

Calculated information may include:

* friendly influence;
* enemy influence;
* threat value;
* resource value;
* reinforcement distance;
* path accessibility;
* front-line estimate;
* region control.

These values should be calculated rather than manually stored whenever possible.

---

# 11. Initial Map and Entity Scale

The engine should eventually support maps up to approximately:

```text
256 × 256 cells
```

However, the first vertical slice should use:

```text
64 × 64 cells
```

Recommended progression:

```text
First vertical slice: 64 × 64
Later scenarios:     128 × 128
Scalability target:  256 × 256
```

A small map is easier to:

* render;
* inspect;
* debug;
* replay;
* test;
* reason about;
* use in early model prompts.

## 11.1 Initial entity set

The first meaningful entity set may include:

* scout vehicle;
* light tank;
* heavy tank;
* artillery or long-range tank;
* factory;
* repair hub;
* command center;
* resource generator.

Unit types may differ in:

* health;
* attack damage;
* attack range;
* movement speed;
* vision range;
* production cost;
* repair time;
* terrain capability.

The first vertical slice may use fewer entities than this complete set.

---

# 12. Human Interaction Model

## 12.1 Conventional RTS interaction

AIRTS should retain basic RTS controls:

* click to select one entity;
* drag a rectangle to select multiple entities;
* use `Shift` to add entities or regions;
* right-click or an equivalent command to move;
* select factories and support buildings;
* place points;
* draw routes;
* draw areas;
* inspect entities;
* inspect automations;
* manually override automation-controlled units.

## 12.2 Grounded language context

A language request may include:

```json
{
  "selected_entities": ["factory_01"],
  "selected_regions": [
    "user_region_014",
    "user_region_015"
  ],
  "selected_points": [],
  "selected_polylines": [],
  "cursor_position": [48, 26],
  "instruction": "Keep producing tanks and patrol both of these areas."
}
```

## 12.3 Grounding priority

The system should interpret references in this order:

1. selected entities;
2. selected regions;
3. selected points or paths;
4. explicit references in the instruction;
5. active automation context;
6. recent conversational context;
7. clarification.

## 12.4 Language–grounding conflict policy

AIRTS follows these rules:

1. Vague language with explicit grounding uses the grounding.
2. Explicit language consistent with grounding proceeds normally.
3. Explicit language contradicting grounding triggers clarification.
4. Missing grounding and materially ambiguous language triggers clarification.
5. AIRTS must not guess when several materially different interpretations remain.

Example:

```text
Selected region: North Bridge
Instruction: “Patrol the South Base.”
```

The system should ask which location the player intends.

## 12.5 Confirmation policy

Simple, reversible, well-grounded instructions should not require confirmation.

Examples:

* move selected units to a selected point;
* pause a selected automation;
* cancel a factory queue;
* patrol explicitly selected areas.

Confirmation should be requested when:

* an instruction is materially ambiguous;
* no valid target has been grounded;
* multiple important interpretations remain;
* the action is destructive or difficult to reverse;
* the action affects a large proportion of the player’s forces;
* the action cancels an important automation;
* the action conflicts with a high-priority instruction.

---

# 13. Command Model

## 13.1 Direct commands

Direct commands are short-lived actions.

Initial direct command types may include:

* move;
* stop;
* attack target;
* attack-move;
* hold position;
* set rally point;
* cancel production;
* pause automation;
* resume automation;
* cancel automation.

Direct manual control has the highest authority.

## 13.2 Persistent automations

Persistent automations continue across simulation ticks until they are:

* completed;
* canceled;
* paused;
* invalidated;
* failed;
* replaced;
* expired.

Initial automation templates may include:

* patrol;
* defend;
* scout;
* harass;
* produce;
* produce and rally;
* produce and reinforce;
* gather resources;
* build structure;
* repair and return;
* maintain force threshold;
* develop economy.

## 13.3 Template parameters

Automation parameters may include:

* entity references;
* spatial references;
* unit type;
* unit count;
* production target;
* resource threshold;
* risk tolerance;
* retreat-health threshold;
* patrol order;
* engagement range;
* pursuit range;
* defensive radius;
* reinforcement threshold;
* completion condition;
* priority;
* return destination;
* acceptable loss level.

The initial language model selects a supported template and supplies validated parameters.

It does not generate arbitrary executable code.

---

