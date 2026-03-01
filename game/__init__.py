import os
import sqlite3

from flask import Flask
from .routes import bp, _SYSTEM_DB

_TEMPLATE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "firemap", "fire_simulation_full.db",
)


def _init_system_db() -> None:
    """Создать необходимые таблицы в system.db если их нет."""
    con = sqlite3.connect(_SYSTEM_DB)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'draft',
                created_at TIMESTAMP NOT NULL DEFAULT (datetime('now'))
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS vehicle_types (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                key              TEXT NOT NULL UNIQUE,
                water_capacity_l INTEGER NOT NULL DEFAULT 0,
                foam_capacity_l  INTEGER NOT NULL DEFAULT 0,
                pump_flow_ls     REAL NOT NULL DEFAULT 0,
                crew_size        INTEGER NOT NULL DEFAULT 0,
                ladder_height_m  REAL NOT NULL DEFAULT 0
            )
        """)
        con.commit()

        # Автозаполнение vehicle_types из шаблонной БД (если таблица пуста)
        count = con.execute("SELECT COUNT(*) FROM vehicle_types").fetchone()[0]
        if count == 0 and os.path.exists(_TEMPLATE_DB):
            tpl = sqlite3.connect(_TEMPLATE_DB)
            tpl.row_factory = sqlite3.Row
            rows = tpl.execute(
                """SELECT
                       TRIM(SUBSTR(model_name, 1, INSTR(model_name, '#') - 1)) AS type_key,
                       water_capacity_l, foam_capacity_l, pump_flow_ls,
                       crew_size, ladder_height_m
                   FROM vehicles
                   GROUP BY type_key"""
            ).fetchall()
            tpl.close()
            for r in rows:
                con.execute(
                    """INSERT OR IGNORE INTO vehicle_types
                       (key, water_capacity_l, foam_capacity_l,
                        pump_flow_ls, crew_size, ladder_height_m)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (r["type_key"], r["water_capacity_l"], r["foam_capacity_l"],
                     r["pump_flow_ls"], r["crew_size"], r["ladder_height_m"] or 0),
                )
            con.commit()
    finally:
        con.close()


def init_app(app: Flask) -> None:
    _init_system_db()
    app.register_blueprint(bp)
