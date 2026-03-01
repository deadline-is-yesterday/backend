"""Синхронизация стволов (placed_hose_ends) из БД → nozzles в FireSystem.

Вызывается при CRUD операциях над hose_end, а также при старте симуляции.
"""

from __future__ import annotations

import logging
import math

from firemap.models import get_game_db
from . import state

logger = logging.getLogger(__name__)

# Размер плана в координатном пространстве SVG (хардкод FireMapView).
_PLAN_W = 800
_PLAN_H = 600


def sync_hose_ends(game_id: str) -> None:
    """Полная синхронизация placed_hose_ends → nozzles в FireSystem.

    Для каждой машины (vehicle_id), у которой есть hose_ends:
    - Создаёт / обновляет FireTruck с water = water_current_l из placed_cars.
    - Устанавливает max_water из vehicles.water_capacity_l.
    - Если хотя бы один hose_end подключён к гидранту → hydrant_connected.
    - Все активные hose_ends → Nozzle (координаты пересчитаны в клетки сетки).
    - Удаляет vehicle_* FireTrucks, у которых больше нет hose_ends.
    """
    # Ищем симуляцию: сначала по game_id напрямую, затем через маппинг
    sim = state.simulations.get(game_id)
    if sim is None:
        mapped_id = state.game_to_map.get(game_id)
        if mapped_id:
            sim = state.simulations.get(mapped_id)
            logger.debug("sync_hose_ends: found sim via mapping %s -> %s", game_id, mapped_id)
    if sim is None:
        logger.warning("sync_hose_ends: NO simulation found for game_id=%s", game_id)
        return

    con = get_game_db(game_id)
    try:
        # ── Размеры сетки для пересчёта координат ────────────────────
        grid_row = con.execute(
            "SELECT resolution, grid_rows FROM grid_state WHERE id = 1"
        ).fetchone()
        if not grid_row:
            logger.warning("sync_hose_ends: no grid_state for game %s", game_id)
            return
        resolution: int = grid_row["resolution"]
        grid_rows: int = grid_row["grid_rows"]

        # ── Все hose_ends ────────────────────────────────────────────
        hose_ends = con.execute(
            """SELECT id, x, y, angle, active, hydrant_id, vehicle_id
               FROM placed_hose_ends"""
        ).fetchall()

        # ── Информация о машинах (вода) ─────────────────────────────
        vehicles_info: dict[int, dict] = {}
        for he in hose_ends:
            vid = he["vehicle_id"]
            if vid is None or vid in vehicles_info:
                continue
            car = con.execute(
                """SELECT pc.water_current_l, v.water_capacity_l
                   FROM placed_cars pc
                   JOIN vehicles v ON pc.vehicle_id = v.id
                   WHERE pc.vehicle_id = ?""",
                (vid,),
            ).fetchone()
            if car:
                vehicles_info[vid] = {
                    "water": car["water_current_l"] or 0,
                    "max_water": car["water_capacity_l"] or 0,
                }

        # ── Группировка по vehicle_id ────────────────────────────────
        by_vehicle: dict[int, list] = {}
        for he in hose_ends:
            vid = he["vehicle_id"]
            if vid is None:
                continue
            by_vehicle.setdefault(vid, []).append(he)

        # ── Обновляем FireTrucks / Nozzles ───────────────────────────
        synced_ids: set[str] = set()

        for vid, ends in by_vehicle.items():
            truck_id = f"vehicle_{vid}"
            synced_ids.add(truck_id)

            vinfo = vehicles_info.get(vid)
            if not vinfo:
                continue

            water = vinfo["water"]
            max_water = vinfo["max_water"]

            # Создать / обновить FireTruck
            if truck_id not in sim.firetrucks:
                sim.set_firetruck(truck_id, x=0, y=0, water=water)
            truck = sim.firetrucks[truck_id]
            truck.max_water = max_water
            # Не сбрасываем water если truck уже существует (вода расходуется)

            # Гидрант — если хотя бы один end подключён
            has_hydrant = any(he["hydrant_id"] is not None for he in ends)
            sim.set_hydrant_connected(truck_id, has_hydrant)

            # Формируем массив nozzles
            nozzles_data = []
            for he in ends:
                # Стволы, подключённые к гидранту — это вход воды, не выход
                if he["hydrant_id"] is not None:
                    continue
                # Неактивные стволы — не добавляем
                if not he["active"]:
                    continue
                # Пересчёт пиксели → клетки сетки
                gx = he["x"] * resolution / _PLAN_W
                gy = he["y"] * grid_rows / _PLAN_H
                # angle в БД — градусы (0° = север), в движке — радианы (0 = восток)
                angle_rad = math.radians(he["angle"] - 90)
                nozzles_data.append({
                    "id": str(he["id"]),
                    "x": gx,
                    "y": gy,
                    "angle": angle_rad,
                    "spread_deg": 45.0,
                    "is_open": True,
                })

            sim.sync_nozzles(truck_id, nozzles_data)

        # ── Удаляем FireTrucks без hose_ends ─────────────────────────
        stale = [
            tid for tid in sim.firetrucks
            if tid.startswith("vehicle_") and tid not in synced_ids
        ]
        for tid in stale:
            del sim.firetrucks[tid]

        logger.debug(
            "sync_hose_ends: game=%s, trucks=%d, total_nozzles=%d",
            game_id,
            len(synced_ids),
            sum(len(t.nozzles) for t in sim.firetrucks.values()),
        )
    finally:
        con.close()
