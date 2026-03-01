import logging

from flask import Blueprint, jsonify, request
from firemap.models import get_active_game_id, get_game_db
from headquarters.models import get_game_db as get_hq_db
from game.logger import log_event

logger = logging.getLogger(__name__)
bp = Blueprint("game_logic", __name__, url_prefix="/game_logic")


# ── Roster endpoints (машины назначенные на пожар) ────────────────────────────

@bp.post("/roster")
def add_to_roster():
    """Add a vehicle to the fire roster."""
    data = request.get_json()
    vehicle_id = data.get("id")

    if vehicle_id is None:
        return jsonify({"error": "id is required"}), 400

    game_id = get_active_game_id()
    con = get_game_db(game_id)
    try:
        vehicle = con.execute("SELECT id FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
        if vehicle is None:
            return jsonify({"error": "vehicle not found"}), 404

        existing = con.execute("SELECT id FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if existing is not None:
            return jsonify({"error": "vehicle already in roster"}), 409

        con.execute("INSERT INTO fire_roster (vehicle_id) VALUES (?)", (vehicle_id,))
        con.commit()

        hq_con = get_hq_db(game_id)
        hq_con.execute("INSERT OR IGNORE INTO fire_roster (vehicle_id) VALUES (?)", (vehicle_id,))
        hq_con.commit()
        hq_con.close()

        log_event(game_id, "roster_add", {"vehicle_id": vehicle_id})
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

    game_id = get_active_game_id()
    con = get_game_db(game_id)
    try:
        existing = con.execute("SELECT id FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
        if existing is None:
            return jsonify({"error": "vehicle not in roster"}), 404

        con.execute("DELETE FROM placed_cars WHERE vehicle_id = ?", (vehicle_id,))
        con.execute("DELETE FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,))
        con.commit()

        hq_con = get_hq_db(game_id)
        hq_con.execute("DELETE FROM placed_cars WHERE vehicle_id = ?", (vehicle_id,))
        hq_con.execute("DELETE FROM fire_roster WHERE vehicle_id = ?", (vehicle_id,))
        hq_con.commit()
        hq_con.close()

        log_event(game_id, "roster_remove", {"vehicle_id": vehicle_id})
        logger.info("ROSTER REMOVE: vehicle_id=%s", vehicle_id)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.post("/dispatch")
def dispatch_vehicles():
    """Batch-добавление машин в fire_roster по типам.

    Body: {"vehicles": {"АЦ-40 (130)": 3, ...}, "address": "ул. ..."}
    Подбирает N свободных машин каждого типа и добавляет в fire_roster.
    """
    data = request.get_json()
    vehicles_map = data.get("vehicles", {})
    address = data.get("address", "")

    if not vehicles_map:
        return jsonify({"error": "vehicles is required"}), 400

    game_id = get_active_game_id()
    con = get_game_db(game_id)
    hq_con = get_hq_db(game_id)
    added: list[dict] = []
    try:
        for type_key, count in vehicles_map.items():
            if count <= 0:
                continue
            # Подбираем свободные машины данного типа (не в roster)
            rows = con.execute(
                """SELECT v.id, v.model_name
                   FROM vehicles v
                   WHERE TRIM(SUBSTR(v.model_name, 1, INSTR(v.model_name, '#') - 1)) = ?
                     AND v.id NOT IN (SELECT vehicle_id FROM fire_roster)
                   LIMIT ?""",
                (type_key, count),
            ).fetchall()
            for row in rows:
                con.execute(
                    "INSERT INTO fire_roster (vehicle_id) VALUES (?)", (row[0],)
                )
                hq_con.execute(
                    "INSERT OR IGNORE INTO fire_roster (vehicle_id) VALUES (?)",
                    (row[0],),
                )
                added.append({"id": row[0], "model_name": row[1]})

        con.commit()
        hq_con.commit()
        log_event(game_id, "dispatch", {
            "address": address,
            "requested": vehicles_map,
            "added": [a["id"] for a in added],
        })
        logger.info("DISPATCH: address=%s, added=%d vehicles", address, len(added))
        return jsonify({"ok": True, "dispatched": added})
    finally:
        con.close()
        hq_con.close()


# ── Car endpoints ─────────────────────────────────────────────────────────────

@bp.post("/car")
def create_car():
    """Add a car to the fire scene."""
    data = request.get_json()
    print("\n" * 5, data, "\n" * 5)
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
        log_event(get_active_game_id(), "car_place", {
            "vehicle_id": vehicle_id, "placed_id": placed_id, "x": x, "y": y,
        })
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
        log_event(get_active_game_id(), "car_move", {"placed_id": car_id, "x": x, "y": y})
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
        log_event(get_active_game_id(), "car_remove", {"placed_id": car_id})
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
        log_event(get_active_game_id(), "hose_place", {
            "hose_id": hose_id, "x": x, "y": y, "angle": angle, "active": active,
        })
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
        log_event(get_active_game_id(), "hose_move", {
            "hose_id": hose_id, "x": new_x, "y": new_y,
            "angle": new_angle, "active": bool(new_active),
        })
        logger.info("HOSE UPDATE: id=%s x=%.1f y=%.1f angle=%.1f active=%s", hose_id, new_x, new_y, new_angle,
                    bool(new_active))
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
        log_event(get_active_game_id(), "hose_remove", {"hose_id": hose_id})
        logger.info("HOSE DELETE: id=%s", hose_id)
        return jsonify({"ok": True})
    finally:
        con.close()
