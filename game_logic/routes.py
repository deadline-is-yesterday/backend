import logging

from flask import Blueprint, jsonify, request
from firemap.models import get_active_game_id, get_game_db
from headquarters.models import get_game_db as get_hq_db
from firesim.water_sync import sync_hose_ends
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
    equipment_instance_id = data.get("equipment_instance_id")
    hose_spec_id = data.get("hose_id")
    waypoints = data.get("waypoints")
    endpoint = data.get("endpoint")

    if hose_id is None or x is None or y is None:
        return jsonify({"error": "id, x, y are required"}), 400

    import json as _json
    wp_json = _json.dumps(waypoints, ensure_ascii=False) if waypoints else None
    ep_json = _json.dumps(endpoint, ensure_ascii=False) if endpoint else None

    con = get_game_db(get_active_game_id())
    try:
        existing = con.execute("SELECT id FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if existing is not None:
            return jsonify({"error": "hose with this id already exists"}), 409

        con.execute(
            """INSERT INTO placed_hoses
               (id, x, y, angle, active, equipment_instance_id, hose_id, waypoints, endpoint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hose_id, x, y, angle, int(active),
             equipment_instance_id, hose_spec_id, wp_json, ep_json),
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
    waypoints = data.get("waypoints")
    endpoint = data.get("endpoint")

    if hose_id is None:
        return jsonify({"error": "id is required"}), 400

    import json as _json

    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT * FROM placed_hoses WHERE id = ?", (hose_id,)).fetchone()
        if row is None:
            return jsonify({"error": "hose not found"}), 404

        new_x = x if x is not None else row["x"]
        new_y = y if y is not None else row["y"]
        new_angle = angle if angle is not None else row["angle"]
        new_active = int(active) if active is not None else row["active"]
        new_wp = _json.dumps(waypoints, ensure_ascii=False) if waypoints is not None else row["waypoints"]
        new_ep = _json.dumps(endpoint, ensure_ascii=False) if endpoint is not None else row["endpoint"]

        con.execute(
            """UPDATE placed_hoses
               SET x = ?, y = ?, angle = ?, active = ?, waypoints = ?, endpoint = ?
               WHERE id = ?""",
            (new_x, new_y, new_angle, new_active, new_wp, new_ep, hose_id),
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


# ── Hose end endpoints (стволы — влияют на симуляцию огня) ────────────────────

@bp.post("/hose_end")
def create_hose_end():
    """Create a hose end. Triggers fire sim sync."""
    data = request.get_json()
    frontend_id = data.get("id")  # UUID от фронтенда
    placed_hose_id = data.get("placed_hose_id")
    x = data.get("x")
    y = data.get("y")
    angle = data.get("angle", 0)
    active = data.get("active", False)
    hydrant_id = data.get("hydrant_id")
    vehicle_id = data.get("vehicle_id")

    if placed_hose_id is None or x is None or y is None:
        return jsonify({"error": "placed_hose_id, x, y are required"}), 400

    con = get_game_db(get_active_game_id())
    try:
        hose = con.execute(
            "SELECT id, equipment_instance_id FROM placed_hoses WHERE id = ?",
            (placed_hose_id,),
        ).fetchone()
        if hose is None:
            return jsonify({"error": "hose not found"}), 404

        # Если vehicle_id не передан — берём из рукава (equipment_instance_id)
        if vehicle_id is None and hose["equipment_instance_id"]:
            vehicle_id = hose["equipment_instance_id"]

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
        log_event(get_active_game_id(), "hose_end_place", {
            "end_id": end_id, "hose_id": placed_hose_id,
            "hydrant_id": hydrant_id, "vehicle_id": vehicle_id,
        })
        logger.info("HOSE_END CREATE: id=%s hose=%s hydrant=%s vehicle=%s",
                     end_id, placed_hose_id, hydrant_id, vehicle_id)
        sync_hose_ends(get_active_game_id())
        return jsonify({"ok": True, "id": end_id})
    finally:
        con.close()


@bp.put("/hose_end")
def update_hose_end():
    """Update a hose end position/state. Triggers fire sim sync."""
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
        log_event(get_active_game_id(), "hose_end_update", {
            "end_id": end_id, "active": bool(new_active),
            "hydrant_id": new_hydrant_id, "vehicle_id": new_vehicle_id,
        })
        logger.info("HOSE_END UPDATE: id=%s active=%s hydrant=%s",
                     end_id, bool(new_active), new_hydrant_id)
        sync_hose_ends(get_active_game_id())
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.delete("/hose_end")
def delete_hose_end():
    """Remove a hose end. Triggers fire sim sync."""
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
        log_event(get_active_game_id(), "hose_end_remove", {"end_id": end_id})
        logger.info("HOSE_END DELETE: id=%s", end_id)
        sync_hose_ends(get_active_game_id())
        return jsonify({"ok": True})
    finally:
        con.close()
