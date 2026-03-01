import sqlite3

from flask import Flask
from .routes import bp, _SYSTEM_DB


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
    finally:
        con.close()


def init_app(app: Flask) -> None:
    _init_system_db()
    app.register_blueprint(bp)
