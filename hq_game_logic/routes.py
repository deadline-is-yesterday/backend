import logging

from flask import Blueprint, jsonify, request
from headquarters.models import get_active_game_id, get_game_db
from game.logger import log_event

logger = logging.getLogger(__name__)
bp = Blueprint("hq_game_logic", __name__, url_prefix="/hq_game_logic")


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
        log_event(get_active_game_id(), "hq_roster_add", {"vehicle_id": vehicle_id})
        logger.info("HQ ROSTER ADD: vehicle_id=%s", vehicle_id)
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
        log_event(get_active_game_id(), "hq_roster_remove", {"vehicle_id": vehicle_id})
        logger.info("HQ ROSTER REMOVE: vehicle_id=%s", vehicle_id)
        return jsonify({"ok": True})
    finally:
        con.close()


# ── Car endpoints ─────────────────────────────────────────────────────────────

@bp.post("/car")
def create_car():
    """Add a car to the fire scene."""
    data = request.get_json()
    vehicle_id = data.get("id")
    x = data.get("x")
    y = data.get("y")

    if vehicle_id is None or x is None or y is None:
        return jsonify({"error": "id, x, y are required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        vehicle = con.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
        if vehicle is None:
            return jsonify({"error": "vehicle not found"}), 404

        in_roster = con.execute("SELECT id FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if in_roster is None:
            return jsonify({"error": "vehicle not in roster"}), 400

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
        log_event(get_active_game_id(), "hq_car_place", {
            "vehicle_id": vehicle_id, "placed_id": placed_id, "x": x, "y": y,
        })
        logger.info("HQ CAR CREATE: vehicle_id=%s placed_id=%s x=%.1f y=%.1f", vehicle_id, placed_id, x, y)
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
        # car_id = vehicle_id (строка типа "5")
        row = con.execute("SELECT id FROM placed_cars WHERE vehicle_id = ?", (car_id,)).fetchone()
        if row is None:
            return jsonify({"error": "placed car not found"}), 404

        placed_id = row["id"]
        con.execute("UPDATE placed_cars SET x = ?, y = ? WHERE id = ?", (x, y, placed_id))
        con.commit()
        log_event(get_active_game_id(), "hq_car_move", {"vehicle_id": car_id, "x": x, "y": y})
        logger.info("HQ CAR MOVE: vehicle_id=%s x=%.1f y=%.1f", car_id, x, y)
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
        # car_id = vehicle_id
        row = con.execute("SELECT id FROM placed_cars WHERE vehicle_id = ?", (car_id,)).fetchone()
        if row is None:
            return jsonify({"error": "placed car not found"}), 404

        con.execute("DELETE FROM placed_cars WHERE id = ?", (row["id"],))
        con.commit()
        log_event(get_active_game_id(), "hq_car_remove", {"vehicle_id": car_id})
        logger.info("HQ CAR DELETE: vehicle_id=%s", car_id)
        return jsonify({"ok": True})
    finally:
        con.close()


# ── Hose endpoints ────────────────────────────────────────────────────────────

@bp.post("/hose")
def create_hose():
    """Create a new hose on the fire scene."""
    data = request.get_json() or {}

    if "id" not in data or "x" not in data or "y" not in data:
        return jsonify({"error": "id, x, y are required"}), 400

    try:
        hose_id = str(data["id"])  # UUID string from frontend
        x = float(data["x"])
        y = float(data["y"])
    except (ValueError, TypeError):
        return jsonify({"error": "x and y must be numbers"}), 400

    import json as _json

    con = get_game_db(get_active_game_id())
    try:
        existing = con.execute("SELECT id FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if existing is not None:
            return jsonify({"error": "hose with this id already exists"}), 409

        eq_instance_id = data.get("equipment_instance_id")
        inner_hose_id = data.get("hose_id")
        waypoints = _json.dumps(data.get("waypoints") or [])
        endpoint = _json.dumps(data.get("endpoint")) if data.get("endpoint") else None

        con.execute(
            """INSERT INTO placed_hoses
               (id, x, y, equipment_instance_id, hose_id, waypoints, endpoint)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (hose_id, x, y, eq_instance_id, inner_hose_id, waypoints, endpoint),
        )
        con.commit()
        log_event(get_active_game_id(), "hq_hose_place", {
            "hose_id": hose_id, "x": x, "y": y,
        })
        logger.info("HQ HOSE CREATE: id=%s x=%.1f y=%.1f", hose_id, x, y)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.put("/hose")
def update_hose():
    """Update a hose (waypoints, position)."""
    import json as _json
    data = request.get_json()
    hose_id = data.get("id")
    x = data.get("x")
    y = data.get("y")

    if hose_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT * FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose not found"}), 404

        new_x = x if x is not None else row["x"]
        new_y = y if y is not None else row["y"]
        waypoints = _json.dumps(data["waypoints"]) if "waypoints" in data else row["waypoints"]
        endpoint = _json.dumps(data["endpoint"]) if "endpoint" in data else row["endpoint"]

        con.execute(
            "UPDATE placed_hoses SET x = ?, y = ?, waypoints = ?, endpoint = ? WHERE id = ?",
            (new_x, new_y, waypoints, endpoint, hose_id),
        )
        con.commit()
        log_event(get_active_game_id(), "hq_hose_move", {
            "hose_id": hose_id, "x": new_x, "y": new_y,
        })
        logger.info("HQ HOSE UPDATE: id=%s x=%.1f y=%.1f", hose_id, new_x, new_y)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.delete("/hose")
def delete_hose():
    """Remove a hose and all its ends from the fire scene."""
    data = request.get_json()
    hose_id = data.get("id")

    if hose_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT id FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose not found"}), 404

        con.execute("DELETE FROM placed_hose_ends WHERE placed_hose_id = ?", (hose_id,))
        con.execute("DELETE FROM placed_hoses WHERE id = ?", (hose_id,))
        con.commit()
        log_event(get_active_game_id(), "hq_hose_remove", {"hose_id": hose_id})
        logger.info("HQ HOSE DELETE: id=%s", hose_id)
        return jsonify({"ok": True})
    finally:
        con.close()


# ── Hose-end endpoints (конец рукава) ────────────────────────────────────────

@bp.post("/hose_end")
def create_hose_end():
    """Create a hose end. If hydrant_id is set — fills vehicle, otherwise drains it."""
    data = request.get_json()
    frontend_id = data.get("id")  # UUID от фронтенда
    placed_hose_id = data.get("placed_hose_id")
    x = data.get("x")
    y = data.get("y")
    angle = data.get("angle", 0)
    active = data.get("active", False)
    hydrant_id = data.get("hydrant_id")  # если подключен к гидранту
    vehicle_id = data.get("vehicle_id")  # какую машину опустошает/наполняет

    if placed_hose_id is None or x is None or y is None:
        return jsonify({"error": "placed_hose_id, x, y are required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        hose = con.execute("SELECT id FROM placed_hoses WHERE id = ?", (placed_hose_id,)).fetchone()
        if hose is None:
            return jsonify({"error": "hose not found"}), 404

        if hydrant_id is not None:
            hydrant = con.execute("SELECT id FROM hydrants WHERE id = ?", (hydrant_id,)).fetchone()
            if hydrant is None:
                return jsonify({"error": "hydrant not found"}), 404

        if vehicle_id is not None:
            vehicle = con.execute("SELECT id FROM placed_cars WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
            if vehicle is None:
                return jsonify({"error": "vehicle not placed"}), 404

        if frontend_id:
            con.execute(
                """INSERT INTO placed_hose_ends
                   (id, placed_hose_id, x, y, angle, active, hydrant_id, vehicle_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (frontend_id, placed_hose_id, x, y, angle, int(active), hydrant_id, vehicle_id),
            )
            end_id = frontend_id
        else:
            con.execute(
                """INSERT INTO placed_hose_ends
                   (placed_hose_id, x, y, angle, active, hydrant_id, vehicle_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (placed_hose_id, x, y, angle, int(active), hydrant_id, vehicle_id),
            )
            end_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()
        log_event(get_active_game_id(), "hq_hose_end_place", {
            "end_id": end_id, "hose_id": placed_hose_id,
            "hydrant_id": hydrant_id, "vehicle_id": vehicle_id,
        })
        logger.info("HQ HOSE_END CREATE: id=%s hose=%s hydrant=%s vehicle=%s",
                     end_id, placed_hose_id, hydrant_id, vehicle_id)
        return jsonify({"ok": True, "id": end_id})
    finally:
        con.close()


@bp.put("/hose_end")
def update_hose_end():
    """Update a hose end position/state."""
    data = request.get_json()
    end_id = data.get("id")

    if end_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT * FROM placed_hose_ends WHERE id = ?", (end_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose end not found"}), 404

        new_x = data.get("x", row["x"])
        new_y = data.get("y", row["y"])
        new_angle = data.get("angle", row["angle"])
        new_active = int(data["active"]) if "active" in data else row["active"]
        new_hydrant_id = data.get("hydrant_id", row["hydrant_id"])
        new_vehicle_id = data.get("vehicle_id", row["vehicle_id"])

        con.execute(
            """UPDATE placed_hose_ends
               SET x = ?, y = ?, angle = ?, active = ?, hydrant_id = ?, vehicle_id = ?
               WHERE id = ?""",
            (new_x, new_y, new_angle, new_active, new_hydrant_id, new_vehicle_id, end_id),
        )
        con.commit()
        log_event(get_active_game_id(), "hq_hose_end_update", {
            "end_id": end_id, "active": bool(new_active),
            "hydrant_id": new_hydrant_id, "vehicle_id": new_vehicle_id,
        })
        logger.info("HQ HOSE_END UPDATE: id=%s active=%s hydrant=%s",
                     end_id, bool(new_active), new_hydrant_id)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.delete("/hose_end")
def delete_hose_end():
    """Remove a hose end."""
    data = request.get_json()
    end_id = data.get("id")

    if end_id is None:
        return jsonify({"error": "id is required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT id FROM placed_hose_ends WHERE id = ?", (end_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose end not found"}), 404

        con.execute("DELETE FROM placed_hose_ends WHERE id = ?", (end_id,))
        con.commit()
        log_event(get_active_game_id(), "hq_hose_end_remove", {"end_id": end_id})
        logger.info("HQ HOSE_END DELETE: id=%s", end_id)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.get("/hose_ends/<int:hose_id>")
def get_hose_ends(hose_id: int):
    """Get all ends for a given hose."""
    con = get_game_db(get_active_game_id())
    try:
        rows = con.execute(
            "SELECT * FROM placed_hose_ends WHERE placed_hose_id = ? ORDER BY id",
            (hose_id,),
        ).fetchall()
        return jsonify([
            {
                "id": r["id"],
                "placed_hose_id": r["placed_hose_id"],
                "x": r["x"],
                "y": r["y"],
                "angle": r["angle"],
                "active": bool(r["active"]),
                "hydrant_id": r["hydrant_id"],
                "vehicle_id": r["vehicle_id"],
            }
            for r in rows
        ])
    finally:
        con.close()
