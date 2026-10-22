"""FIFO execution of direct movement, attack and builder repair orders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.commands import AttackCommand, RepairBuildingCommand
from airts.contracts.events import EventType
from airts.navigation.pathfinding import PathfindingError, RouteDeferred
from airts.systems import automation_assignments, formations, movement
from airts.world.entities import OrderKind, UnitOrder, UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


QUEUED_SEARCH_CELL_BUDGET = 8_192


def continuing_ids(simulation: Simulation, entity_ids: tuple[str, ...]) -> frozenset[str]:
    """Finite direct work may finish before an appended order starts."""
    return frozenset(
        key
        for key in entity_ids
        if key not in simulation.assignments
        and (
            simulation.entities[key].path
            or simulation.entities[key].order_queue
            or simulation.entities[key].pursue_target
            or simulation.entities[key].repair_target_id is not None
            or simulation.entities[key].reclaim_target_id is not None
        )
    )


def append_target_order(
    simulation: Simulation, entity_ids: tuple[str, ...], order: UnitOrder
) -> None:
    continuing = continuing_ids(simulation, entity_ids)
    replacing = tuple(key for key in entity_ids if key not in continuing)
    automation_assignments.manual_override_many(simulation, replacing)
    for key in replacing:
        entity = simulation.entities[key]
        entity.path.clear()
        entity.move_target = None
        entity.state = UnitState.IDLE
        movement.reset_movement_liveness(entity)
    for key in entity_ids:
        simulation.entities[key].order_queue.append(order)


def drive_order_queues(simulation: Simulation) -> None:
    waiting = formations.waiting_members(simulation)
    pending = sorted(
        key
        for key, entity in simulation.entities.items()
        if entity.order_queue
        and key not in waiting
        and not entity.path
        and not entity.pursue_target
        and entity.repair_target_id is None
        and entity.reclaim_target_id is None
        and key not in simulation.assignments
    )
    if not pending:
        return
    offset = simulation.tick % len(pending)
    allowance = simulation._routes.automation_allowance(
        simulation.GATHERING_PATH_BUDGET, search_limit=QUEUED_SEARCH_CELL_BUDGET
    )
    ordered = pending[offset:] + pending[:offset]
    for key in ordered[: simulation.GATHERING_PATH_BUDGET]:
        entity = simulation.entities[key]
        order = entity.order_queue[0]
        if order.kind is OrderKind.MOVE:
            if not allowance.available:
                continue
            try:
                route = movement.plan_group_paths(
                    simulation,
                    (key,),
                    {key: order.position},
                    shared_goal=order.group_goal,
                    compact_approach=order.group_goal is not None and order.formation_goal is None,
                    allowance=allowance,
                )[key]
            except RouteDeferred:
                continue
            except PathfindingError as error:
                if entity.state is not UnitState.WAITING:
                    simulation.events.record(
                        simulation.tick,
                        EventType.PATHFINDING_FAILED,
                        key,
                        operation="queued_move",
                        reason=str(error),
                        target=[order.position.x, order.position.y],
                    )
                entity.state = UnitState.WAITING
                continue
            # Planning does not claim routes. Charge only completed plans so a
            # deferred search cannot consume another controller's route capacity.
            allowance.claim()
            entity.order_queue.pop(0)
            movement.start_path(
                simulation, entity, order.position, route, "human_queue", UnitState.MOVING
            )
            entity.formation_goal = order.formation_goal
            entity.formation_leg = order.formation_leg
            continue
        assert order.target_entity_id is not None
        command: AttackCommand | RepairBuildingCommand = (
            AttackCommand((key,), order.target_entity_id, entity.owner_id)
            if order.kind is OrderKind.ATTACK
            else RepairBuildingCommand((key,), order.target_entity_id, entity.owner_id)
        )
        # Give the canonical command boundary only the active order. Its ordinary
        # replacement cleanup must not cancel the pending FIFO tail.
        tail = entity.order_queue[1:]
        entity.order_queue = []
        result = simulation._execute_command(command, record=False)
        entity.order_queue = tail
        if not result.accepted:
            simulation.events.record(
                simulation.tick,
                EventType.ORDER_SKIPPED,
                key,
                kind=order.kind.value,
                target_entity_id=order.target_entity_id,
                reason=result.reason,
            )
