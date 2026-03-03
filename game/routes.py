import json
import logging
import os
import sqlite3
import uuid

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from firemap.models import (
    _GAMES_DIR,
    _SYSTEM_DB,
    ensure_game_db as ensure_firemap_game_db,
    get_active_game_id,
    get_game_db,
)
from headquarters.models import ensure_game_db as ensure_headquarters_game_db

from game.logger import log_event
from firesim.engine import FireSystem
from firesim import state as sim_state
from firesim.events import start_tick_loop, stop_tick_loop
from firesim.water_sync import sync_hose_ends

logger = logging.getLogger(__name__)
bp = Blueprint("game", __name__, url_prefix="/game")

_PLANS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firemap", "plans")


# ── helpers ──────────────────────────────────────────────────────────────────

def _system_db() -> sqlite3.Connection:
    con = sqlite3.connect(_SYSTEM_DB)
    con.row_factory = sqlite3.Row
    return con


def _set_system(key: str, value: str) -> None:
    con = _system_db()
    try:
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        con.commit()
    finally:
        con.close()


def _get_system(key: str, default: str = "") -> str:
    con = _system_db()
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default
    finally:
        con.close()


# ── Game lifecycle ───────────────────────────────────────────────────────────

@bp.post("")
def create_game():
    """Create a new game (copy template DB) and set it as active."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")

    game_id = str(uuid.uuid4())[:8]
    ensure_firemap_game_db(game_id)
    ensure_headquarters_game_db(game_id)
    _set_system("active_game_id", game_id)
    _set_system("is_running", "0")

    con = _system_db()
    try:
        con.execute(
            "INSERT INTO games (id, name, status) VALUES (?, ?, 'draft')",
            (game_id, name),
        )
        con.commit()
    finally:
        con.close()

    logger.info("GAME CREATE: %s (%s)", game_id, name)
    return jsonify({"game_id": game_id}), 201


@bp.get("/list")
def list_games():
    """Return all games ordered by creation date (newest first)."""
    con = _system_db()
    try:
        rows = con.execute(
            "SELECT id, name, created_at, status FROM games ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([
            {"id": r["id"], "name": r["name"], "created_at": r["created_at"], "status": r["status"]}
            for r in rows
        ])
    finally:
        con.close()


@bp.get("/status")
def game_status():
    """Return active game id and running flag."""
    return jsonify({
        "active_game_id": get_active_game_id(),
        "is_running": _get_system("is_running", "0") == "1",
    })


@bp.put("/status")
def update_game_status():
    """Update the is_running flag. Starts/stops fire simulation."""
    data = request.get_json(silent=True) or {}
    is_running = data.get("is_running", False)
    _set_system("is_running", "1" if is_running else "0")

    game_id = get_active_game_id()

    if is_running and game_id:
        # ── Создаём серверную симуляцию из grid ──────────────────────
        con = get_game_db(game_id)
        try:
            grid_row = con.execute(
                "SELECT resolution, grid_rows, grid_data FROM grid_state WHERE id = 1"
            ).fetchone()
            if grid_row:
                resolution = grid_row["resolution"]
                grid_rows = grid_row["grid_rows"]
                grid = json.loads(grid_row["grid_data"])

                sim = FireSystem(resolution, grid_rows)
                for y, row in enumerate(grid):
                    for x, cell in enumerate(row):
                        if cell == "wall":
                            sim.set_wall(x, y, -100)
                        elif cell == "fire":
                            sim.set_source(x, y, 1000)

                sim_state.simulations[game_id] = sim
                sim_state.game_to_map[game_id] = game_id
                sync_hose_ends(game_id)
                start_tick_loop(game_id)
                logger.info("Fire sim started for game %s (%dx%d)", game_id, resolution, grid_rows)
        finally:
            con.close()

    # Reset all roles and stop sim when stopping the game
    if not is_running and game_id:
        stop_tick_loop(game_id)
        sim_state.simulations.pop(game_id, None)
        sim_state.tick_rates.pop(game_id, None)

        con = get_game_db(game_id)
        try:
            con.execute("UPDATE roles SET occupied = 0, sid = NULL")
            con.commit()
        finally:
            con.close()

    log_event(game_id, "simulation_start" if is_running else "simulation_stop")
    logger.info("GAME STATUS: is_running=%s", is_running)

    # Уведомляем всех клиентов о смене статуса
    from extensions import socketio
    socketio.emit("game_status", {
        "is_running": is_running,
        "active_game_id": game_id,
    })

    return jsonify({"ok": True})


# ── Plan image ───────────────────────────────────────────────────────────────

@bp.post("/plan")
def upload_plan():
    """Upload a floor plan image for the current active game."""
    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "empty filename"}), 400

    game_id = get_active_game_id()
    ext = os.path.splitext(file.filename)[1] or ".png"
    filename = secure_filename(f"plan_{game_id}{ext}")

    os.makedirs(_PLANS_DIR, exist_ok=True)
    filepath = os.path.join(_PLANS_DIR, filename)
    file.save(filepath)

    con = get_game_db(game_id)
    try:
        con.execute(
            "INSERT OR REPLACE INTO maps (id, name, plan_filename, scale_m_per_px) "
            "VALUES (1, 'plan', ?, 1.0)",
            (filename,),
        )
        con.commit()
    finally:
        con.close()

    logger.info("PLAN UPLOAD: game=%s file=%s", game_id, filename)
    return jsonify({"plan_url": "/firemap/maps/plan.png", "filename": filename})


# ── Grid state ───────────────────────────────────────────────────────────────

@bp.get("/map")
def get_map_grid():
    """Load the grid state for the current active game."""
    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT * FROM grid_state WHERE id = 1").fetchone()
        if row is None:
            return jsonify({})
        return jsonify({
            "resolution": row["resolution"],
            "grid_rows": row["grid_rows"],
            "aspect_ratio": row["aspect_ratio"],
            "grid": json.loads(row["grid_data"]),
            "scale_m_per_px": row["scale_m_per_px"],
        })
    finally:
        con.close()


@bp.put("/map")
def save_map_grid():
    """Save the grid state for the current active game."""
    data = request.get_json(silent=True) or {}

    resolution = data.get("resolution")
    grid_rows = data.get("grid_rows")
    aspect_ratio = data.get("aspect_ratio")
    grid = data.get("grid")

    if not all([resolution, grid_rows, aspect_ratio, grid]):
        return jsonify({"error": "resolution, grid_rows, aspect_ratio, grid are required"}), 400

    scale = data.get("scale_m_per_px")

    con = get_game_db(get_active_game_id())
    try:
        con.execute(
            """INSERT OR REPLACE INTO grid_state
               (id, resolution, grid_rows, aspect_ratio, grid_data, scale_m_per_px)
               VALUES (1, ?, ?, ?, ?, ?)""",
            (resolution, grid_rows, aspect_ratio, json.dumps(grid), scale),
        )
        con.commit()
    finally:
        con.close()

    log_event(get_active_game_id(), "grid_save", {
        "resolution": resolution, "grid_rows": grid_rows,
    })
    logger.info("GRID SAVE: %dx%d", resolution, grid_rows)
    return jsonify({"ok": True})


# ── Scenario ─────────────────────────────────────────────────────────────────

@bp.get("/scenario")
def get_scenario():
    """Load scenario (environment conditions) for the current active game."""
    con = get_game_db(get_active_game_id())
    try:
        row = con.execute("SELECT * FROM scenario WHERE id = 1").fetchone()
        if row is None:
            return jsonify({})
        return jsonify({
            "temperature": row["temperature"],
            "wind_speed": row["wind_speed"],
            "wind_direction": row["wind_direction"],
            "target_address": row["target_address"],
        })
    finally:
        con.close()


@bp.put("/scenario")
def save_scenario():
    """Save scenario (environment conditions) for the current active game."""
    data = request.get_json(silent=True) or {}

    con = get_game_db(get_active_game_id())
    try:
        con.execute(
            """INSERT OR REPLACE INTO scenario
               (id, temperature, wind_speed, wind_direction, target_address)
               VALUES (1, ?, ?, ?, ?)""",
            (
                data.get("temperature", 20.0),
                data.get("wind_speed", 0.0),
                data.get("wind_direction", 0),
                data.get("target_address", ""),
            ),
        )
        con.commit()
    finally:
        con.close()

    log_event(get_active_game_id(), "scenario_save", {
        "temperature": data.get("temperature"),
        "wind_speed": data.get("wind_speed"),
        "wind_direction": data.get("wind_direction"),
        "target_address": data.get("target_address"),
    })
    logger.info("SCENARIO SAVE")
    return jsonify({"ok": True})


# ── Depot ────────────────────────────────────────────────────────────────────

@bp.get("/depot")
def get_depot():
    """Load depot config: vehicle type counts derived from vehicles table."""
    game_con = get_game_db(get_active_game_id())
    try:
        rows = game_con.execute(
            """
            SELECT
                TRIM(SUBSTR(model_name, 1, INSTR(model_name, '#') - 1)) AS type_key,
                COUNT(*) AS cnt
            FROM vehicles
            GROUP BY type_key
            """
        ).fetchall()
        vehicles = {row["type_key"]: row["cnt"] for row in rows}
        return jsonify({"vehicles": vehicles})
    finally:
        game_con.close()


@bp.put("/depot")
def save_depot():
    """Reconcile vehicles table to match desired counts per type."""
    data = request.get_json(silent=True) or {}
    vehicles = data.get("vehicles", {})

    game_id = get_active_game_id()
    game_con = get_game_db(game_id)
    try:
        game_con.execute("PRAGMA foreign_keys = ON")

        for type_key, desired in vehicles.items():
            desired = max(0, int(desired))

            # Current vehicles of this type
            current_rows = game_con.execute(
                "SELECT id, model_name FROM vehicles WHERE model_name LIKE ? ORDER BY id",
                (f"{type_key} #%",),
            ).fetchall()
            current = len(current_rows)

            if desired == current:
                continue

            if desired > current:
                # Find max number suffix
                max_num = 0
                for r in current_rows:
                    try:
                        num = int(r["model_name"].rsplit("#", 1)[1])
                        max_num = max(max_num, num)
                    except (IndexError, ValueError):
                        pass

                # Get specs from first vehicle (or from system vehicle_types)
                ref = current_rows[0] if current_rows else None
                if ref:
                    ref_row = game_con.execute(
                        "SELECT * FROM vehicles WHERE id = ?", (ref["id"],)
                    ).fetchone()
                else:
                    # No vehicles of this type exist — get specs from system.db
                    sys_con = _system_db()
                    try:
                        t = sys_con.execute(
                            "SELECT * FROM vehicle_types WHERE key = ?", (type_key,)
                        ).fetchone()
                    finally:
                        sys_con.close()
                    ref_row = t  # will use system specs

                for i in range(current + 1, desired + 1):
                    num = max_num + (i - current)
                    new_name = f"{type_key} #{num}"

                    if ref and ref_row:
                        game_con.execute(
                            """INSERT INTO vehicles
                               (model_name, water_capacity_l, foam_capacity_l,
                                pump_flow_ls, crew_size, ladder_height_m)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                new_name,
                                ref_row["water_capacity_l"],
                                ref_row["foam_capacity_l"],
                                ref_row["pump_flow_ls"],
                                ref_row["crew_size"],
                                ref_row["ladder_height_m"],
                            ),
                        )
                    else:
                        # From system vehicle_types
                        game_con.execute(
                            """INSERT INTO vehicles
                               (model_name, water_capacity_l, foam_capacity_l,
                                pump_flow_ls, crew_size, ladder_height_m)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                new_name,
                                ref_row["water_capacity_l"] if ref_row else 0,
                                ref_row["foam_capacity_l"] if ref_row else 0,
                                ref_row["pump_flow_ls"] if ref_row else 0,
                                ref_row["crew_size"] if ref_row else 0,
                                ref_row["ladder_height_m"] if ref_row else 0,
                            ),
                        )

                    new_id = game_con.execute("SELECT last_insert_rowid()").fetchone()[0]

                    # Copy hose/nozzle links from first vehicle
                    if ref:
                        for link in game_con.execute(
                            "SELECT hose_type_id, count FROM link_vehicle_hoses WHERE vehicle_id = ?",
                            (ref["id"],),
                        ).fetchall():
                            game_con.execute(
                                "INSERT INTO link_vehicle_hoses (vehicle_id, hose_type_id, count) VALUES (?, ?, ?)",
                                (new_id, link["hose_type_id"], link["count"]),
                            )
                        for link in game_con.execute(
                            "SELECT nozzle_type_id, count FROM link_vehicle_nozzles WHERE vehicle_id = ?",
                            (ref["id"],),
                        ).fetchall():
                            game_con.execute(
                                "INSERT INTO link_vehicle_nozzles (vehicle_id, nozzle_type_id, count) VALUES (?, ?, ?)",
                                (new_id, link["nozzle_type_id"], link["count"]),
                            )

            elif desired < current:
                # Delete vehicles with highest IDs (CASCADE removes links)
                to_delete = current - desired
                ids = [r["id"] for r in current_rows[-to_delete:]]
                game_con.execute(
                    f"DELETE FROM vehicles WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )

        game_con.commit()
    finally:
        game_con.close()

    logger.info("DEPOT SAVE (reconcile): %d types", len(vehicles))
    return jsonify({"ok": True})


# ── Vehicle types ────────────────────────────────────────────────────────────

@bp.get("/vehicle_types")
def get_vehicle_types():
    """Return vehicle types from reference table with current counts from game."""
    # Types from reference table (system.db)
    sys_con = _system_db()
    try:
        types = sys_con.execute(
            "SELECT * FROM vehicle_types ORDER BY id"
        ).fetchall()
    finally:
        sys_con.close()

    # Current counts from game vehicles table
    game_con = get_game_db(get_active_game_id())
    try:
        counts_rows = game_con.execute(
            """
            SELECT
                TRIM(SUBSTR(model_name, 1, INSTR(model_name, '#') - 1)) AS type_key,
                COUNT(*) AS cnt
            FROM vehicles
            GROUP BY type_key
            """
        ).fetchall()
        counts = {row["type_key"]: row["cnt"] for row in counts_rows}

        roster_rows = game_con.execute(
            """
            SELECT
                TRIM(SUBSTR(v.model_name, 1, INSTR(v.model_name, '#') - 1)) AS type_key,
                COUNT(*) AS cnt
            FROM fire_roster fr
            JOIN vehicles v ON v.id = fr.vehicle_id
            GROUP BY type_key
            """
        ).fetchall()
        in_roster = {row["type_key"]: row["cnt"] for row in roster_rows}
    finally:
        game_con.close()

    return jsonify([
        {
            "key": t["key"],
            "name": t["key"],
            "water_capacity_l": t["water_capacity_l"],
            "foam_capacity_l": t["foam_capacity_l"],
            "pump_flow_ls": t["pump_flow_ls"],
            "crew_size": t["crew_size"],
            "ladder_height_m": t["ladder_height_m"],
            "count": counts.get(t["key"], 0),
            "in_roster": in_roster.get(t["key"], 0),
        }
        for t in types
    ])


# ── Auth ─────────────────────────────────────────────────────────────────────

@bp.post("/auth")
def login():
    """Verify teacher password."""
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    stored = _get_system("teacher_password", "admin")
    if password == stored:
        logger.info("AUTH: teacher login OK")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "wrong password"}), 401


# ── Roles ────────────────────────────────────────────────────────────────────

def _ensure_roles_table(con: sqlite3.Connection) -> None:
    """Create roles table if it doesn't exist (migration for old game DBs)."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            role TEXT PRIMARY KEY,
            occupied INTEGER NOT NULL DEFAULT 0,
            sid TEXT
        )
    """)
    for role in ('dispatcher', 'rtp', 'squad', 'chief'):
        con.execute("INSERT OR IGNORE INTO roles (role) VALUES (?)", (role,))
    con.commit()


@bp.get("/roles")
def get_roles():
    """Return available roles with occupancy status for the active game."""
    game_id = get_active_game_id()
    is_running = _get_system("is_running", "0") == "1"
    if not game_id or not is_running:
        return jsonify({"running": False, "roles": []})

    con = get_game_db(game_id)
    try:
        _ensure_roles_table(con)
        rows = con.execute("SELECT role, occupied FROM roles").fetchall()
        return jsonify({
            "running": True,
            "roles": [
                {"role": r["role"], "occupied": bool(r["occupied"])}
                for r in rows
            ],
        })
    finally:
        con.close()


@bp.post("/roles/join")
def join_role():
    """Join a role. Returns 409 if already taken (race-condition safe)."""
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    sid = data.get("sid")

    if not role or not sid:
        return jsonify({"error": "role and sid are required"}), 400

    game_id = get_active_game_id()
    if not game_id:
        return jsonify({"error": "no active game"}), 400

    con = get_game_db(game_id)
    try:
        _ensure_roles_table(con)
        cur = con.execute(
            "UPDATE roles SET occupied = 1, sid = ? WHERE role = ? AND occupied = 0",
            (sid, role),
        )
        con.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "role already taken"}), 409
        logger.info("ROLE JOIN: %s sid=%s", role, sid)
        return jsonify({"ok": True})
    finally:
        con.close()


@bp.post("/roles/leave")
def leave_role():
    """Explicitly leave a role."""
    data = request.get_json(silent=True) or {}
    role = data.get("role")

    game_id = get_active_game_id()
    if not game_id or not role:
        return jsonify({"ok": True})

    con = get_game_db(game_id)
    try:
        con.execute(
            "UPDATE roles SET occupied = 0, sid = NULL WHERE role = ?",
            (role,),
        )
        con.commit()
        logger.info("ROLE LEAVE: %s", role)
    finally:
        con.close()

    return jsonify({"ok": True})


# ── Logs ─────────────────────────────────────────────────────────────────────

@bp.get("/logs")
def get_logs():
    """Return game event logs for the active game (or a specific game via ?game_id=)."""
    game_id = request.args.get("game_id") or get_active_game_id()
    if not game_id or game_id == "0":
        return jsonify([])

    from game.logger import read_logs
    return jsonify(read_logs(game_id))
