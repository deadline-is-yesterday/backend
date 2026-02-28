import os

from flask import Blueprint, jsonify, request, send_from_directory

from .models import EQUIPMENT_LIST, LAYOUTS, MAPS

bp = Blueprint("firemap", __name__, url_prefix="/firemap")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_ICONS_DIR = os.path.join(_BACKEND_DIR, "..", "frontend", "ICONS")
_PLANS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plans")


# ── Equipment ─────────────────────────────────────────────────────────────────

@bp.get("/equipment")
def get_equipment():
    return jsonify([e.to_dict() for e in EQUIPMENT_LIST])


# ── Maps ──────────────────────────────────────────────────────────────────────

@bp.get("/maps/<map_id>")
def get_map(map_id: str):
    fire_map = MAPS.get(map_id)
    if fire_map is None:
        return jsonify({"error": "map not found"}), 404
    return jsonify(fire_map.to_dict())


@bp.get("/maps/<map_id>/plan.png")
def get_plan(map_id: str):
    if map_id not in MAPS:
        return jsonify({"error": "map not found"}), 404
    return send_from_directory(_PLANS_DIR, f"{map_id}.png")


# ── Icons ─────────────────────────────────────────────────────────────────────

@bp.get("/icons/<path:icon_path>")
def get_icon(icon_path: str):
    return send_from_directory(_FRONTEND_ICONS_DIR, icon_path)


# ── Layout ────────────────────────────────────────────────────────────────────

@bp.get("/maps/<map_id>/layout")
def get_layout(map_id: str):
    if map_id not in MAPS:
        return jsonify({"error": "map not found"}), 404
    return jsonify(LAYOUTS.get(map_id))


@bp.post("/maps/<map_id>/layout")
def save_layout(map_id: str):
    if map_id not in MAPS:
        return jsonify({"error": "map not found"}), 404
    LAYOUTS[map_id] = request.get_json()
    return jsonify({"ok": True})
