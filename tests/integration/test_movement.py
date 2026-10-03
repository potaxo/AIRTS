"""Physical guarantees of the replacement controller."""

from collections.abc import Callable

import pytest
from tests.integration.scenarios import hold_contact_simulation

from airts.contracts.automations import DefendParameters
from airts.contracts.commands import (
    CreateDefendCommand,
    HoldPositionCommand,
    MoveCommand,
    StopCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PointTarget
from airts.navigation.collision import collision_radius
from airts.simulation import Simulation
from airts.systems.movement import position_clear
from airts.world.entities import UnitState
from airts.world.map import GameMap, load_map_data


def _advance_with_clearance(simulation: Simulation, ticks: int = 1) -> None:
    """Check actual trajectories, total speed, static clearance and submitted occupancy."""
    units = [entity for entity in simulation.entities.values() if entity.is_movable]
    blocked = frozenset(
        (x, y)
        for y, row in enumerate(simulation.game_map.terrain)
        for x, terrain in enumerate(row)
        if not terrain.passable
    ) | frozenset(
        cell
        for entity in simulation.entities.values()
        if not entity.is_movable
        for cell in entity.occupied_cells
    )
    for _ in range(ticks):
        before = {entity.entity_id: entity.position for entity in units}
        simulation.advance()
        for index, entity in enumerate(units):
            start = before[entity.entity_id]
            assert (
                entity.position.distance_to(start) <= entity.speed * Simulation.TICK_SECONDS + 1e-8
            )
            assert position_clear(
                entity.position,
                collision_radius(entity.kind),
                simulation.game_map.width,
                simulation.game_map.height,
                blocked,
            )
            for sample in range(1, 5):
                time = sample / 5
                assert position_clear(
                    Point(
                        start.x + (entity.position.x - start.x) * time,
                        start.y + (entity.position.y - start.y) * time,
                    ),
                    collision_radius(entity.kind),
                    simulation.game_map.width,
                    simulation.game_map.height,
                    blocked,
                )
            assert simulation.occupancy.cells_for(entity.entity_id) == entity.occupied_cells
            assert all(
                entity.entity_id in simulation.occupancy.occupants(cell)
                for cell in entity.occupied_cells
            )
            for other in units[index + 1 :]:
                other_start = before[other.entity_id]
                dx, dy = start.x - other_start.x, start.y - other_start.y
                vx = entity.position.x - start.x - other.position.x + other_start.x
                vy = entity.position.y - start.y - other.position.y + other_start.y
                time = (
                    min(1.0, max(0.0, -(dx * vx + dy * vy) / (vx * vx + vy * vy)))
                    if vx or vy
                    else 0.0
                )
                contact = collision_radius(entity.kind) + collision_radius(other.kind)
                assert (dx + vx * time) ** 2 + (dy + vy * time) ** 2 >= contact**2 - 1e-8


def _displace_holder_for_recovery(simulation: Simulation, position: Point) -> None:
    """Inject a legal displaced body to isolate recovery from the pending contact solver."""
    holder = simulation.entities["holder"]
    holder.position = position
    simulation.occupancy.move("holder", holder.occupied_cells, frozenset(simulation.entities))


@pytest.mark.parametrize("vertical", [False, True], ids=["horizontal", "vertical"])
def test_opposing_steps_cannot_tunnel_or_exceed_speed(vertical: bool) -> None:
    def coordinates(x: float, y: float) -> list[float]:
        return [y, x] if vertical else [x, y]

    simulation = Simulation(
        load_map_data(
            {
                "id": "crossing",
                "name": "Crossing",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "a", "kind": "scout", "position": coordinates(4.5, 5.5)},
                    {"id": "b", "kind": "scout", "position": coordinates(5.8, 5.5)},
                ],
            }
        )
    )
    assert simulation.execute(MoveCommand(("a",), Point(*coordinates(9.5, 5.5)))).accepted
    assert simulation.execute(MoveCommand(("b",), Point(*coordinates(1.5, 5.5)))).accepted
    _advance_with_clearance(simulation, 40)


def test_held_unit_preserves_clearance_and_recovers_after_another_passes(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    held = simulation.entities["unit_02"].position
    assert simulation.execute(HoldPositionCommand(("unit_02",))).accepted
    assert simulation.execute(MoveCommand(("unit_01",), Point(1.5, 7.5))).accepted
    _advance_with_clearance(simulation, 60)
    assert simulation.entities["unit_01"].position == Point(1.5, 7.5)
    assert simulation.entities["unit_02"].state is UnitState.HOLDING
    assert simulation.entities["unit_02"].position.distance_to(held) <= 0.05


@pytest.mark.parametrize("behavior", ["idle", "stop", "hold"])
def test_stationary_unit_yields_to_traffic_in_single_file_throat(behavior: str) -> None:
    """The exit has space to pass, but a held whole-cell wall would reject the order."""
    simulation = hold_contact_simulation()
    holder = simulation.entities["holder"]
    anchor = holder.position
    if behavior == "hold":
        assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    elif behavior == "stop":
        assert simulation.execute(StopCommand(("holder",))).accepted
    target = Point(12.5, 5.5)
    result = simulation.execute(MoveCommand(("mover",), target))
    assert result.accepted, (
        f"a mobile collider must not become a static route wall: {result.reason}"
    )

    maximum_displacement = 0.0
    for _ in range(140):
        _advance_with_clearance(simulation)
        maximum_displacement = max(maximum_displacement, holder.position.distance_to(anchor))
        assert holder.state is (UnitState.HOLDING if behavior == "hold" else UnitState.IDLE)
        assert "holder" not in simulation.assignments
        assert not holder.pursue_target
    assert maximum_displacement > 0.05, "stationary bodies must actually yield to contact"
    assert simulation.entities["mover"].position.distance_to(target) <= 0.05
    assert not any(
        event.subject_id == "holder"
        and event.event_type in {EventType.MOVEMENT_STARTED, EventType.MOVEMENT_COMPLETED}
        for event in simulation.events.events
    ), "passive yielding/hold recovery must not fabricate a player movement order"
    if behavior == "hold":
        assert holder.position.distance_to(anchor) <= 0.05
        assert holder.hold_anchor == anchor
    else:
        assert holder.position.distance_to(anchor) > 0.05
    settled = holder.position
    _advance_with_clearance(simulation, 20)
    assert holder.position.distance_to(settled) <= 1e-4, "pressure-free units must settle"


def test_hold_recovers_displaced_body_without_active_movement_paths() -> None:
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(7.5, 5.5))
    assert all(not entity.path for entity in simulation.entities.values())

    _advance_with_clearance(simulation, 60)

    holder = simulation.entities["holder"]
    assert holder.state is UnitState.HOLDING
    assert holder.position.distance_to(anchor) <= 0.05, "Hold must retain the original anchor"
    settled = holder.position
    _advance_with_clearance(simulation, 20)
    assert holder.position.distance_to(settled) <= 1e-4


def test_reissued_hold_uses_current_position_as_the_new_anchor() -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    new_anchor = Point(7.5, 5.5)
    _displace_holder_for_recovery(simulation, new_anchor)
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(9.5, 5.5))

    _advance_with_clearance(simulation, 60)

    assert simulation.entities["holder"].position.distance_to(new_anchor) <= 0.05
    assert simulation.entities["holder"].state is UnitState.HOLDING


@pytest.mark.parametrize("replacement", ["stop", "move"])
def test_accepted_control_replacement_ends_hold_recovery(replacement: str) -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    displaced = Point(7.5, 5.5)
    _displace_holder_for_recovery(simulation, displaced)
    destination = Point(12.5, 5.5) if replacement == "move" else displaced
    command = (
        MoveCommand(("holder",), destination) if replacement == "move" else StopCommand(("holder",))
    )
    assert simulation.execute(command).accepted

    _advance_with_clearance(simulation, 80)

    assert simulation.entities["holder"].position.distance_to(destination) <= 0.05
    assert simulation.entities["holder"].state is UnitState.IDLE
    assert simulation.entities["holder"].hold_anchor is None


def _contact_queue(count: int = 4, *, throat: bool = False, closed: bool = False) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "contact_queue",
                "name": "Contact queue",
                "width": 24,
                "height": 12,
                "terrain": {
                    "default": "grass",
                    "rectangles": (
                        (
                            [[0, 0, 7, 5, "rock"], [0, 6, 7, 6, "rock"]]
                            + ([[7, 0, 17, 12, "rock"]] if closed else [])
                        )
                        if throat
                        else []
                    ),
                },
                "entities": [
                    {"id": f"unit_{i}", "kind": "scout", "position": [3.5 + i, 5.5]}
                    for i in range(count)
                ],
            }
        ),
        random_seed=181,
    )


def test_contact_pressure_transmits_through_a_short_stationary_queue() -> None:
    simulation = _contact_queue(throat=True)
    starts = {k: e.position for k, e in simulation.entities.items()}
    assert simulation.execute(MoveCommand(("unit_0",), Point(16.5, 5.5))).accepted
    _advance_with_clearance(simulation, 80)
    assert simulation.entities["unit_0"].position.distance_to(Point(16.5, 5.5)) < 0.05
    assert all(
        simulation.entities[k].position.distance_to(starts[k]) > 0.05
        for k in starts
        if k != "unit_0"
    )
    assert all(e.state is UnitState.IDLE and not e.path for e in simulation.entities.values())
    settled = simulation.snapshot()
    positions = {k: e.position for k, e in simulation.entities.items()}
    _advance_with_clearance(simulation, 20)
    assert positions == {k: e.position for k, e in simulation.entities.items()}
    assert settled["entities"] == simulation.snapshot()["entities"]


@pytest.mark.parametrize("offset", [0.0, 0.58], ids=["identical-centers", "slight-overlap"])
def test_initial_overlap_recovers_deterministically_without_new_orders(offset: float) -> None:
    simulations = [_contact_queue(3), _contact_queue(3)]
    for simulation in simulations:
        for i, entity in enumerate(simulation.entities.values()):
            entity.position = Point(8.5 + i * offset, 5.5)
            simulation.occupancy.move(
                entity.entity_id, entity.occupied_cells, frozenset(simulation.entities)
            )
        for _ in range(60):
            before = {k: e.position for k, e in simulation.entities.items()}
            simulation.advance()
            units = list(simulation.entities.values())
            for i, entity in enumerate(units):
                assert (
                    entity.position.distance_to(before[entity.entity_id])
                    <= entity.speed * Simulation.TICK_SECONDS + 1e-8
                )
                for other in units[i + 1 :]:
                    initial = before[entity.entity_id].distance_to(before[other.entity_id])
                    assert entity.position.distance_to(other.position) >= min(0.6, initial) - 1e-8
        assert (
            min(
                a.position.distance_to(b.position)
                for i, a in enumerate(units)
                for b in units[i + 1 :]
            )
            >= 0.6 - 1e-8
        )
        assert not any(
            event.event_type in {EventType.MOVEMENT_STARTED, EventType.MOVEMENT_COMPLETED}
            for event in simulation.events.events
        )
    assert simulations[0].export_state() == simulations[1].export_state()


def test_defender_yields_without_losing_station_or_assignment() -> None:
    simulation = hold_contact_simulation()
    result = simulation.execute(
        CreateDefendCommand(("holder",), PointTarget(simulation.entities["holder"].position))
    )
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    stations = dict(automation.parameters.stations)
    simulation.advance(40)
    anchor = stations["holder"]
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 5.5))).accepted
    displaced = 0.0
    for _ in range(160):
        _advance_with_clearance(simulation)
        holder = simulation.entities["holder"]
        displaced = max(displaced, holder.position.distance_to(anchor))
        assert simulation.assignments["holder"] == automation.automation_id
        assert automation.parameters.stations == stations
    assert displaced > 0.05
    assert holder.position.distance_to(anchor) <= 0.05
    settled = holder.position
    _advance_with_clearance(simulation, 20)
    assert holder.position == settled


def test_hold_unreachable_anchor_sleeps_until_static_obstacles_change() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "blocked_anchor",
                "name": "Blocked anchor",
                "width": 20,
                "height": 12,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [8, 0, 2, 12, "rock"],
                        [8, 4, 2, 4, "grass"],
                    ],
                },
                "entities": [
                    {"id": "holder", "kind": "scout", "position": [5.5, 5.5]},
                    {"id": "factory", "kind": "factory", "position": [8, 4]},
                ],
            }
        )
    )
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    # A legal body and anchor in disconnected components isolate failed-route
    # recovery (e.g. a loaded anchor after a bridge entrance was built over).
    _displace_holder_for_recovery(simulation, Point(12.5, 5.5))
    _advance_with_clearance(simulation, 100)
    holder = simulation.entities["holder"]
    assert holder.position == Point(12.5, 5.5)
    assert holder.hold_anchor == Point(5.5, 5.5)
    assert holder.recovery_blocked_cells is not None
    builds = simulation.navigation_field_build_count
    retry_tick = holder.recovery_retry_tick
    _advance_with_clearance(simulation, 100)
    assert simulation.navigation_field_build_count == builds
    assert holder.recovery_retry_tick == retry_tick
    assert simulation.remove_entity("factory", "OPEN_BRIDGE").accepted
    _advance_with_clearance(simulation, 100)
    assert holder.position.distance_to(holder.hold_anchor) <= 0.05
    assert holder.recovery_blocked_cells is None


def test_closed_throat_keeps_the_queue_safe_without_forcing_passage() -> None:
    simulation = _contact_queue(throat=True, closed=True)
    # The last stationary body occupies the goal; the one-cell throat cannot
    # fit passing discs, so the order can wait instead of exchanging identities.
    assert simulation.execute(MoveCommand(("unit_0",), Point(6.5, 5.5))).accepted
    _advance_with_clearance(simulation, 80)
    assert all(key in simulation.entities for key in ("unit_0", "unit_1", "unit_2", "unit_3"))
    assert not any(entity.hold_anchor for entity in simulation.entities.values())
    assert simulation.entities["unit_0"].path
    assert [simulation.entities[f"unit_{i}"].position.x for i in range(4)] == sorted(
        simulation.entities[f"unit_{i}"].position.x for i in range(4)
    )


def test_hold_recovery_retains_opportunistic_fire_without_enemy_pursuit() -> None:
    simulation = _contact_queue(2)
    enemy = simulation.entities["unit_1"]
    enemy.owner_id = "enemy"
    enemy.position = Point(9.5, 5.5)
    simulation.occupancy.move(enemy.entity_id, enemy.occupied_cells)
    holder = simulation.entities["unit_0"]
    anchor = holder.position
    assert simulation.execute(HoldPositionCommand((holder.entity_id,))).accepted
    holder.position = Point(5.5, 5.5)
    simulation.occupancy.move(holder.entity_id, holder.occupied_cells)
    _advance_with_clearance(simulation, 20)
    assert holder.hold_anchor == anchor
    assert holder.state is UnitState.HOLDING and not holder.pursue_target and not holder.path
    assert holder.position.distance_to(anchor) <= 0.05
    assert any(
        event.subject_id == holder.entity_id and event.event_type is EventType.PROJECTILE_LAUNCHED
        for event in simulation.events.events
    )


def test_rejected_control_replacement_preserves_hold_recovery() -> None:
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(7.5, 5.5))
    assert not simulation.execute(MoveCommand(("holder",), Point(1.5, 1.5))).accepted

    _advance_with_clearance(simulation, 60)

    assert simulation.entities["holder"].state is UnitState.HOLDING
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05


def test_occupied_hold_anchor_waits_safely_then_recovers_when_cleared() -> None:
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(7.5, 5.5))
    mover = simulation.entities["mover"]
    mover.position = anchor
    simulation.occupancy.move("mover", mover.occupied_cells, frozenset(simulation.entities))
    # The anchors now conflict; neither identity gets guaranteed priority over safety.
    assert simulation.execute(HoldPositionCommand(("mover",))).accepted

    _advance_with_clearance(simulation, 60)
    assert simulation.navigation_field_build_count <= 1

    assert all(entity.state is UnitState.HOLDING for entity in simulation.entities.values())
    assert (
        max(entity.position.distance_to(anchor) for entity in simulation.entities.values()) >= 0.3
    )
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 9.5))).accepted
    _advance_with_clearance(simulation, 100)
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05


@pytest.mark.parametrize("target", [Point(8.5, 8.5), Point(11.5, 11.5)], ids=["open", "edge"])
def test_group_route_metadata_ends_at_each_assigned_destination(
    make_map: Callable[[int], GameMap],
    target: Point,
) -> None:
    simulation = Simulation(make_map(3))
    ids = tuple(simulation.entities)
    assert simulation.execute(MoveCommand(ids, target)).accepted
    events = {
        event.subject_id: event
        for event in simulation.events.events
        if event.event_type.value == "path_computed"
    }
    translations = set()
    for key in ids:
        entity = simulation.entities[key]
        assert entity.move_target is not None
        translations.add(
            (
                round(entity.move_target.x - entity.position.x, 9),
                round(entity.move_target.y - entity.position.y, 9),
            )
        )
        assert 0 < entity.move_target.x < simulation.game_map.width
        assert 0 < entity.move_target.y < simulation.game_map.height
        assert entity.path[-1] == entity.move_target
        assert events[key].details["cells"][-1] == list(
            simulation.game_map.cell_for(entity.move_target)
        )

    assert len(translations) == 1


def test_arrival_rounding_cannot_snap_through_a_held_collider(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    holder = simulation.entities["unit_02"]
    mover = simulation.entities["unit_01"]
    assert simulation.execute(HoldPositionCommand((holder.entity_id,))).accepted
    target = Point(holder.position.x, holder.position.y - 0.68 + 0.0000005)
    assert simulation.execute(MoveCommand((mover.entity_id,), target)).accepted
    _advance_with_clearance(simulation, 10)
    assert mover.position.distance_to(holder.position) >= 0.68 - 1e-9
    assert mover.path or mover.position.distance_to(target) <= 1e-9
