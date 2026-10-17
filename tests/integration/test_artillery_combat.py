"""Fixed artillery impacts, splash falloff, casualties and save/replay continuation."""

from pathlib import Path

import pytest

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.commands import AttackCommand, MoveCommand, RemoveEntityCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def artillery_simulation() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "artillery_splash",
                "name": "Artillery splash",
                "width": 32,
                "height": 24,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "gun", "kind": "artillery", "position": [2.5, 10.5]},
                    {"id": "target", "kind": "builder", "owner": "enemy", "position": [14.5, 10.5]},
                    {"id": "near", "kind": "builder", "owner": "enemy", "position": [13, 10.5]},
                    {"id": "far", "kind": "builder", "owner": "enemy", "position": [12, 10.5]},
                    {"id": "edge", "kind": "builder", "owner": "enemy", "position": [14.5, 13.5]},
                    {
                        "id": "outside",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [14.5, 14.5],
                    },
                    {"id": "friend", "kind": "builder", "position": [14.5, 12]},
                    {
                        "id": "building",
                        "kind": "matter_synthesizer",
                        "owner": "enemy",
                        "position": [15, 8],
                    },
                ],
            }
        )
    )


@pytest.mark.parametrize(
    ("kind", "speed"),
    [
        (EntityKind.SCOUT, 24),
        (EntityKind.LIGHT_TANK, 20),
        (EntityKind.HEAVY_TANK, 14),
        (EntityKind.TURRET, 20),
    ],
)
def test_ordinary_weapons_launch_at_doubled_speed(kind: EntityKind, speed: float) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "ordinary_speed",
                "name": "Ordinary speed",
                "width": 16,
                "height": 16,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "gun", "kind": kind.value, "position": [2, 2]},
                    {"id": "target", "kind": "builder", "owner": "enemy", "position": [5.5, 2.5]},
                ],
            }
        )
    )
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance()
    projectile = next(iter(simulation.projectiles.values()))
    start = projectile.position
    assert projectile.speed == speed
    assert projectile.weapon_kind.profile.splash_radius == 0
    simulation.advance()
    assert projectile.position.distance_to(start) == pytest.approx(speed * simulation.TICK_SECONDS)


def test_shell_splash_falls_off_by_center_distance_and_spares_friendlies() -> None:
    simulation = artillery_simulation()
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance()
    shell = next(iter(simulation.projectiles.values()))
    assert shell.speed == EntityKind.LIGHT_TANK.profile.projectile_speed / 2
    simulation.advance(12)
    assert shell.projectile_id not in simulation.projectiles
    assert simulation.entities["target"].health == 40  # 40 at the center.
    assert simulation.entities["near"].health == 60  # 20 at half radius.
    assert simulation.entities["far"].health == 73  # ceil(40 / 6).
    assert simulation.entities["building"].health == 389  # Center distance sqrt(5).
    for key in ("edge", "outside", "friend"):
        assert simulation.entities[key].health == 80
    for key in ("target", "near", "far", "building"):
        assert simulation.entities[key].last_attacker_id == "gun"
        assert simulation.entities[key].last_attacked_tick == 13
    impacts = simulation.events.query(event_types=frozenset({EventType.PROJECTILE_IMPACT}))
    assert len(impacts) == 1
    assert impacts[0].details["position"] == [14.5, 10.5]
    assert impacts[0].details["splash_radius"] == 3
    assert simulation.projectile_traces[-1].points[-1] == Point(14.5, 10.5)
    run_replay(capture_replay(simulation))


def test_moving_target_can_escape_fixed_shell_and_continue_across_save(tmp_path: Path) -> None:
    simulation = artillery_simulation()
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance()
    shell = next(iter(simulation.projectiles.values()))
    destination = shell.destination
    # Issue the enemy move through its normal command boundary.
    assert simulation._execute_command(
        MoveCommand(("target",), Point(14.5, 2.5), owner_id="enemy"), record=False
    ).accepted
    simulation.advance(4)
    assert shell.destination == destination
    assert shell.position.y == destination.y
    assert simulation.entities["target"].position != destination
    path = tmp_path / "flying-shell.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    for current in (simulation, restored):
        current.advance(8)
        assert current.entities["target"].position.distance_to(destination) > 3
        assert current.entities["target"].health == 80
        assert current.entities["near"].health == 60
        assert current.projectile_traces[-1].points[-1] == destination
    assert restored.export_state() == simulation.export_state()
    # Impact feedback persists through save/load and expires exactly once.
    save_simulation(simulation, path)
    restored = load_simulation(path)
    assert restored.projectile_traces == simulation.projectile_traces
    for current in (simulation, restored):
        current.advance(10)
        assert not current.projectile_traces


def test_shell_explodes_after_source_and_target_removal_and_cleans_up_casualties(
    tmp_path: Path,
) -> None:
    simulation = artillery_simulation()
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance()
    for key in ("gun", "target"):
        assert simulation.execute(RemoveEntityCommand(key)).accepted
    simulation.entities["near"].health = 20
    simulation.entities["far"].health = 7
    simulation.advance(12)
    assert "near" not in simulation.entities
    assert "far" not in simulation.entities
    assert simulation.entities["building"].health == 389
    assert len(simulation.projectile_traces) == 1
    assert len(simulation.events.query(event_types=frozenset({EventType.ENTITY_DESTROYED}))) == 2
    assert simulation._removal_batch is None
    assert simulation.entities["building"].last_attacker_id is None
    assert simulation.entities["building"].last_attacked_tick is None
    path = tmp_path / "destroyed-source-impact.json"
    save_simulation(simulation, path)
    assert load_simulation(path).export_state() == simulation.export_state()


def test_shell_empty_impact_still_emits_feedback() -> None:
    simulation = artillery_simulation()
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance()
    for key in ("target", "near", "far", "building"):
        assert simulation.execute(RemoveEntityCommand(key)).accepted
    simulation.advance(12)
    assert not simulation.projectiles
    assert len(simulation.projectile_traces) == 1
    assert simulation.events.query(event_types=frozenset({EventType.PROJECTILE_IMPACT}))
    assert not simulation.events.query(event_types=frozenset({EventType.COMBAT_ATTACK}))
