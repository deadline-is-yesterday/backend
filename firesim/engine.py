"""
FireSim Engine — библиотека симуляции распространения огня и тушения пожаров.
==============================================================================

Содержит всю логику симуляции в одном файле. Не зависит от Flask, SocketIO
или любого другого фреймворка — может использоваться автономно.

Основные сущности
-----------------
- **Сетка (grid)** — двумерный массив height x width.
  Каждая ячейка хранит числовое значение:
    * ``> 0``  — температура (чем выше, тем сильнее горит)
    * ``== 0`` — пустая клетка (нет огня)
    * ``< 0``  — стена (абсолютное значение = прочность; разрушается от огня)

- **Источник огня (source)** — клетка, которая непрерывно генерирует тепло.
  Каждый тик интенсивность источника растёт на 1, пока его не потушат.
  Когда интенсивность падает до 0, источник удаляется.

- **Пожарная машина (FireTruck)** — объект с координатами, запасом воды
  и рукавом с соплом (nozzle). У каждой машины:
    * ``id`` — уникальный строковый идентификатор
    * ``x, y`` — позиция на карте
    * ``water`` — остаток воды в литрах
    * ``nozzle_x, nozzle_y`` — координаты конца рукава (сопла)
    * ``hose_open`` — открыт ли рукав (True = вода льётся)

- **Зона воды (active_water)** — множество клеток, которые в данный момент
  поливаются водой. Вода блокирует распространение огня через эти клетки.

Физика распространения огня
---------------------------
Каждый тик для каждой клетки:

1. **Источник**: если источник жив (intensity > 0), его intensity += 1,
   и клетка получает это значение.

2. **Обычная клетка (>= 0)**: считается среднее тепло от 8 соседей
   (игнорируя клетки под водой). Новая температура = max(текущая, среднее).
   Если температура > 0, она дополнительно растёт на 1 за тик.

3. **Стена (< 0)**: если рядом есть огонь, прочность стены уменьшается
   (значение приближается к 0). Когда достигнет 0 — стена разрушена,
   клетка становится горючей.

Физика тушения
--------------
Для каждой машины с ``hose_open=True`` и ``water > 0``:

1. Из позиции сопла (nozzle) определяется направление на ближайший
   горящий участок.

2. В конусе с углом ``spread_degrees`` (по умолч. 45°) и радиусом
   ``radius`` (по умолч. 8) все достижимые клетки:
   - Помечаются как «под водой» (active_water)
   - Их температура снижается на ``amount`` (по умолч. 100)
   - Если клетка — источник, его интенсивность снижается на amount * 0.5

3. Израсходованная вода вычитается из ``truck.water``.
   Когда вода = 0, тушение прекращается.

4. Вода не проходит сквозь стены (is_path_blocked проверяет
   луч от сопла до клетки).

Сериализация
------------
Метод ``to_dict()`` возвращает полное состояние симуляции в виде словаря,
готового для отправки на фронтенд через JSON:
    {
        "ticks":        int,
        "width":        int,
        "height":       int,
        "grid":         [[float, ...], ...],
        "sources":      [{"x": int, "y": int, "intensity": float}, ...],
        "active_water": [{"x": int, "y": int}, ...],
        "trucks":       [{"id": str, "x": int, "y": int, "water": float,
                          "hose_open": bool,
                          "hose_end": {"x": int, "y": int} | null}, ...]
    }

Пример использования (без сервера)
-----------------------------------
    sim = FireSystem(20, 12)
    sim.set_wall(5, 0, -100)
    sim.set_source(3, 3, intensity=1000)
    sim.set_firetruck("truck_1", x=10, y=10, water=2400)
    sim.set_hose_nozzle("truck_1", nozzle_x=4, nozzle_y=4, is_open=True)

    for _ in range(50):
        sim.update()

    state = sim.to_dict()
    print(state["trucks"][0]["water"])   # остаток воды
    print(state["sources"])              # оставшиеся источники
"""

from __future__ import annotations

import copy
import math
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
#  FireTruck — пожарная машина
# ═══════════════════════════════════════════════════════════════════════════════


class FireTruck:
    """Пожарная машина с запасом воды и рукавом.

    Attributes:
        id:        Уникальный идентификатор машины (str).
        x, y:      Позиция машины на сетке.
        water:     Остаток воды в литрах (по умолч. 2400).
        nozzle_x:  X-координата конца рукава (сопла). None если рукав не развёрнут.
        nozzle_y:  Y-координата конца рукава (сопла). None если рукав не развёрнут.
        hose_open: True — вода подаётся, False — рукав перекрыт.
    """

    __slots__ = ("id", "x", "y", "water", "nozzle_x", "nozzle_y", "hose_open")

    def __init__(self, truck_id: str, x: int, y: int, water: float = 2400) -> None:
        self.id: str = truck_id
        self.x: int = x
        self.y: int = y
        self.water: float = water
        self.nozzle_x: int | None = None
        self.nozzle_y: int | None = None
        self.hose_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Сериализация машины в словарь для JSON."""
        d: dict[str, Any] = {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "water": round(self.water, 2),
            "hose_open": self.hose_open,
        }
        if self.nozzle_x is not None and self.nozzle_y is not None:
            d["hose_end"] = {"x": self.nozzle_x, "y": self.nozzle_y}
        else:
            d["hose_end"] = None
        return d


# ═══════════════════════════════════════════════════════════════════════════════
#  FireSystem — ядро симуляции
# ═══════════════════════════════════════════════════════════════════════════════


class FireSystem:
    """Главный класс симуляции: сетка, огонь, стены, машины, вода.

    Args:
        width:   Ширина сетки (кол-во столбцов).
        height:  Высота сетки (кол-во строк).
        speed_n: Делитель тиков. Реальный пересчёт физики происходит
                 каждый ``speed_n``-й вызов update(). По умолч. 1 (каждый тик).
    """

    def __init__(self, width: int, height: int, speed_n: int = 1) -> None:
        self.width: int = width
        self.height: int = height
        self.speed_n: int = speed_n
        self.ticks: int = 0

        # Сетка: 0 = пусто, >0 = огонь, <0 = стена
        self.grid: list[list[float]] = [
            [0.0 for _ in range(width)] for _ in range(height)
        ]

        # Источники огня: (x, y) -> текущая интенсивность
        self.sources: dict[tuple[int, int], float] = {}

        # Клетки, которые сейчас поливаются водой
        self.active_water: dict[tuple[int, int], bool] = {}

        # Пожарные машины: truck_id -> FireTruck
        self.firetrucks: dict[str, FireTruck] = {}

    # ── Настройка карты ──────────────────────────────────────────────────────

    def set_wall(self, x: int, y: int, wall_type: int = -30) -> None:
        """Поставить стену в клетку (x, y).

        Args:
            x, y:      Координаты клетки.
            wall_type: Отрицательное число = прочность стены.
                       Например, -100 = очень прочная, -10 = слабая.
        """
        self.grid[y][x] = wall_type

    def set_source(self, x: int, y: int, intensity: int = 1000) -> None:
        """Установить источник огня.

        Args:
            x, y:      Координаты клетки.
            intensity: Начальная интенсивность (по умолч. 1000).
        """
        self.sources[(x, y)] = intensity
        self.grid[y][x] = intensity

    # ── Пожарные машины ──────────────────────────────────────────────────────

    def set_firetruck(
        self, truck_id: str, x: int, y: int, water: float = 2400
    ) -> None:
        """Добавить или обновить позицию пожарной машины.

        Если машина с таким truck_id уже существует — обновляет координаты
        и запас воды. Иначе — создаёт новую.

        Args:
            truck_id: Уникальный id машины.
            x, y:     Позиция на сетке.
            water:    Запас воды в литрах (по умолч. 2400).
        """
        if truck_id in self.firetrucks:
            truck = self.firetrucks[truck_id]
            truck.x = x
            truck.y = y
            truck.water = water
        else:
            self.firetrucks[truck_id] = FireTruck(truck_id, x, y, water)

    def set_hose_nozzle(
        self, truck_id: str, nozzle_x: int, nozzle_y: int, is_open: bool
    ) -> None:
        """Установить позицию конца рукава (сопла) и его состояние.

        Args:
            truck_id:           ID машины.
            nozzle_x, nozzle_y: Координаты конца рукава.
            is_open:            True — вода подаётся, False — рукав перекрыт.
        """
        truck = self.firetrucks.get(truck_id)
        if truck is None:
            return
        truck.nozzle_x = nozzle_x
        truck.nozzle_y = nozzle_y
        truck.hose_open = is_open

    # ── Проверка проходимости луча ───────────────────────────────────────────

    def is_path_blocked(
        self, start_x: int, start_y: int, end_x: int, end_y: int
    ) -> bool:
        """Проверяет, пересекает ли прямая линия от (start) до (end) стену.

        Используется для определения, дойдёт ли вода от сопла до клетки.
        Проходит по клеткам на луче; если хоть одна — стена (< 0),
        возвращает True (путь заблокирован).

        Returns:
            True если путь заблокирован стеной, False если свободен.
        """
        steps = max(abs(end_x - start_x), abs(end_y - start_y))
        if steps == 0:
            return False
        for i in range(1, steps):
            tx = int(start_x + (end_x - start_x) * i / steps)
            ty = int(start_y + (end_y - start_y) * i / steps)
            if 0 <= tx < self.width and 0 <= ty < self.height:
                if self.grid[ty][tx] < 0:
                    return True
        return False

    # ── Тушение водой ────────────────────────────────────────────────────────

    def _apply_water_from_truck(
        self,
        truck: FireTruck,
        radius: int = 8,
        amount: float = 100,
        spread_degrees: int = 45,
    ) -> None:
        """Применить воду от одной машины.

        Алгоритм:
        1. Определяет направление: от сопла к ближайшему горящему участку.
        2. В конусе (spread_degrees, по умолч. 45°) с радиусом (radius, по умолч. 8)
           находит все достижимые клетки (не за стеной).
        3. Для каждой найденной горящей клетки:
           - Снижает температуру на amount
           - Если это источник — снижает его intensity на amount * 0.5
           - Отмечает клетку как «под водой» (active_water)
        4. Вычитает израсходованную воду из truck.water.

        Args:
            truck:          Пожарная машина.
            radius:         Радиус действия воды (в клетках).
            amount:         Сила тушения за тик.
            spread_degrees: Угол конуса распыления (макс. 45°).
        """
        # Без сопла или без воды — ничего не делаем
        if truck.nozzle_x is None or truck.nozzle_y is None:
            return
        if truck.water <= 0:
            return

        nozzle_x = truck.nozzle_x
        nozzle_y = truck.nozzle_y

        # Шаг 1: ищем ближайший огонь для определения направления струи
        nearest_fire: tuple[int, int] | None = None
        nearest_dist = float("inf")
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] > 0:
                    d = math.hypot(x - nozzle_x, y - nozzle_y)
                    if d < nearest_dist:
                        nearest_dist = d
                        nearest_fire = (x, y)

        if nearest_fire is None:
            return  # нечего тушить

        # Шаг 2: угол от сопла к цели
        target_x, target_y = nearest_fire
        spread_degrees = min(spread_degrees, 45)
        main_angle = math.atan2(target_y - nozzle_y, target_x - nozzle_x)
        half_spread_rad = math.radians(spread_degrees / 2)

        # Шаг 3: обход клеток в зоне действия
        water_used = 0.0

        for y in range(self.height):
            for x in range(self.width):
                # Вода закончилась — прекращаем
                if truck.water - water_used <= 0:
                    break

                dx = x - nozzle_x
                dy = y - nozzle_y
                dist = math.hypot(dx, dy)

                # Проверка: клетка в радиусе?
                if dist > radius:
                    continue

                # Проверка: клетка в конусе?
                cell_angle = math.atan2(dy, dx)
                diff = (cell_angle - main_angle + math.pi) % (2 * math.pi) - math.pi
                if abs(diff) > half_spread_rad:
                    continue

                # Проверка: клетка не стена и не за стеной?
                if self.grid[y][x] < 0:
                    continue
                if self.is_path_blocked(nozzle_x, nozzle_y, x, y):
                    continue

                # Клетка достижима — помечаем как «под водой»
                self.active_water[(x, y)] = True

                # Тушим огонь
                if self.grid[y][x] > 0:
                    reduction = min(amount, self.grid[y][x])
                    self.grid[y][x] = round(max(0.0, self.grid[y][x] - amount), 2)
                    water_used += reduction

                # Тушим источник (медленнее)
                if (x, y) in self.sources:
                    self.sources[(x, y)] = max(
                        0.0, self.sources[(x, y)] - amount * 0.5
                    )

        # Шаг 4: списываем воду
        truck.water = max(0.0, truck.water - water_used)

    # ── Основной тик ─────────────────────────────────────────────────────────

    def update(self) -> bool:
        """Выполнить один тик симуляции.

        Порядок действий:
        1. Тушение: для каждой машины с hose_open=True применяется вода.
        2. Распространение огня: для каждой клетки пересчитывается температура.
        3. Разрушение стен: если рядом со стеной есть огонь, прочность стены
           уменьшается (значение приближается к 0).
        4. Очистка: потухшие источники удаляются.

        Returns:
            True если физика была пересчитана, False если тик пропущен
            (из-за speed_n).
        """
        self.ticks += 1

        # Пропуск тика (speed_n > 1 замедляет симуляцию)
        if self.ticks % self.speed_n != 0:
            return False

        # ── Фаза 1: тушение ─────────────────────────────────────────────
        self.active_water.clear()
        for truck in self.firetrucks.values():
            if truck.hose_open:
                self._apply_water_from_truck(truck)

        # ── Фаза 2: распространение огня ────────────────────────────────
        new_grid = copy.deepcopy(self.grid)

        for y in range(self.height):
            for x in range(self.width):
                current = self.grid[y][x]

                # --- Источник огня ---
                if (x, y) in self.sources:
                    if self.sources[(x, y)] > 0:
                        # Источник разгорается
                        self.sources[(x, y)] += 1
                        new_grid[y][x] = self.sources[(x, y)]
                    else:
                        # Источник потушен, температура остаётся как есть
                        new_grid[y][x] = current

                # --- Обычная клетка (пол, воздух) ---
                elif current >= 0:
                    # Считаем среднее тепло от 8 соседей
                    surrounding_sum = 0.0
                    for dx in [-1, 0, 1]:
                        for dy in [-1, 0, 1]:
                            if dx == 0 and dy == 0:
                                continue
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < self.width and 0 <= ny < self.height:
                                # Соседи под водой не распространяют огонь
                                if (nx, ny) not in self.active_water:
                                    val = self.grid[ny][nx]
                                    if val > 0:
                                        surrounding_sum += val

                    calculated_mean = round(surrounding_sum / 8, 2)
                    new_temp = max(current, calculated_mean)

                    # Огонь разгорается сам по себе (+1 за тик)
                    if new_temp > 0:
                        new_temp += 1

                    new_grid[y][x] = round(new_temp, 2)

                # --- Стена ---
                elif current < 0:
                    # Проверяем, есть ли огонь рядом
                    is_near_fire = False
                    for dx in [-1, 0, 1]:
                        for dy in [-1, 0, 1]:
                            if dx == 0 and dy == 0:
                                continue
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < self.width and 0 <= ny < self.height:
                                if self.grid[ny][nx] > 0:
                                    is_near_fire = True
                                    break
                        if is_near_fire:
                            break

                    if is_near_fire:
                        # Стена разрушается (значение растёт к 0)
                        new_grid[y][x] = current + 1
                    else:
                        new_grid[y][x] = current

        # ── Фаза 3: применяем новую сетку ───────────────────────────────
        self.grid = new_grid

        # Удаляем потухшие источники (intensity <= 0)
        self.sources = {pos: val for pos, val in self.sources.items() if val > 0}

        return True

    # ── Сериализация ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Полное состояние симуляции в виде словаря (для JSON).

        Returns:
            dict с ключами: ticks, width, height, grid, sources,
            active_water, trucks.
        """
        sources_list = [
            {"x": x, "y": y, "intensity": val}
            for (x, y), val in self.sources.items()
        ]
        water_list = [{"x": x, "y": y} for (x, y) in self.active_water]
        trucks_list = [truck.to_dict() for truck in self.firetrucks.values()]

        return {
            "ticks": self.ticks,
            "width": self.width,
            "height": self.height,
            "grid": self.grid,
            "sources": sources_list,
            "active_water": water_list,
            "trucks": trucks_list,
        }
