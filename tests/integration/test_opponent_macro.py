"""Surplus spending and connected base growth under real production and combat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from airts.contracts.automations import ConstructionParameters, ProductionParameters
from airts.contracts.commands import CreateConstructionCommand, SetAIIncomeMultiplierCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import economy, opponent_ai
from airts.world.map import Terrain, footprint_cells, load_map_data
from airts.world.opponents import SquadPhase
from airts.world.skirmish import create_skirmish_map
from airts.world.units import catalog
from airts.world.units.catalog import EntityKind


def developed_base(*, battle: bool = False) -> Simulation:
    entities: list[dict[str, object]] = [
        {"id": "human_factory", "kind": "factory", "position": [8, 8], "owner": "player"},
        {"id": "center", "kind": "command_center", "position": [154, 120], "owner": "ai"},
        {"id": "extractor", "kind": "resource_generator", "position": [168, 120], "owner": "ai"},
    ]
    entities.extend(
        {"id": f"factory_{i}", "kind": "factory", "position": [130 + i * 7, 110], "owner": "ai"}
        for i in range(4)
    )
    entities.extend(
        {
            "id": f"synth_{i}",
            "kind": "matter_synthesizer",
            "position": [130 + i * 5, 102],
            "owner": "ai",
        }
        for i in range(8)
    )
    entities.extend(
        {"id": f"worker_{i}", "kind": "builder", "position": [135.5 + i * 2, 124.5], "owner": "ai"}
        for i in range(6)
    )
    if battle:
        entities.extend(
            {
                "id": f"army_{i}",
                "kind": "heavy_tank" if i % 3 == 0 else "light_tank",
                "position": [116.5 + i % 12, 79.5 + i // 12],
                "owner": "ai",
            }
            for i in range(96)
        )
        entities.extend(
            {
                "id": f"enemy_{i}",
                "kind": "heavy_tank",
                "position": [96.5 + i % 8, 79.5 + i // 8],
                "owner": "player",
            }
            for i in range(72)
        )
        entities.append(
            {"id": "forward_factory", "kind": "factory", "position": [114, 94], "owner": "ai"}
        )
    simulation = Simulation(
        load_map_data(
            {
                "id": "developed-base",
                "name": "Developed base",
                "width": 192,
                "height": 160,
                "terrain": {
                    "default": "grass",
                    "rectangles": [[64, 0, 1, 160, "rock"], [65, 134, 127, 2, "road"]],
                },
                "resource_sites": [[168, 120], [170, 100], [170, 80], [86, 40], [28, 80]],
                "entities": entities,
                "match_slots": [
                    {"owner": "player", "controller": "player", "team": 1},
                    {"owner": "ai", "controller": "ai", "team": 2},
                ],
            }
        )
    )
    for entity in simulation.entities.values():
        if entity.owner_id == "ai" and entity.kind.upgrades:
            entity.upgrade.tier = 3
    economy.rebuild_income(simulation)
    simulation.resources["ai"] = 30000
    return simulation


@pytest.mark.parametrize("battle", [False, True], ids=["peaceful", "combat"])
def test_surplus_infills_the_base_and_funds_new_factory_output(
    battle: bool, record_property: Callable[[str, object], None]
) -> None:
    simulation = developed_base(battle=battle)
    opening = set(simulation.entities)
    combat_turns = 0
    peak_builders_working = 0
    for _ in range(90):
        simulation.advance(10)
        assert simulation._opponent_command_count <= opponent_ai.MAX_ORDERS
        combat_turns += any(
            squad.phase in {SquadPhase.ATTACKING, SquadPhase.DEFENDING}
            for squad in simulation.opponents["ai"].squads
        )
        peak_builders_working = max(
            peak_builders_working,
            sum(
                isinstance(job.parameters, ConstructionParameters)
                for job in simulation.live_automations
            ),
        )
    additions = [
        entity
        for entity in simulation.entities.values()
        if entity.owner_id == "ai"
        and entity.entity_id not in opening
        and not entity.is_movable
        and entity.is_complete
    ]
    nearby = [entity for entity in additions if entity.position.distance_to(Point(148, 114)) <= 32]
    record_property("nearby_additions", len(nearby))
    record_property("total_additions", len(additions))
    record_property("cash", simulation.resources["ai"])
    record_property("combat_turns", combat_turns)
    record_property("parallel_construction", peak_builders_working)
    assert len(nearby) >= 10
    assert {EntityKind.FACTORY, EntityKind.TURRET, EntityKind.MATTER_SYNTHESIZER}.issubset(
        {entity.kind for entity in additions}
    )
    assert any(entity.kind is EntityKind.REPAIR_HUB for entity in additions)
    assert peak_builders_working >= 4
    assert simulation.resources["ai"] < 20000
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.factory_id not in opening
        and job.parameters.produced_count > 0
        for job in simulation.automations.values()
    )
    assert all(
        simulation.game_map.terrain_at_cell(cell) not in {Terrain.ROAD, Terrain.BRIDGE}
        for entity in additions
        for cell in footprint_cells(entity.kind, entity.position)
    )
    # Every new factory retains a static route out of the growing city, not only
    # one nominal spawn cell. Existing roads and two-cell block streets stay open.
    blocked = simulation._building_cells()
    exits = tuple(Point(float(x) + 0.5, 134.5) for x in range(66, 192))
    for entity in additions:
        cells = footprint_cells(entity.kind, entity.position)
        if entity.kind is not EntityKind.RESOURCE_GENERATOR:
            assert all(
                x % opponent_ai.BASE_BLOCK_SIZE >= opponent_ai.BASE_STREET_WIDTH
                and y % opponent_ai.BASE_BLOCK_SIZE >= opponent_ai.BASE_STREET_WIDTH
                for x, y in cells
            )
        if entity.kind is EntityKind.FACTORY:
            width, height = entity.kind.profile.footprint
            approaches = (
                Point(float(x) + 0.5, float(y) + 0.5)
                for x in range(int(entity.position.x) - 1, int(entity.position.x) + width + 1)
                for y in range(int(entity.position.y) - 1, int(entity.position.y) + height + 1)
                if (x, y) not in cells and simulation.game_map.is_cell_passable((x, y))
            )
            assert any(
                simulation._routes.can_reach_any(point, exits, blocked) for point in approaches
            )
    if battle:
        assert combat_turns >= 10
        attacks = simulation.events.query(event_types=frozenset({EventType.COMBAT_ATTACK}))
        destroyed = simulation.events.query(event_types=frozenset({EventType.ENTITY_DESTROYED}))
        record_property("combat_attacks", len(attacks))
        record_property("combat_losses", len(destroyed))
        assert len(attacks) >= 20
        assert len(destroyed) >= 5


def test_base_rescue_and_occupied_blueprint_do_not_starve_funded_macro() -> None:
    simulation = developed_base(battle=True)
    simulation.entities["worker_0"].position = Point(123.5, 88.5)
    simulation.occupancy.move("worker_0", simulation.entities["worker_0"].occupied_cells)
    assert simulation.execute(
        CreateConstructionCommand("worker_0", EntityKind.TURRET, Point(124, 84), owner_id="ai")
    ).accepted
    simulation.advance(10)
    assert any(job.reason_code == "SITE_OCCUPIED" for job in simulation.live_automations)
    simulation.advance(10)
    assert any(squad.phase is SquadPhase.DEFENDING for squad in simulation.opponents["ai"].squads)
    assert any(
        isinstance(job.parameters, ProductionParameters) for job in simulation.live_automations
    )
    assert (
        sum(
            isinstance(job.parameters, ConstructionParameters)
            for job in simulation.live_automations
        )
        >= 2
    )
    assert simulation._opponent_command_count <= opponent_ai.MAX_ORDERS


@pytest.mark.parametrize(
    "production_ticks,expected", [(5, EntityKind.RESOURCE_GENERATOR), (160, EntityKind.FACTORY)]
)
def test_surplus_capacity_demand_adapts_to_configured_production_times(
    monkeypatch: pytest.MonkeyPatch, production_ticks: int, expected: EntityKind
) -> None:
    monkeypatch.setitem(
        catalog._ENTITY_PROFILES,
        EntityKind.LIGHT_TANK,
        replace(EntityKind.LIGHT_TANK.profile, production_ticks=production_ticks),
    )
    data = developed_base().game_map.to_dict()
    data["entities"].extend(
        {"id": f"gun_{i}", "kind": "turret", "position": [x, y], "owner": "ai"}
        for i, (x, y) in enumerate(
            ((128, 100), (148, 96), (168, 100), (128, 118), (152, 116), (172, 120))
        )
    )
    data["entities"].append(
        {"id": "repair", "kind": "repair_hub", "position": [112, 138], "owner": "ai"}
    )
    simulation = Simulation(load_map_data(data))
    for entity in simulation.entities.values():
        if entity.owner_id == "ai" and entity.kind.upgrades:
            entity.upgrade.tier = 3
    economy.rebuild_income(simulation)
    simulation.resources["ai"] = 30000
    simulation.advance(20)
    plans = [
        job.parameters.building_kind
        for job in simulation.live_automations
        if isinstance(job.parameters, ConstructionParameters)
    ]
    assert plans == [expected]


@pytest.mark.parametrize("multiplier", [1, 5])
def test_rich_ai_develops_all_three_default_river_bases(
    multiplier: int,
    record_property: Callable[[str, object], None],
) -> None:
    simulation = Simulation(create_skirmish_map((("player", 1), ("ai", 2), ("ai", 3), ("ai", 4))))
    assert simulation.execute(SetAIIncomeMultiplierCommand(multiplier)).accepted
    owners = ("ai_b", "ai_c", "ai_d")
    for owner in owners:
        simulation.resources[owner] = 30000
    simulation.advance(600)
    for owner in owners:
        own = [entity for entity in simulation.entities.values() if entity.owner_id == owner]
        buildings = [entity for entity in own if not entity.is_movable and entity.is_complete]
        record_property(f"{owner}_buildings", len(buildings))
        record_property(f"{owner}_cash", simulation.resources[owner])
        assert len(buildings) >= 10
        assert sum(entity.kind is EntityKind.FACTORY for entity in buildings) >= 3
        assert sum(entity.profile.can_build for entity in own) >= 6
        assert any(entity.kind is EntityKind.TURRET for entity in buildings)
        assert simulation.resources[owner] < 20000
        if multiplier == 5:
            assert sum(entity.kind is EntityKind.RESOURCE_GENERATOR for entity in buildings) >= 2


def test_fixed_resource_on_a_wide_bridge_remains_eligible_for_expansion() -> None:
    data = developed_base().game_map.to_dict()
    data["terrain"] = {
        "default": "grass",
        "rectangles": [[164, 128, 12, 20, "water"], [164, 132, 12, 12, "bridge"]],
    }
    data["resource_sites"] = [[168, 120], [170, 134]]
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 30000
    simulation.advance(600)
    assert any(
        entity.owner_id == "ai"
        and entity.kind is EntityKind.RESOURCE_GENERATOR
        and entity.position == Point(170, 134)
        for entity in simulation.entities.values()
    )
