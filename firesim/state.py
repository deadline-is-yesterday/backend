"""Shared simulation state accessible from both routes and SocketIO events."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import FireSystem

# map_id -> FireSystem instance
simulations: dict[str, FireSystem] = {}

# map_id -> ticks per second (default 5)
tick_rates: dict[str, float] = {}
