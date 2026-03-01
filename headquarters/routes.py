import os

from flask import Blueprint, jsonify, request, send_from_directory

from .models import (
    get_active_game_id,
    get_game_db,
    load_equipment,
    load_layout,
    load_map,
    save_layout,
)

bp = Blueprint("headquarters", __name__, url_prefix="/headquarters")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_ICONS_DIR = os.path.join(_BACKEND_DIR, "..", "frontend", "ICONS")
_PLANS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plans")


# ── Equipment ─────────────────────────────────────────────────────────────────

@bp.get("/equipment")
def get_equipment():
    """Return all vehicles in the fire roster (with placement info if placed)."""
    con = get_game_db(get_active_game_id())
    try:
        roster = con.execute(
            """
            SELECT fr.vehicle_id,
                   pc.id AS placed_id, pc.x, pc.y, pc.water_current_l
            FROM fire_roster fr
            LEFT JOIN placed_cars pc ON pc.vehicle_id = fr.vehicle_id
            ORDER BY fr.vehicle_id
            """
        ).fetchall()

        all_eq = {e.id: e for e in load_equipment()}

        result = []
        for row in roster:
            eq_key = f"{row['vehicle_id']}"
            eq = all_eq.get(eq_key)
            if eq is None:
                continue
            d = eq.to_dict()
            d["placed_id"] = row["placed_id"]
            d["x"] = row["x"]
            d["y"] = row["y"]
            d["water_current_l"] = row["water_current_l"]
            result.append(d)

        return jsonify(result)
    finally:
        con.close()


@bp.get("/equipment/all")
def get_all_equipment():
    """Return all available vehicles from the game DB."""
    return jsonify([e.to_dict() for e in load_equipment()])


# ── Maps ──────────────────────────────────────────────────────────────────────

@bp.get("/maps")
def get_map():
    hq_map = load_map()
    if hq_map is None:
        return jsonify({"error": "map not found"}), 404
    return jsonify(hq_map.to_dict())


@bp.get("/maps/plan.png")
def get_plan():
    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT plan_filename FROM maps WHERE id = 1").fetchone()
    finally:
        con.close()
    if row is None:
        return jsonify({"error": "map not found"}), 404
    return send_from_directory(_PLANS_DIR, row["plan_filename"])


# ── Icons ─────────────────────────────────────────────────────────────────────

@bp.get("/icons/<path:icon_path>")
def get_icon(icon_path: str):
    return send_from_directory(_FRONTEND_ICONS_DIR, icon_path)


# ── Layout ────────────────────────────────────────────────────────────────────

@bp.get("/maps/layout")
def get_layout():
    return jsonify(load_layout())


@bp.post("/maps/layout")
def post_layout():
    save_layout(request.get_json())
    return jsonify({"ok": True})
