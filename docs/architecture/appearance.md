# Battle appearance, terrain and interface

This document owns battle art, terrain detail, interface styling, authoring and visual facing. The
[performance contract](../milestones/performance.md#fps-and-rendering) owns batching,
frame staging, latency and workload gates. Production/selection portraits are not implemented.

## Ownership and files

- [world/units](../../src/airts/world/units/catalog.py) owns gameplay profiles, collision, mass,
  prices and production relationships. It imports no presentation or image code.
- [visuals/catalog.py](../../src/airts/presentation/visuals/catalog.py) explicitly maps every
  entity kind to its body/optional weapon PNG and displayed size in world units. A drawing's
  size does not alter footprint, movement, collision radius or mass.
- [data/sprites](../../src/airts/data/sprites) contains shipped art, grouped as
  `units/<kind>/body.png`, `units/<kind>/weapon.png` and `buildings/<kind>/...`.
  These are canonical game resources, not experiment outputs. `pyproject.toml` includes them
  in packages; the existing Windows package job collects AIRTS data.
- [visuals/assets.py](../../src/airts/presentation/visuals/assets.py) loads and validates packaged
  resources, makes the atlas and owns bounded software variants. Missing or invalid images
  fail with their resource name; no silent replacement circles are drawn.
- [visuals/facing.py](../../src/airts/presentation/visuals/facing.py) owns display-only facing;
  [visuals/gpu.py](../../src/airts/presentation/visuals/gpu.py) encodes sprite instances through
  the shared [GPU record layout](../../src/airts/presentation/gpu_shapes.py).
- [visuals/terrain.py](../../src/airts/presentation/visuals/terrain.py) owns deterministic material
  patterns, water banks, software detail sources and the matching GLSL functions.
- [ui/theme.py](../../src/airts/presentation/ui/theme.py) owns semantic interface colors and pixel
  frames. [ui/drawing.py](../../src/airts/presentation/ui/drawing.py) paints shared buttons and text;
  scoped panels own layout and actions.

## Shared style

The battlefield uses muted olive ground, brown roads, blue-gray water and gray stone. Forest
clusters, gravel, short water ripples and bridge planks use hard pixel detail. Water banks follow
actual adjacent terrain inside the water cell; they do not add obstacles or change map geometry.
Terrain variation is deterministic in world coordinates. Its eight detail pixels per cell become
visible only at eight or more screen pixels per cell, keeping distant views clear and avoiding
subpixel texture shimmer. Central pixels retain the exact terrain palette reference.

The interface uses dark green-gray metal panels, hard raised edges, warm light text and gold
active states. Green retains its resource/work meaning; team colors remain distinct. Selection,
production, automation tabs, settings, help, saves and the lobby share these primitives. Font
antialiasing remains enabled for reading small labels. Styling changes preserve button rectangles,
command routing and the renderer's dirty-region cache; pointer hover adds no redraw work.

Change terrain/entity/team colors in [palette.py](../../src/airts/presentation/palette.py), material
patterns in `visuals/terrain.py` and panel/button/text roles in `ui/theme.py`. This separation keeps
appearance out of gameplay profiles. Terrain and chrome use code-native drawing with existing
dependencies; no generated covers or image-generation service is involved.

Artillery feedback uses shared [projectile effects](../../src/airts/presentation/projectile_effects.py)
in both backends: a larger orange shell with a pale rim follows a display-only parabolic arc above
its ground shadow. Impact shows a one-second full damage-radius outline, expanding shockwave,
central flash and radial embers, even when no enemy is hit. Radius follows the combat profile;
expiry follows the saved trace and freezes with simulation pause. Ordinary projectiles keep their
small fixed-pixel cores. OpenGL packs these effects into existing world shape batches.

## Art conventions

Each source is a 64x64 RGBA PNG, facing north, with its rotation pivot at the exact image center.
Transparent pixels have alpha 0 and opaque pixels 255; soft alpha is rejected. The neutral
material ramp is tinted by the shared entity/team palette at display time. Black is reserved
for software transparency; shipped palette colors keep all opaque material pixels nonblack.

Scouts have narrow wheeled hulls, light tanks narrow tracks, heavy tanks wide reinforced hulls,
builders an asymmetric claw and heavy artillery a long barrel/rear stabilizers. Light artillery
uses a compact open cradle and a long slim barrel; tank destroyers have a wedge hull and long narrow cannon.
Heavy artillery has a visibly thicker barrel and broad muzzle brake. Both artillery barrels extend
into the sprite tile's forward transparent margin; hull images, display sizes, collision radii,
footprints and mass stay independent of this visual overhang. Turret pedestals,
factory bays, repair crosses, command antennas, extractor plumbing and synthesis cores provide
building silhouettes. Vehicle/turret weapons have separate centered layers. Display sizes preserve
a smaller scout/light tank and a broader heavy tank without imposing different grid occupancies.
Health bars sit above visual extents rather than inside large building roofs.

Authoring is offline and uses the existing Pygame dependency:

```powershell
.\.venv\Scripts\python tools/art/generate_sprites.py
.\.venv\Scripts\python tools/art/generate_sprites.py --check
```

The generator owns deterministic source shapes; edit it when changing authored pixels, then
regenerate the PNGs. Edit the appearance catalog for resource bindings/display sizes and the
palette for entity/team colors. The runtime imports no generator and writes no images.
Adding a kind requires its gameplay definition and explicit appearance binding; coverage tests
identify missing bindings. New art can replace these files while respecting the image contract.

## Facing and lifecycle

Headings are clockwise radians from north in positive-Y-down map coordinates. Body heading follows
voluntary path movement, including its final step; a parked body's collision displacement does not
rotate it. Stopping retains the last heading. Armed mobile weapons aim at their current target;
fixed turret bodies remain still while their weapons turn. With no target an aligned weapon follows
its body; an independently aimed weapon returns toward it.

Visual turning is bounded per simulation tick, uses the shortest angular arc and retains adjacent
unwrapped endpoints for GPU interpolation. Facing records are reused, synchronize once per changed
tick/command/membership and prune removed entities. App load/reset discards history and initializes
from current path/target; otherwise idle facing starts north. Facing is not an authoritative save
field, changes no gameplay or events, and is not guaranteed to retain an idle direction after load.

## Render resource policy

Sources load once into bounded caches. One RGBA atlas includes body/weapon layers and derived
aligned composites; aligned parts share one draw record without losing either image. The atlas
uploads once per GPU context and releases with that renderer. Camera changes are uniforms and
reuse world buffers; position and angle interpolation happen in the vertex shader. Pixel sampling
is nearest and does not bleed between atlas tiles.

Software uses 32 heading bins and a 512-entry LRU for scale/tint/rotation variants. Variants use
the target's RGB format and binary transparency/RLE, avoiding per-entity RGBA conversion during
scene drawing. Software renders cached scene transforms at tick boundaries; the default GPU
backend provides continuous angle interpolation. Neither renderer changes simulation state.

GPU terrain keeps the one-RGB-texel-per-cell map upload, uses procedural material detail in the
existing world rectangle and reads adjacent cells only at water-edge pixels. No extra texture or
per-frame world geometry is introduced. Software retains a coarse cell map and lazily builds one
fixed-resolution eight-pixels-per-cell detail surface (18.75 MiB in its RGB surface storage for
a 320x320 map). Zoom scales only visible crops; map reset releases both sources. Detail and UI
pixels are covered by [map appearance integration](../../tests/integration/test_map_appearance.py),
including native readback across all materials/variants/shorelines and zoom-time texture reuse.

Source/atlas/cache and angle tests live in [visual assets](../../tests/unit/test_visual_assets.py).
[Visual integration](../../tests/integration/test_unit_visuals.py) covers movement, passive pushes,
independent aiming, scene staging and native texture/rotation pixel readback.
