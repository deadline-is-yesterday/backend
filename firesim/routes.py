import logging

from flask import Blueprint, jsonify, request

from firemap.models import get_active_game_id, get_game_db

from .engine import FireSystem
from . import state
from .events import start_tick_loop, stop_tick_loop

logger = logging.getLogger(__name__)
bp = Blueprint("firesim", __name__, url_prefix="/firesim")


# ── Roster endpoints (машины назначенные на пожар) ────────────────────────────

@bp.post("/roster")
def add_to_roster():
    """Add a vehicle to the fire roster."""
    data = request.get_json()
    vehicle_id = data.get("id")

    if vehicle_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        vehicle = con.execute("SELECT id FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
        if vehicle is None:
            return jsonify({"error": "vehicle not found"}), 404

        existing = con.execute("SELECT id FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if existing is not None:
            return jsonify({"error": "vehicle already in roster"}), 409

        con.execute("INSERT INTO fire_roster (vehicle_id) VALUES (?)", (vehicle_id,))
        con.commit()
        logger.info("ROSTER ADD: vehicle_id=%s", vehicle_id)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.delete("/roster")
def remove_from_roster():
    """Remove a vehicle from the fire roster (also removes from placed_cars)."""
    data = request.get_json()
    vehicle_id = data.get("id")

    if vehicle_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        existing = con.execute("SELECT id FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if existing is None:
            return jsonify({"error": "vehicle not in roster"}), 404

        con.execute("DELETE FROM placed_cars WHERE vehicle_id = ?", (vehicle_id,))
        con.execute("DELETE FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,))
        con.commit()
        logger.info("ROSTER REMOVE: vehicle_id=%s", vehicle_id)
        return jsonify({"ok": True})
    finally:
        con.close()


# ── Car endpoints ─────────────────────────────────────────────────────────────

@bp.post("/car")
def create_car():
    """Add a car to the fire scene."""
    data = request.get_json()
    print("\n"*5, data, "\n"*5)
    vehicle_id = data.get("id")
    x = data.get("x")
    y = data.get("y")

    if vehicle_id is None or x is None or y is None:
        return jsonify({"error": "id, x, y are required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        # Check vehicle exists
        vehicle = con.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
        if vehicle is None:
            return jsonify({"error": "vehicle not found"}), 404

        # Check vehicle is in roster
        in_roster = con.execute("SELECT id FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if in_roster is None:
            return jsonify({"error": "vehicle not in roster"}), 400

        # Check not already placed
        existing = con.execute("SELECT id FROM placed_cars WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if existing is not None:
            return jsonify({"error": "vehicle already placed"}), 409

        water = vehicle["water_capacity_l"]
        con.execute(
            "INSERT INTO placed_cars (vehicle_id, x, y, water_current_l) VALUES (?, ?, ?, ?)",
            (vehicle_id, x, y, water),
        )
        con.commit()
        placed_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        logger.info("CAR CREATE: vehicle_id=%s placed_id=%s x=%.1f y=%.1f", vehicle_id, placed_id, x, y)
        return jsonify({"ok": True, "id": placed_id})
    finally:
        con.close()


@bp.put("/car")
def update_car():
    """Move a car on the fire scene."""
    data = request.get_json()
    car_id = data.get("id")
    x = data.get("x")
    y = data.get("y")

    if car_id is None or x is None or y is None:
        return jsonify({"error": "id, x, y are required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT id FROM placed_cars WHERE id = ?", (car_id,)).fetchone()
        if row is None:
            return jsonify({"error": "placed car not found"}), 404

        con.execute("UPDATE placed_cars SET x = ?, y = ? WHERE id = ?", (x, y, car_id))
        con.commit()
        logger.info("CAR MOVE: placed_id=%s x=%.1f y=%.1f", car_id, x, y)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.delete("/car")
def delete_car():
    """Remove a car from the fire scene."""
    data = request.get_json()
    car_id = data.get("id")

    if car_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT id FROM placed_cars WHERE id = ?", (car_id,)).fetchone()
        if row is None:
            return jsonify({"error": "placed car not found"}), 404

        con.execute("DELETE FROM placed_cars WHERE id = ?", (car_id,))
        con.commit()
        logger.info("CAR DELETE: placed_id=%s", car_id)
        return jsonify({"ok": True})
    finally:
        con.close()


# ── Hose endpoints ────────────────────────────────────────────────────────────

@bp.post("/hose")
def create_hose():
    """Create a new hose on the fire scene."""
    data = request.get_json()
    hose_id = data.get("id")
    x = data.get("x")
    y = data.get("y")
    angle = data.get("angle", 0)
    active = data.get("active", False)

    if hose_id is None or x is None or y is None:
        return jsonify({"error": "id, x, y are required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        existing = con.execute("SELECT id FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if existing is not None:
            return jsonify({"error": "hose with this id already exists"}), 409

        con.execute(
            "INSERT INTO placed_hoses (id, x, y, angle, active) VALUES (?, ?, ?, ?, ?)",
            (hose_id, x, y, angle, int(active)),
        )
        con.commit()
        logger.info("HOSE CREATE: id=%s x=%.1f y=%.1f angle=%.1f active=%s", hose_id, x, y, angle, active)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.put("/hose")
def update_hose():
    """Update a hose position/state."""
    data = request.get_json()
    hose_id = data.get("id")
    x = data.get("x")
    y = data.get("y")
    angle = data.get("angle")
    active = data.get("active")

    if hose_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT * FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose not found"}), 404

        new_x = x if x is not None else row["x"]
        new_y = y if y is not None else row["y"]
        new_angle = angle if angle is not None else row["angle"]
        new_active = int(active) if active is not None else row["active"]

        con.execute(
            "UPDATE placed_hoses SET x = ?, y = ?, angle = ?, active = ? WHERE id = ?",
            (new_x, new_y, new_angle, new_active, hose_id),
        )
        con.commit()
        logger.info("HOSE UPDATE: id=%s x=%.1f y=%.1f angle=%.1f active=%s", hose_id, new_x, new_y, new_angle, bool(new_active))
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.delete("/hose")
def delete_hose():
    """Remove a hose from the fire scene."""
    data = request.get_json()
    hose_id = data.get("id")

    if hose_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT id FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose not found"}), 404

        con.execute("DELETE FROM placed_hoses WHERE id = ?", (hose_id,))
        con.commit()
        logger.info("HOSE DELETE: id=%s", hose_id)
        return jsonify({"ok": True})
    finally:
        con.close()


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
