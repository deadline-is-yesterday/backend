import os
import sqlite3
from dataclasses import dataclass
from typing import Any

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fire_simulation_full.db")

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
class FireMap:
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


# ── DB loader ─────────────────────────────────────────────────────────────────

def _load_equipment() -> list[Equipment]:
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
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
                    id=f"vehicle_{v['id']}",
                    name=v["model_name"],
                    icon_path=_icon_for(v["model_name"]),
                    hoses=hoses,
                    branchings=[],  # branchings table to be added later
                )
            )
        return result
    finally:
        con.close()


EQUIPMENT_LIST: list[Equipment] = _load_equipment()

MAPS: dict[str, FireMap] = {
    "default": FireMap(
        id="default",
        name="Объект: Склад №4",
        plan_url="/firemap/maps/default/plan.png",
        scale_m_per_px=0.5,
        hydrants=[
            Hydrant("hydrant_001", 320, 215, "ПГ-1"),
            Hydrant("hydrant_002", 640, 430, "ПГ-2"),
            Hydrant("hydrant_003", 180, 500, "ПГ-3"),
        ],
    )
}

# in-memory layout storage: map_id -> layout dict | None
LAYOUTS: dict[str, Any] = {}
