from flask import Blueprint, jsonify, request

from .engine import FireSystem
from . import state
from .events import start_tick_loop, stop_tick_loop

bp = Blueprint("firesim", __name__, url_prefix="/firesim")


# ── Lifecycle ────────────────────────────────────────────────────────────────


@bp.post("/start")
def start_simulation():
    """Create a new simulation for a given map.

    Body JSON:
        map_id:   str                               - simulation key
        width:    int                               - grid width
        height:   int                               - grid height
        speed_n:  int (opt)                         - tick divisor (default 1)
        walls:    list of {x, y, hp}                - wall cells
        sources:  list of {x, y, intensity}         - fire sources
        trucks:   list of {id, x, y, water}         - firetrucks
    """
    data = request.get_json(silent=True) or {}
    map_id = data.get("map_id", "default")
    width = data.get("width", 20)
    height = data.get("height", 12)
    speed_n = data.get("speed_n", 1)

    sim = FireSystem(width, height, speed_n)

    for wall in data.get("walls", []):
        sim.set_wall(wall["x"], wall["y"], wall.get("hp", -30))

    for src in data.get("sources", []):
        sim.set_source(src["x"], src["y"], src.get("intensity", 1000))

    for truck in data.get("trucks", []):
        sim.set_firetruck(
            truck_id=truck["id"],
            x=truck["x"],
            y=truck["y"],
            water=truck.get("water", 2400),
        )

    state.simulations[map_id] = sim
    return jsonify({"ok": True, "map_id": map_id})


@bp.post("/reset")
def reset_simulation():
    """Remove an existing simulation and stop its tick loop."""
    data = request.get_json(silent=True) or {}
    map_id = data.get("map_id", "default")
    stop_tick_loop(map_id)
    state.simulations.pop(map_id, None)
    state.tick_rates.pop(map_id, None)
    return jsonify({"ok": True})


# ── State ────────────────────────────────────────────────────────────────────


@bp.get("/state")
def get_state():
    """Return the full simulation state for a map."""
    map_id = request.args.get("map_id", "default")
    sim = state.simulations.get(map_id)
    if sim is None:
        return jsonify({"error": "simulation not found"}), 404
    return jsonify(sim.to_dict())


# ── Fire sources (can still be added via REST) ───────────────────────────────


@bp.post("/set_source")
def set_source():
    """Add a fire source to a running simulation.

    Body JSON:
        map_id:    str
        x:         int
        y:         int
        intensity: int (opt, default 1000)
    """
    data = request.get_json(silent=True) or {}
    map_id = data.get("map_id", "default")
    sim = state.simulations.get(map_id)
    if sim is None:
        return jsonify({"error": "simulation not found"}), 404

    sim.set_source(data["x"], data["y"], data.get("intensity", 1000))
    return jsonify({"ok": True})
