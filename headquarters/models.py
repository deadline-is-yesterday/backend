import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HQ_DIR = os.path.join(_BACKEND_DIR, "headquarters")
_TEMPLATE_DB = os.path.join(_HQ_DIR, "games", "0_.db")
_GAMES_DIR = os.path.join(_HQ_DIR, "games")
_SYSTEM_DB = os.path.join(_BACKEND_DIR, "system.db")

# Piece length for all hose diameters (standard 20 m sections)
_HOSE_PIECE_LENGTH_M = 20

# Icon mapping by model name prefix
_ICON_BY_PREFIX: list[tuple[str, str]] = [
    ("АЦ",  "Лист 02/02.Пожарная автоцистерна.png"),
    ("ПНС", "Лист 02/01.Пожарная автонасосная станция.png"),
    ("АНР", "Лист 02/03.Пожарный автомобиль насосно-рукавный.png"),
    ("АР",  "Лист 01/09.Пожарный рукавный автомобиль.png"),
    ("АЛ",  "Лист 02/04.Пожарная автолестница.png"),
]
_ICON_DEFAULT = "Лист 01/01.Автомобиль пожарный.png"


def _icon_for(model_name: str) -> str:
    for prefix, icon in _ICON_BY_PREFIX:
        if model_name.startswith(prefix):
            return icon
    return _ICON_DEFAULT


@dataclass
class Hose:
    id: str
    max_length_m: int


@dataclass
class Branching:
    id: str
    type: str  # "two_way" | "three_way" | "four_way"


@dataclass
class Equipment:
    id: str
    name: str
    icon_path: str
    hoses: list[Hose]
    branchings: list[Branching]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "icon_path": self.icon_path,
            "hoses": [{"id": h.id, "max_length_m": h.max_length_m} for h in self.hoses],
            "branchings": [{"id": b.id, "type": b.type} for b in self.branchings],
        }


@dataclass
class Hydrant:
    id: str
    x: int
    y: int
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y, "label": self.label}


@dataclass
class HQMap:
    id: str
    name: str
    plan_url: str
    scale_m_per_px: float
    hydrants: list[Hydrant]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "plan_url": self.plan_url,
            "scale_m_per_px": self.scale_m_per_px,
            "hydrants": [h.to_dict() for h in self.hydrants],
        }


# ── Game DB management ────────────────────────────────────────────────────────

def _game_db_path(game_id: str) -> str:
    return os.path.join(_GAMES_DIR, f"{game_id}.db")


def ensure_game_db(game_id: str) -> str:
    """Copy template DB for the game if it doesn't exist yet. Return path."""
    path = _game_db_path(game_id)
    if not os.path.exists(path):
        os.makedirs(_GAMES_DIR, exist_ok=True)
        shutil.copy2(_TEMPLATE_DB, path)
        logger.info("Created HQ game DB: %s", path)
    # Миграция: таблица placed_hose_ends (могла отсутствовать в старых копиях)
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS placed_hose_ends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placed_hose_id INTEGER NOT NULL,
            x REAL NOT NULL, y REAL NOT NULL,
            angle REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 0,
            hydrant_id INTEGER,
            vehicle_id INTEGER,
            FOREIGN KEY(placed_hose_id) REFERENCES placed_hoses(id) ON DELETE CASCADE,
            FOREIGN KEY(hydrant_id) REFERENCES hydrants(id),
            FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
        )"""
    )
    con.commit()
    con.close()
    return path


def get_game_db(game_id: str) -> sqlite3.Connection:
    """Return a connection to the game's DB (creates copy from template if needed)."""
    path = ensure_game_db(game_id)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def get_active_game_id() -> str:
    """Read the active game id from system.db."""
    con = sqlite3.connect(_SYSTEM_DB)
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'active_game_id'").fetchone()
        return row[0] if row else "0"
    finally:
        con.close()


# ── DB loader ─────────────────────────────────────────────────────────────────

def load_equipment(game_id: str | None = None) -> list[Equipment]:
    gid = game_id or get_active_game_id()
    con = get_game_db(gid)
    try:
        vehicles = con.execute("SELECT * FROM vehicles ORDER BY id").fetchall()
        result: list[Equipment] = []
        for v in vehicles:
            hose_rows = con.execute(
                """
                SELECT ht.diameter_mm, lh.count
                FROM link_vehicle_hoses lh
                JOIN hose_types ht ON ht.id = lh.hose_type_id
                WHERE lh.vehicle_id = ?
                ORDER BY ht.diameter_mm
                """,
                (v["id"],),
            ).fetchall()

            hoses = [
                Hose(
                    id=f"hose_{row['diameter_mm']}mm",
                    max_length_m=row["count"] * _HOSE_PIECE_LENGTH_M,
                )
                for row in hose_rows
            ]

            result.append(
                Equipment(
                    id=f"{v['id']}",
                    name=v["model_name"],
                    icon_path=_icon_for(v["model_name"]),
                    hoses=hoses,
                    branchings=[],
                )
            )
        return result
    finally:
        con.close()

def load_map(game_id: str | None = None) -> HQMap | None:
    """Load the map for the given game from its DB."""
    gid = game_id or get_active_game_id()
    con = get_game_db(gid)
    try:
        row = con.execute("SELECT * FROM maps WHERE id = 1").fetchone()
        if row is None:
            return None

        hydrant_rows = con.execute("SELECT * FROM hydrants ORDER BY id").fetchall()
        hydrants = [
            Hydrant(id=str(h["id"]), x=h["x"], y=h["y"], label=h["label"])
            for h in hydrant_rows
        ]

        return HQMap(
            id="1",
            name=row["name"],
            plan_url="/headquarters/maps/plan.png",
            scale_m_per_px=row["scale_m_per_px"],
            hydrants=hydrants,
        )
    finally:
        con.close()


def load_layout(game_id: str | None = None) -> dict:
    """Load the layout JSON from the game DB.

    hose_ends always come from the placed_hose_ends table (source of truth),
    not from the JSON blob stored in layouts.
    """
    gid = game_id or get_active_game_id()
    con = get_game_db(gid)
    try:
        row = con.execute("SELECT data FROM layouts WHERE id = 1").fetchone()
        if row is None:
            layout: dict[str, Any] = {}
        else:
            import json
            layout = json.loads(row["data"])

        # Source of truth for hose_ends is the relational table.
        hose_end_rows = con.execute(
            "SELECT * FROM placed_hose_ends ORDER BY id"
        ).fetchall()
        layout["hose_ends"] = [
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
            for r in hose_end_rows
        ]
        return layout
    finally:
        con.close()


def save_layout(layout: dict, game_id: str | None = None) -> None:
    """Save the layout JSON to the game DB.

    hose_ends are stripped from the JSON because the relational table
    placed_hose_ends is the single source of truth for them.
    """
    gid = game_id or get_active_game_id()
    con = get_game_db(gid)
    try:
        import json
        clean = {k: v for k, v in layout.items() if k != "hose_ends"}
        data = json.dumps(clean, ensure_ascii=False)
        con.execute(
            "INSERT OR REPLACE INTO layouts (id, data) VALUES (1, ?)",
            (data,),
        )
        con.commit()
    finally:
        con.close()
