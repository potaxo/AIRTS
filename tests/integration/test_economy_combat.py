"""Income, spawning, weapon range, and projectile behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from tests.integration.scenarios import (
    economy_combat_simulation,
    gated_attack_simulation,
    interaction_simulation,
)

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    defend_parameters,
)
from airts.contracts.commands import (
    AttackCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateRepairAndReturnCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    RemoveEntityCommand,
    SetDeveloperModeCommand,
    SetSelectionCommand,
    StopCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PointTarget, PolylineTarget
from airts.navigation.collision.geometry import collision_radius
from airts.simulation import Simulation
from airts.systems import automation_execution
from airts.world.entities import Projectile, UnitState
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def support_world() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "nearby_support",
                "name": "Nearby support",
                "width": 40,
                "height": 30,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "shooter", "kind": "scout", "position": [12.5, 10.5]},
                    {"id": "support", "kind": "scout", "position": [5.5, 10.5]},
                    {"id": "builder", "kind": "builder", "position": [7.5, 10.5]},
                    {"id": "distant", "kind": "scout", "position": [1.5, 1.5]},
                    {"id": "held", "kind": "scout", "position": [6.5, 8.5]},
                    {"id": "enemy", "kind": "builder", "owner": "enemy", "position": [16.5, 10.5]},
                ],
            }
        )
    )


def test_support_requires_an_actual_shot_and_preserves_builder_and_auxiliary_exclusion() -> None:
    simulation = support_world()
    simulation.entities["shooter"].attack_cooldown = 3
    simulation.advance()
    assert not simulation.entities["support"].pursue_target
    assert not simulation.events.query(event_types=frozenset({EventType.COMBAT_SUPPORT_JOINED}))
    simulation.advance(2)
    assert simulation.entities["support"].pursue_target


@pytest.mark.parametrize("order", ["idle", "move", "patrol", "defend"])
def test_actual_shot_enlists_nearby_combat_units_and_preserves_their_work(
    order: str, tmp_path: Path
) -> None:
    simulation = support_world()
    support = simulation.entities["support"]
    destination = Point(5.5, 24.5)
    assert simulation.execute(HoldPositionCommand(("held",))).accepted
    if order == "move":
        assert simulation._execute_command(
            MoveCommand(("support",), destination), record=False
        ).accepted
    elif order == "patrol":
        assert simulation._execute_command(
            CreatePatrolCommand(
                ("support",), PolylineTarget((Point(5.5, 10.5), Point(22.5, 10.5)))
            ),
            record=False,
        ).accepted
    elif order == "defend":
        assert simulation._execute_command(
            CreateDefendCommand(("support",), PointTarget(Point(10, 10.5))),
            record=False,
        ).accepted
    assignment = simulation.assignments.get("support")
    simulation.advance()
    assert support.pursue_target and support.attack_target_id == "enemy"
    assert simulation.events.query(
        event_types=frozenset({EventType.COMBAT_SUPPORT_JOINED}), subject_id="support"
    )
    assert not simulation.entities["builder"].pursue_target
    assert not simulation.entities["distant"].pursue_target
    assert simulation.entities["held"].hold_anchor == Point(6.5, 8.5)
    assert simulation.assignments.get("support") == assignment
    save_simulation(simulation, tmp_path / "support.json")
    loaded = load_simulation(tmp_path / "support.json")
    for world in (simulation, loaded):
        assert world.remove_entity("enemy").accepted
        world.advance(120)
    if order == "move":
        assert support.position == destination
    assert simulation.export_state() == loaded.export_state()
    if order == "idle":
        assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


def test_player_micro_window_refreshes_on_queued_moves_survives_save_and_expires(
    tmp_path: Path,
) -> None:
    simulation = support_world()
    simulation.advance()
    support = simulation.entities["support"]
    assert support.pursue_target  # Manual Stop must cancel an already active support pursuit.
    assert simulation.execute(StopCommand(("support",))).accepted
    assert simulation.execute(HoldPositionCommand(("held",))).accepted
    assert support.last_player_control_tick == 1
    simulation.advance(20)
    assert not support.pursue_target
    assert simulation.execute(MoveCommand(("support",), Point(5.6, 10.5), queued=True)).accepted
    assert support.last_player_control_tick == 21
    assert simulation.execute(SetSelectionCommand(entity_ids=("support",))).accepted
    assert not simulation.execute(MoveCommand(("support",), Point(-1, -1))).accepted
    assert support.last_player_control_tick == 21
    assert simulation.entities["shooter"].last_player_control_tick is None
    assert simulation.entities["distant"].last_player_control_tick is None
    save_simulation(simulation, tmp_path / "micro.json")
    restored = load_simulation(tmp_path / "micro.json")
    for world in (simulation, restored):
        for _ in range(Simulation.COMBAT_SUPPORT_MANUAL_COOLDOWN_TICKS - 1):
            world.advance()
            assert not world.entities["support"].pursue_target
        assert not world.events.query(
            event_types=frozenset({EventType.COMBAT_SUPPORT_JOINED}),
            subject_id="support",
            since_tick=2,
        )
        world.advance()
        assert world.entities["support"].pursue_target
        assert world.entities["support"].attack_target_id == "enemy"
        assert world.entities["held"].hold_anchor == Point(6.5, 8.5)
        assert not world.entities["held"].pursue_target
        assert not world.events.query(
            event_types=frozenset({EventType.COMBAT_SUPPORT_JOINED}),
            subject_id="held",
            since_tick=2,
        )
    assert restored.export_state() == simulation.export_state()
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


def test_hold_stays_out_of_support_after_timeout_and_new_move_releases_it() -> None:
    data = support_world().game_map.to_dict()
    data["entities"] = [
        item
        if item["id"] != "enemy"
        else {"id": "enemy", "kind": "factory", "owner": "enemy", "position": [15, 10]}
        for item in data["entities"]
    ]
    simulation = Simulation(load_map_data(data))
    assert simulation.execute(HoldPositionCommand(("support",))).accepted
    simulation.advance(2 * Simulation.COMBAT_SUPPORT_MANUAL_COOLDOWN_TICKS)
    support = simulation.entities["support"]
    assert support.hold_anchor == Point(5.5, 10.5)
    assert not support.pursue_target
    assert not simulation.events.query(
        event_types=frozenset({EventType.COMBAT_SUPPORT_JOINED}),
        subject_id="support",
    )
    assert simulation.execute(MoveCommand(("support",), Point(5.6, 10.5))).accepted
    assert support.hold_anchor is None
    simulation.advance(Simulation.COMBAT_SUPPORT_MANUAL_COOLDOWN_TICKS - 1)
    assert not support.pursue_target
    simulation.advance(11)
    assert support.pursue_target
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


@pytest.mark.parametrize("replacement", ["pause", "retarget"])
def test_player_automation_control_cancels_existing_defense_support_pursuit(
    replacement: str,
) -> None:
    simulation = support_world()
    created = simulation._execute_command(
        CreateDefendCommand(("support",), PointTarget(Point(10, 10.5))),
        record=False,
    )
    assert created.accepted and created.automation_id is not None
    simulation.advance()
    assert simulation.entities["support"].pursue_target
    command = (
        PauseAutomationCommand(created.automation_id)
        if replacement == "pause"
        else ModifyAutomationCommand(created.automation_id, target=PointTarget(Point(7.5, 24.5)))
    )
    assert simulation.execute(command).accepted
    unit = simulation.entities["support"]
    assert not unit.pursue_target and not unit.path
    assert unit.last_player_control_tick == 1
    simulation.advance(Simulation.COMBAT_SUPPORT_MANUAL_COOLDOWN_TICKS + 10)
    assert not unit.pursue_target
    if replacement == "pause":
        assert not unit.path
        assert simulation.automations[created.automation_id].status is AutomationStatus.PAUSED
    else:
        assert unit.position.distance_to(Point(7.5, 24.5)) < 2


def test_player_control_suppresses_only_commanded_units_and_background_control_does_not() -> None:
    manual = support_world()
    assert manual.execute(MoveCommand(("support",), Point(5.5, 20.5))).accepted
    manual.advance()
    assert not manual.entities["support"].pursue_target
    assert manual.entities["support"].path
    assert manual.entities["held"].pursue_target  # This unit has not actually received Hold here.
    assert manual.entities["held"].last_player_control_tick is None
    background = support_world()
    assert background._execute_command(StopCommand(("support",)), record=False).accepted
    background.advance()
    assert background.entities["support"].pursue_target
    assert background.entities["support"].last_player_control_tick is None


def test_destroyed_ordered_target_converts_survivors_to_gathering_and_continues_after_save(
    tmp_path: Path,
) -> None:
    simulation = support_world()
    ids = ("shooter", "support")
    assert simulation.execute(AttackCommand(ids, "enemy")).accepted
    simulation.advance()
    save_simulation(simulation, tmp_path / "attack.json")
    loaded = load_simulation(tmp_path / "attack.json")
    for world in (simulation, loaded):
        for _ in range(160):
            world.advance()
            if "enemy" not in world.entities:
                break
        assert "enemy" not in world.entities
        assert all(world.entities[key].move_queue for key in ids)
        assert all(world.entities[key].state is UnitState.MOVING for key in ids)
        world.advance(120)
        assert all(world.entities[key].position.distance_to(Point(16.5, 10.5)) < 2 for key in ids)
    assert simulation.export_state() == loaded.export_state()
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


def test_unreachable_attackers_leave_route_allowance_for_reachable_members() -> None:
    simulation, blocked, reachable = gated_attack_simulation()
    starts = {key: simulation.entities[key].position for key in blocked + reachable}
    assert simulation.execute(AttackCommand(blocked + reachable, "target")).accepted
    for _ in range(12):
        simulation.advance()
        assert simulation._routes.combat_route_count <= Simulation.COMBAT_PATH_BUDGET
    assert all(simulation.entities[key].position == starts[key] for key in blocked)
    assert all(simulation.entities[key].state is UnitState.WAITING for key in blocked)
    assert simulation.entities[reachable[0]].position != starts[reachable[0]]
    failures = simulation.events.query(event_types=frozenset({EventType.PATHFINDING_FAILED}))
    assert {event.subject_id for event in failures} == set(blocked)
    assert len(failures) == len(blocked)
    assert simulation.execute(RemoveEntityCommand("gate")).accepted
    simulation.advance(4)
    assert all(simulation.entities[key].position != starts[key] for key in blocked)


def test_combat_route_admission_rotates_and_dispatches_every_pending_member() -> None:
    simulation, _, reachable = gated_attack_simulation(0, 48)
    assert simulation.execute(AttackCommand(reachable[::-1], "target")).accepted
    for _ in range(4):
        simulation._routes.clear()
        simulation.advance()
        assert simulation._routes.combat_route_count <= Simulation.COMBAT_PATH_BUDGET
    dispatched = {
        event.subject_id
        for event in simulation.events.query(event_types=frozenset({EventType.PATH_COMPUTED}))
        if event.details["source"] == "combat"
    }
    assert dispatched == set(reachable)


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


def test_seeded_ambient_spawn_checks_disc_capacity_in_adjacent_cells() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "ambient_adjacent_disc",
                "name": "Ambient adjacent disc",
                "width": 10,
                "height": 1,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "player", "kind": "scout", "position": [0.5, 0.5]},
                    {
                        "id": "blocker",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [8.99, 0.5],
                    },
                ],
            }
        ),
        random_seed=1,
        ambient_enemy_spawns=True,
        enemy_spawn_interval_ticks=1,
    )
    simulation.advance()
    spawned = simulation.entities["enemy_tank_001"]
    blocker = simulation.entities["blocker"]
    assert spawned.position.distance_to(blocker.position) >= (
        collision_radius(spawned.kind) + collision_radius(blocker.kind) - 1e-8
    )
    assert simulation.collision_pair_check_count > 0


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
def test_move_patrol_and_defend_units_all_fire_in_range(
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
    if isinstance(command, CreatePatrolCommand):
        assert simulation.entities["unit"].pursue_target
        assert not simulation.entities["unit"].path
    else:
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


def test_developer_mode_bonus_is_player_only_and_resets_when_disabled() -> None:
    simulation = interaction_simulation()
    assert not simulation.developer_mode
    enemy_balance = simulation.resources["enemy"]
    simulation.advance(3)
    assert simulation.execute(SetDeveloperModeCommand(True)).accepted
    simulation.advance(9)
    assert simulation.resources["player"] == 1500  # Ordinary extractor income only.
    assert simulation.execute(SetDeveloperModeCommand(True)).accepted  # Preserve timer.
    simulation.advance()
    assert simulation.resources["player"] == 3500
    simulation.advance(20)
    assert simulation.resources["player"] == 9500
    assert simulation.resources["enemy"] == enemy_balance
    bonuses = [
        event
        for event in simulation.events.events
        if event.details.get("reason") == "DEVELOPER_MODE_INCOME"
    ]
    assert [event.tick for event in bonuses] == [13, 23, 33]
    assert all(event.details["amount"] == 2000 for event in bonuses)

    simulation.advance(9)
    assert simulation.execute(SetDeveloperModeCommand(False)).accepted
    assert simulation.developer_income_ticks == 0
    simulation.advance(20)
    assert simulation.resources["player"] == 12500  # Normal income continues.
    assert simulation.execute(SetDeveloperModeCommand(True)).accepted
    simulation.advance(9)
    assert simulation.resources["player"] == 13500
    simulation.advance()
    assert simulation.resources["player"] == 15500
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


def test_developer_mode_rejects_other_owners_without_changing_the_setting() -> None:
    simulation = interaction_simulation()
    assert simulation.execute(SetDeveloperModeCommand(True)).accepted
    simulation.advance(4)
    for owner_id in ("enemy", "unknown"):
        result = simulation.execute(SetDeveloperModeCommand(False, owner_id))
        assert not result.accepted
        assert result.reason == "DEVELOPER_MODE_PLAYER_ONLY"
        assert simulation.developer_mode
        assert simulation.developer_income_ticks == 4


def test_resource_generators_produce_without_an_automation() -> None:
    simulation = interaction_simulation()

    simulation.advance(20)

    assert simulation.resources["player"] == 2500


def test_defenders_keep_their_route_when_firing_at_an_enemy_far_from_their_station() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "defend_opportunistic_fire",
                "name": "Defend travel under opportunistic fire",
                "width": 48,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "defender",
                        "kind": "heavy_tank",
                        "owner": "player",
                        "position": [5.5, 5.5],
                    },
                    {"id": "enemy", "kind": "heavy_tank", "owner": "enemy", "position": [8.5, 5.5]},
                ],
            }
        )
    )
    result = simulation.execute(CreateDefendCommand(("defender",), PointTarget(Point(40.5, 5.5))))
    assert result.accepted
    simulation.advance()
    defender = simulation.entities["defender"]
    assert defender.attack_target_id == "enemy"
    assert not defender.pursue_target
    first_route = tuple(defender.path)
    first_position = defender.position
    simulation.advance()
    assert defender.position != first_position
    assert tuple(defender.path) in (first_route, first_route[1:])
    routes = simulation.events.query(
        event_types=frozenset({EventType.PATH_COMPUTED}), subject_id="defender"
    )
    assert len(routes) == 1
    assert simulation.events.query(
        event_types=frozenset({EventType.PROJECTILE_LAUNCHED}), subject_id="defender"
    )


def test_simultaneous_casualties_coordinate_survivors_once_and_continue_after_load(
    tmp_path: Path,
) -> None:
    world = load_map_data(
        {
            "id": "batch_casualties",
            "name": "Batch casualties",
            "width": 40,
            "height": 30,
            "terrain": {"default": "grass"},
            "entities": [
                {"id": f"unit_{i}", "kind": "scout", "position": [2.5 + i, 2.5]} for i in range(8)
            ],
        }
    )
    simulation = Simulation(world)
    ids = tuple(simulation.entities)
    result = simulation.execute(
        CreateDefendCommand(ids, PointTarget(Point(20.5, 20.5)), gathering_point=True)
    )
    assert result.accepted
    survivor = simulation.entities[ids[-1]]
    survivor.attack_target_id, survivor.pursue_target = ids[0], True
    survivor.last_attacker_id, survivor.last_attacked_tick = ids[1], simulation.tick
    for index, key in enumerate(ids[:4]):
        point = simulation.entities[key].position
        simulation.projectiles[str(index)] = Projectile(
            str(index), "enemy", key, "enemy", EntityKind.SCOUT, point, point, 1000, 1000
        )
    with patch.object(
        automation_execution,
        "coordinate_shared_defend_stations",
        wraps=automation_execution.coordinate_shared_defend_stations,
    ) as coordinate:
        simulation.advance()
    assert coordinate.call_count == 1
    assert set(simulation.entities) == set(ids[4:])
    assert survivor.attack_target_id is None and not survivor.pursue_target
    assert survivor.last_attacker_id is None and survivor.last_attacked_tick is None
    removed = [
        event for event in simulation.events.events if event.event_type is EventType.ENTITY_REMOVED
    ]
    assert [event.subject_id for event in removed] == list(ids[:4])
    stations = defend_parameters(simulation.automations[result.automation_id or ""]).stations
    assert set(stations) == set(ids[4:]) and len(set(stations.values())) == 4
    assert simulation._removal_batch is None and not simulation._removal_formations
    path = tmp_path / "casualties.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    simulation.advance(20)
    restored.advance(20)
    assert restored.snapshot() == simulation.snapshot()
    assert [event.to_dict() for event in restored.events.events] == [
        event.to_dict() for event in simulation.events.events
    ]
    # A direct removal remains complete before its command returns.
    with patch.object(
        automation_execution,
        "coordinate_shared_defend_stations",
        wraps=automation_execution.coordinate_shared_defend_stations,
    ) as coordinate:
        assert simulation.execute(RemoveEntityCommand(ids[4])).accepted
    assert coordinate.call_count == 1
    final_stations = defend_parameters(simulation.automations[result.automation_id or ""]).stations
    assert set(final_stations) == set(ids[5:])
