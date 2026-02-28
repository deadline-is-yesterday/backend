"""SocketIO namespace for real-time fire simulation."""

from __future__ import annotations

import logging
import time

from flask import request
from flask_socketio import Namespace, emit, join_room, leave_room

from extensions import socketio
from . import state

logger = logging.getLogger(__name__)

_DEFAULT_TICK_RATE = 5  # ticks per second
_tick_loop_running: dict[str, bool] = {}  # map_id -> is running


def _tick_loop(map_id: str) -> None:
    """Background loop that auto-ticks the simulation and emits state."""
    logger.info("tick_loop started for map_id=%s", map_id)
    _tick_loop_running[map_id] = True

    while _tick_loop_running.get(map_id, False):
        sim = state.simulations.get(map_id)
        if sim is None:
            logger.info("tick_loop: sim %s removed, stopping", map_id)
            break

        rate = state.tick_rates.get(map_id, _DEFAULT_TICK_RATE)
        interval = 1.0 / max(rate, 0.1)

        sim.update()
        socketio.emit(
            "state_update",
            sim.to_dict(),
            namespace="/firesim",
            room=map_id,
        )
        socketio.sleep(interval)

    _tick_loop_running.pop(map_id, None)
    logger.info("tick_loop stopped for map_id=%s", map_id)


def start_tick_loop(map_id: str) -> None:
    """Start auto-tick loop for a simulation (idempotent)."""
    if _tick_loop_running.get(map_id, False):
        return
    socketio.start_background_task(_tick_loop, map_id)


def stop_tick_loop(map_id: str) -> None:
    """Stop auto-tick loop for a simulation."""
    _tick_loop_running[map_id] = False


class FireSimNamespace(Namespace):
    """Namespace /firesim for real-time fire simulation control."""

    def on_connect(self) -> None:
        sid = request.sid
        logger.info("firesim connect: sid=%s", sid)

    def on_disconnect(self) -> None:
        sid = request.sid
        logger.info("firesim disconnect: sid=%s", sid)

    # ── Room management ──────────────────────────────────────────────────

    def on_join_sim(self, data: dict) -> None:
        map_id = data.get("map_id", "default")
        join_room(map_id)
        logger.info("sid=%s joined room %s", request.sid, map_id)

        # Start tick loop if sim exists and loop not running
        if map_id in state.simulations:
            start_tick_loop(map_id)

    def on_leave_sim(self, data: dict) -> None:
        map_id = data.get("map_id", "default")
        leave_room(map_id)
        logger.info("sid=%s left room %s", request.sid, map_id)

    # ── Tick rate ────────────────────────────────────────────────────────

    def on_set_tickrate(self, data: dict) -> None:
        map_id = data.get("map_id", "default")
        rate = data.get("ticks_per_second", _DEFAULT_TICK_RATE)
        state.tick_rates[map_id] = max(0.1, float(rate))
        logger.info("tickrate for %s set to %s", map_id, state.tick_rates[map_id])

    # ── Firetruck position ───────────────────────────────────────────────

    def on_firetruck_move(self, data: dict) -> None:
        map_id = data.get("map_id", "default")
        truck_id = data.get("truck_id")
        x = data.get("x")
        y = data.get("y")

        sim = state.simulations.get(map_id)
        if sim is None or truck_id is None:
            return

        truck = sim.firetrucks.get(truck_id)
        if truck is None:
            return

        truck.x = x
        truck.y = y
        logger.debug("truck %s moved to (%s, %s) in %s", truck_id, x, y, map_id)

    # ── Hose nozzle position + open/closed ───────────────────────────────

    def on_hose_update(self, data: dict) -> None:
        map_id = data.get("map_id", "default")
        truck_id = data.get("truck_id")
        nozzle_x = data.get("nozzle_x")
        nozzle_y = data.get("nozzle_y")
        is_open = data.get("is_open", False)

        sim = state.simulations.get(map_id)
        if sim is None or truck_id is None:
            return

        sim.set_hose_nozzle(truck_id, nozzle_x, nozzle_y, is_open)
        logger.debug(
            "hose %s: nozzle=(%s,%s) open=%s in %s",
            truck_id, nozzle_x, nozzle_y, is_open, map_id,
        )
