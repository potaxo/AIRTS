"""Income, spawning, weapon range, and projectile behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.scenarios import (
    economy_combat_simulation,
    interaction_simulation,
)

from airts.adapters.persistence import load_simulation, save_simulation
from airts.automations import (
    AutomationKind,
    AutomationStatus,
)
from airts.commands import (
    AttackCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    RemoveEntityCommand,
)
from airts.events import EventType
from airts.geometry import Point, PointTarget
from airts.simulation import Simulation
from airts.world.map_model import EntityKind, load_map_data
from airts.world.projectiles import Projectile


def test_economy_automation_collects_generator_income_to_target() -> None:
    simulation = economy_combat_simulation()
    result = simulation.execute(CreateEconomyCommand(("generator",), 1500))
    simulation.advance(10)
    automation = simulation.automations[result.automation_id or ""]

    assert simulation.resources["player"] == 1500
    assert automation.kind is AutomationKind.ECONOMY
    assert automation.status is AutomationStatus.COMPLETED
    assert automation.parameters.to_dict()["collected"] == 1000


def test_seeded_enemy_tank_reinforcements_spawn_on_the_right_each_second() -> None:
    first = Simulation(
        economy_combat_simulation().game_map,
        random_seed=19,
        ambient_enemy_spawns=True,
    )
    second = Simulation(
        economy_combat_simulation().game_map,
        random_seed=19,
        ambient_enemy_spawns=True,
    )

    first.advance(30)
    second.advance(30)

    spawned = first.events.query(event_types=frozenset({EventType.ENEMY_REINFORCEMENT_SPAWNED}))
    assert len(spawned) == 3
    assert all(event.details["position"][0] >= 11.5 for event in spawned)
    assert {event.details["kind"] for event in spawned} <= {
        EntityKind.LIGHT_TANK.value,
        EntityKind.HEAVY_TANK.value,
    }
    assert first.snapshot() == second.snapshot()


def test_enemy_reinforcement_interval_and_cap_limit_world_growth() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "enemy_cap",
                "name": "Enemy Cap",
                "width": 40,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "base",
                        "kind": "command_center",
                        "owner": "player",
                        "position": [1, 4],
                    },
                    {
                        "id": "initial_enemy",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [38.5, 10.5],
                    },
                ],
            }
        ),
        random_seed=19,
        ambient_enemy_spawns=True,
        enemy_spawn_interval_ticks=20,
        enemy_spawn_cap=2,
    )

    simulation.advance(80)

    spawned = simulation.events.query(
        event_types=frozenset({EventType.ENEMY_REINFORCEMENT_SPAWNED})
    )
    assert [event.tick for event in reversed(spawned)] == [20]
    assert (
        sum(
            entity.owner_id == "enemy" and entity.is_movable
            for entity in simulation.entities.values()
        )
        == 2
    )


def test_attack_damage_destruction_and_repair_only_when_requested() -> None:
    simulation = economy_combat_simulation()
    assert simulation.execute(AttackCommand(("tank",), "enemy")).accepted
    simulation.advance()
    assert simulation.entities["enemy"].health == 60
    assert simulation.projectiles
    assert simulation.events.query(event_types=frozenset({EventType.PROJECTILE_LAUNCHED}))
    player_projectile = next(
        projectile
        for projectile in simulation.projectiles.values()
        if projectile.source_entity_id == "tank"
    )
    launch_position = player_projectile.position

    simulation.advance()
    assert player_projectile.position != launch_position
    assert len(player_projectile.trajectory) == 2

    simulation.advance()
    assert simulation.entities["enemy"].health == 48
    assert simulation.events.query(event_types=frozenset({EventType.COMBAT_ATTACK}))
    assert simulation.events.query(event_types=frozenset({EventType.PROJECTILE_IMPACT}))
    assert simulation.projectile_traces

    simulation.entities["enemy"].health = 5
    simulation.entities["tank"].attack_cooldown = 0
    simulation.advance(3)
    assert "enemy" not in simulation.entities
    assert simulation.events.query(event_types=frozenset({EventType.ENTITY_DESTROYED}))

    simulation.entities["tank"].health = 17
    simulation.advance()
    assert "tank" not in simulation.assignments
    assert not any(
        automation.kind is AutomationKind.REPAIR_AND_RETURN
        for automation in simulation.automations.values()
    )
    assert not simulation.events.query(event_types=frozenset({EventType.RETREAT_STARTED}))

    requested = simulation.execute(CreateRepairAndReturnCommand(("tank",)))
    assigned = simulation.automations[simulation.assignments["tank"]]
    assert requested.accepted
    assert assigned.kind is AutomationKind.REPAIR_AND_RETURN
    assert assigned.title == "Repair And Return"


def test_projectile_finishes_at_last_target_position_after_target_is_destroyed(
    tmp_path: Path,
) -> None:
    simulation = economy_combat_simulation()
    destination = simulation.entities["enemy"].selection_position
    projectile = Projectile(
        projectile_id="projectile_orphan",
        source_entity_id="tank",
        target_entity_id="enemy",
        owner_id="player",
        weapon_kind=EntityKind.LIGHT_TANK,
        position=Point(2.5, 2.5),
        destination=destination,
        damage=EntityKind.LIGHT_TANK.profile.attack_damage,
        speed=5.0,
    )
    simulation.projectiles[projectile.projectile_id] = projectile
    assert simulation.execute(RemoveEntityCommand("enemy", "TEST_DESTROYED")).accepted

    simulation.advance()

    assert projectile.projectile_id in simulation.projectiles
    assert projectile.position != destination

    save_path = tmp_path / "orphan-projectile.json"
    save_simulation(simulation, save_path)
    restored = load_simulation(save_path)
    restored_projectile = restored.projectiles[projectile.projectile_id]
    assert restored_projectile.destination == destination

    simulation.advance(3)
    restored.advance(3)

    assert projectile.projectile_id not in simulation.projectiles
    trace = next(
        item
        for item in simulation.projectile_traces
        if item.projectile_id == projectile.projectile_id
    )
    assert trace.points[-1] == destination
    assert restored.snapshot() == simulation.snapshot()


@pytest.mark.parametrize(
    "kind",
    [EntityKind.SCOUT, EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK],
    ids=lambda kind: kind.value,
)
def test_explicit_attack_moves_then_holds_at_weapon_range_and_fires(kind: EntityKind) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "attack_move",
                "name": "Attack Move",
                "width": 18,
                "height": 8,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "attacker",
                        "kind": kind.value,
                        "owner": "player",
                        "position": [2.5, 3.5],
                    },
                    {
                        "id": "target",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [10.5, 3.5],
                    },
                ],
            }
        )
    )
    initial = simulation.entities["attacker"].position
    assert initial.distance_to(simulation.entities["target"].position) > kind.profile.attack_range
    assert simulation.execute(AttackCommand(("attacker",), "target")).accepted

    for _ in range(20):
        simulation.advance()
        if simulation.events.query(
            event_types=frozenset({EventType.PROJECTILE_LAUNCHED}),
            subject_id="attacker",
        ):
            break
    attacker = simulation.entities["attacker"]
    firing_position = attacker.position

    assert simulation.events.query(
        event_types=frozenset({EventType.PROJECTILE_LAUNCHED}), subject_id="attacker"
    ), f"{kind.value} reached range but did not fire"
    assert firing_position != initial
    assert not attacker.path
    assert attacker.move_target is None
    assert firing_position.distance_to(simulation.entities["target"].position) <= (
        attacker.kind.profile.attack_range
    )
    simulation.advance()
    assert attacker.position == firing_position
    assert not attacker.path


@pytest.mark.parametrize(
    "command",
    (
        MoveCommand(("unit",), Point(14.5, 2.5)),
        CreatePatrolCommand(("unit",), PointTarget(Point(13.5, 4.5), radius=1)),
        CreateDefendCommand(("unit",), PointTarget(Point(4.5, 4.5), radius=1)),
    ),
    ids=["move", "patrol", "defend"],
)
def test_move_patrol_and_defend_units_all_fire_opportunistically_in_range(
    command: MoveCommand | CreatePatrolCommand | CreateDefendCommand,
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "behavior_fire",
                "name": "Behavior Fire",
                "width": 18,
                "height": 8,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "unit",
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [2.5, 2.5],
                    },
                    {
                        "id": "enemy",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [7.5, 2.5],
                    },
                ],
            }
        )
    )
    assert simulation.execute(command).accepted

    simulation.advance()

    assert simulation.events.query(
        event_types=frozenset({EventType.PROJECTILE_LAUNCHED}),
        subject_id="unit",
    )
    assert simulation.entities["unit"].path


@pytest.mark.parametrize(
    ("kind", "damage"),
    [
        pytest.param(EntityKind.LIGHT_TANK, 12, id="light-tank"),
        pytest.param(EntityKind.HEAVY_TANK, 20, id="heavy-tank"),
    ],
)
def test_tank_projectiles_use_source_damage_and_never_damage_nearby_entities(
    kind: EntityKind, damage: int
) -> None:
    def combat_simulation(kind: EntityKind) -> Simulation:
        return Simulation(
            load_map_data(
                {
                    "id": f"{kind.value}_projectile",
                    "name": "Projectile Damage",
                    "width": 12,
                    "height": 12,
                    "terrain": {"default": "grass", "rectangles": []},
                    "entities": [
                        {
                            "id": "attacker",
                            "kind": kind.value,
                            "owner": "player",
                            "position": [2.5, 5.5],
                        },
                        {
                            "id": "target",
                            "kind": "heavy_tank",
                            "owner": "enemy",
                            "position": [5.5, 5.5],
                        },
                        {
                            "id": "bystander",
                            "kind": "heavy_tank",
                            "owner": "enemy",
                            "position": [5.5, 6.5],
                        },
                    ],
                }
            )
        )

    simulation = combat_simulation(kind)
    assert simulation.execute(AttackCommand(("attacker",), "target")).accepted
    simulation.advance(8)
    assert EntityKind.HEAVY_TANK.profile.max_health - simulation.entities["target"].health == damage
    assert simulation.entities["bystander"].health == EntityKind.HEAVY_TANK.profile.max_health


def test_resource_generators_produce_without_an_automation() -> None:
    simulation = interaction_simulation()

    simulation.advance(20)

    assert simulation.resources["player"] == 2500
