"""
FireSim Engine — библиотека симуляции распространения огня.
=========================================================================

Работает без Flask/SocketIO. Вызывается через tick-loop каждые ~200 мс.
Зависимости: только stdlib (math).

Модель сетки
------------
- Размер: width × height клеток
- Значения ячеек (float):
    * ``0``   — пустая клетка
    * ``> 0`` — огонь (температура, растёт со временем)
    * ``< 0`` — барьер (абсолютное значение = оставшаяся прочность)

Порядок обновления (update)
---------------------------
1. Пополнение баков от гидрантов
2. Очистка active_water
3. Тушение: для каждого truck → для каждого nozzle → конус воды (локально)
4. Обновление сетки:
   a. Барьеры: разрушение от огня
   b. Источники: рост intensity (если не под водой)
   c. Горящие клетки: рост температуры (если под водой → 0)
   d. Пустые клетки: распространение огня (каждые FIRE_SPREAD_INTERVAL тиков)
5. Очистка потухших источников
"""

from __future__ import annotations

import math
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
#  КОНСТАНТЫ (настраиваемые)
# ═══════════════════════════════════════════════════════════════════════════════

FIRE_SPREAD_INTERVAL: int = 50       # тиков между волнами распространения
FIRE_GROWTH_PER_TICK: float = 1.0    # рост температуры горящей клетки за тик
FIRE_SOURCE_GROWTH: float = 1.0      # рост интенсивности источника за тик

WATER_RADIUS: int = 25               # радиус действия ствола (клетки)
WATER_AMOUNT: float = 100.0          # сила тушения за тик (снижение температуры клетки)
WATER_PER_NOZZLE_TICK: float = 50.0  # фиксированный расход воды на ствол за тик (литры)

HYDRANT_REFILL_RATE: float = 200.0   # литров/тик при подключённом гидранте

BARRIER_HP: dict[str, int] = {
    "wall":    1000,
    "door":    300,
    "window":  500,
    "hydrant": 9999,
}

# Ортогональные смещения (4 стороны)
_ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))

# Все 8 соседей
_NEIGHBORS_8 = (
    (-1, -1), (0, -1), (1, -1),
    (-1,  0),          (1,  0),
    (-1,  1), (0,  1), (1,  1),
)

_TWO_PI: float = 2.0 * math.pi


# ═══════════════════════════════════════════════════════════════════════════════
#  Nozzle — ствол (конец рукава)
# ═══════════════════════════════════════════════════════════════════════════════


class Nozzle:
    """Ствол — точка, из которой льётся вода конусом.

    Attributes:
        id:         Уникальный идентификатор (str, UUID).
        x, y:       Позиция на сетке (float, в клетках).
        angle:      Направление струи (радианы, 0 = вправо).
        spread_deg: Угол конуса (градусы, 10..140).
        is_open:    True — вода подаётся.
    """

    __slots__ = ("id", "x", "y", "angle", "spread_deg", "is_open")

    def __init__(
        self,
        nozzle_id: str,
        x: float,
        y: float,
        angle: float = 0.0,
        spread_deg: float = 45.0,
        is_open: bool = False,
    ) -> None:
        self.id: str = nozzle_id
        self.x: float = float(x)
        self.y: float = float(y)
        self.angle: float = float(angle)
        self.spread_deg: float = max(10.0, min(float(spread_deg), 140.0))
        self.is_open: bool = bool(is_open)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "angle": round(self.angle, 4),
            "spread_deg": self.spread_deg,
            "is_open": self.is_open,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  FireTruck — пожарная машина
# ═══════════════════════════════════════════════════════════════════════════════


class FireTruck:
    """Пожарная машина с баком воды и стволами.

    Attributes:
        id:                Уникальный идентификатор (str, UUID — equipment_instance_id).
        x, y:              Позиция машины.
        water:             Текущий запас воды (литры).
        max_water:         Ёмкость бака.
        hydrant_connected: Подключена ли к гидранту.
        nozzles:           Список стволов (Nozzle).
    """

    __slots__ = ("id", "x", "y", "water", "max_water", "hydrant_connected", "nozzles")

    def __init__(self, truck_id: str, x: float, y: float, water: float = 2400.0) -> None:
        self.id: str = truck_id
        self.x: float = float(x)
        self.y: float = float(y)
        self.water: float = float(water)
        self.max_water: float = float(water)
        self.hydrant_connected: bool = False
        self.nozzles: list[Nozzle] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "water": round(self.water, 2),
            "max_water": round(self.max_water, 2),
            "hydrant_connected": self.hydrant_connected,
            "nozzles": [n.to_dict() for n in self.nozzles],
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  FireSystem — ядро симуляции
# ═══════════════════════════════════════════════════════════════════════════════


class FireSystem:
    """Главный класс симуляции: сетка, огонь, барьеры, машины, вода.

    Args:
        width:   Ширина сетки (столбцы).
        height:  Высота сетки (строки).
        speed_n: Делитель тиков — реальный пересчёт каждые speed_n вызовов update().
    """

    def __init__(self, width: int, height: int, speed_n: int = 1) -> None:
        self.width: int = width
        self.height: int = height
        self.speed_n: int = max(1, speed_n)
        self.ticks: int = 0

        # Сетка: 0 = пусто, >0 = огонь, <0 = барьер
        self.grid: list[list[float]] = [[0.0] * width for _ in range(height)]

        # Источники огня: (x, y) → текущая интенсивность
        self.sources: dict[tuple[int, int], float] = {}

        # Клетки, которые сейчас поливаются водой
        self.active_water: set[tuple[int, int]] = set()

        # Пожарные машины: truck_id → FireTruck
        self.firetrucks: dict[str, FireTruck] = {}

    # ── Настройка карты ──────────────────────────────────────────────────────

    def set_barrier(
        self,
        x: int,
        y: int,
        kind: str = "wall",
        hp: int | None = None,
    ) -> None:
        """Поставить барьер (wall / door / window / hydrant)."""
        if hp is not None:
            resolved = abs(hp)
        else:
            resolved = BARRIER_HP.get(str(kind).lower(), BARRIER_HP["wall"])
        self.sources.pop((x, y), None)
        self.grid[y][x] = -float(resolved)

    # Обратная совместимость
    def set_wall(self, x: int, y: int, wall_type: int = -200) -> None:
        """legacy-обёртка для set_barrier."""
        self.set_barrier(x, y, hp=abs(wall_type))

    def set_door(self, x: int, y: int, hp: int | None = None) -> None:
        self.set_barrier(x, y, kind="door", hp=hp)

    def set_window(self, x: int, y: int, hp: int | None = None) -> None:
        self.set_barrier(x, y, kind="window", hp=hp)

    def set_source(self, x: int, y: int, intensity: float = 1000.0) -> None:
        """Установить источник огня."""
        if self.grid[y][x] < 0:
            return
        self.sources[(x, y)] = float(intensity)
        self.grid[y][x] = float(intensity)

    # ── Пожарные машины ──────────────────────────────────────────────────────

    def set_firetruck(
        self, truck_id: str, x: float, y: float, water: float = 2400.0,
    ) -> None:
        """Добавить или обновить пожарную машину."""
        if truck_id in self.firetrucks:
            t = self.firetrucks[truck_id]
            t.x = float(x)
            t.y = float(y)
            t.water = float(water)
        else:
            self.firetrucks[truck_id] = FireTruck(truck_id, x, y, water)

    def set_hydrant_connected(self, truck_id: str, connected: bool) -> None:
        """Подключение / отключение от гидранта."""
        t = self.firetrucks.get(truck_id)
        if t is not None:
            t.hydrant_connected = bool(connected)

    def sync_nozzles(
        self, truck_id: str, nozzles_data: list[dict[str, Any]],
    ) -> None:
        """Полная синхронизация стволов машины."""
        t = self.firetrucks.get(truck_id)
        if t is None:
            return
        t.nozzles = [
            Nozzle(
                nozzle_id=str(nd["id"]),
                x=nd["x"],
                y=nd["y"],
                angle=nd.get("angle", 0.0),
                spread_deg=nd.get("spread_deg", 45.0),
                is_open=nd.get("is_open", False),
            )
            for nd in nozzles_data
        ]

    # Legacy-метод для events.py (on_hose_update)
    def set_hose_nozzle(
        self, truck_id: str, nozzle_x: int, nozzle_y: int, is_open: bool,
    ) -> None:
        """Создаёт одиночный ствол (обратная совместимость)."""
        t = self.firetrucks.get(truck_id)
        if t is None:
            return
        if is_open and nozzle_x is not None and nozzle_y is not None:
            t.nozzles = [Nozzle(
                nozzle_id=f"{truck_id}_legacy",
                x=float(nozzle_x),
                y=float(nozzle_y),
                angle=0.0,
                spread_deg=45.0,
                is_open=True,
            )]
        else:
            t.nozzles = []

    # ── Проверка видимости (луч не пересекает барьер) ────────────────────────

    @staticmethod
    def _is_blocked(grid: list[list[float]], W: int, H: int,
                    sx: int, sy: int, ex: int, ey: int) -> bool:
        """True если линия от (sx,sy) до (ex,ey) пересекает барьер."""
        dx = ex - sx
        dy = ey - sy
        steps = max(abs(dx), abs(dy))
        if steps == 0:
            return False
        inv = 1.0 / steps
        for i in range(1, steps):
            tx = int(sx + dx * i * inv)
            ty = int(sy + dy * i * inv)
            if 0 <= tx < W and 0 <= ty < H and grid[ty][tx] < 0:
                return True
        return False

    # ── Физика воды ──────────────────────────────────────────────────────────

    def _apply_nozzle(self, nozzle: Nozzle) -> None:
        """Конус воды от одного ствола. Bounding-box оптимизация.

        Расход воды из бака списывается в update() до вызова — здесь только
        отмечаем клетки под водой и снижаем огонь.
        """
        nx, ny = nozzle.x, nozzle.y
        angle = nozzle.angle
        half_spread = math.radians(nozzle.spread_deg / 2.0)
        R = WATER_RADIUS
        amt = WATER_AMOUNT

        # Целочисленный центр для ray-check
        inx = int(round(nx))
        iny = int(round(ny))

        W, H = self.width, self.height
        grid = self.grid
        aw = self.active_water
        sources = self.sources

        # Bounding box вместо полного обхода сетки
        x0 = max(0, inx - R)
        x1 = min(W - 1, inx + R)
        y0 = max(0, iny - R)
        y1 = min(H - 1, iny + R)

        R_sq = float(R * R)
        is_blocked = self._is_blocked

        for cy in range(y0, y1 + 1):
            row = grid[cy]
            for cx in range(x0, x1 + 1):
                fdx = cx - nx
                fdy = cy - ny

                # Проверка радиуса (квадрат расстояния)
                if fdx * fdx + fdy * fdy > R_sq:
                    continue

                # Проверка конуса (угол)
                cell_angle = math.atan2(fdy, fdx)
                diff = (cell_angle - angle + math.pi) % _TWO_PI - math.pi
                if abs(diff) > half_spread:
                    continue

                # Вода не проходит через барьер
                cell = row[cx]
                if cell < 0:
                    continue
                if is_blocked(grid, W, H, inx, iny, cx, cy):
                    continue

                # Клетка под водой
                aw.add((cx, cy))

                # Тушим огонь
                if cell > 0:
                    row[cx] = max(0.0, cell - amt)

                # Тушим источник
                if (cx, cy) in sources:
                    sources[(cx, cy)] = max(0.0, sources[(cx, cy)] - amt)

    # ── Основной тик ─────────────────────────────────────────────────────────

    def update(self) -> bool:
        """Один тик симуляции.

        Returns:
            True если физика пересчитана, False если тик пропущен (speed_n).
        """
        self.ticks += 1
        if self.ticks % self.speed_n != 0:
            return False

        W, H = self.width, self.height
        grid = self.grid
        sources = self.sources

        # ── 1. Пополнение баков от гидрантов ─────────────────────────
        for truck in self.firetrucks.values():
            if truck.hydrant_connected:
                truck.water = min(truck.max_water, truck.water + HYDRANT_REFILL_RATE)

        # ── 2. Очистка active_water ──────────────────────────────────
        self.active_water.clear()

        # ── 3. Тушение конусами ──────────────────────────────────────
        # Фиксированный расход воды на каждый открытый ствол
        for truck in self.firetrucks.values():
            for nozzle in truck.nozzles:
                if nozzle.is_open and truck.water > 0:
                    truck.water = max(0.0, truck.water - WATER_PER_NOZZLE_TICK)
                    self._apply_nozzle(nozzle)

        aw = self.active_water

        # ── 4. Обновление сетки ──────────────────────────────────────
        # Shallow copy строк (float immutable → безопасно)
        new_grid = [row[:] for row in grid]
        can_spread = (self.ticks % FIRE_SPREAD_INTERVAL == 0)

        for y in range(H):
            old_row = grid[y]
            new_row = new_grid[y]
            for x in range(W):
                val = old_row[x]

                # 4a. Барьеры: разрушение от огня
                if val < 0:
                    # Гидрант — неразрушим
                    if val <= -9999:
                        continue

                    # Горящие ортогональные соседи
                    fire_near = False
                    for dx, dy in _ORTHO:
                        ax, ay = x + dx, y + dy
                        if 0 <= ax < W and 0 <= ay < H and grid[ay][ax] > 0:
                            fire_near = True
                            break

                    if fire_near:
                        new_hp = val + 1  # приближается к 0
                        if new_hp >= 0:
                            # Разрушен → огонь = среднее горящих соседей
                            total = 0.0
                            cnt = 0
                            for dx, dy in _ORTHO:
                                ax, ay = x + dx, y + dy
                                if 0 <= ax < W and 0 <= ay < H and grid[ay][ax] > 0:
                                    total += grid[ay][ax]
                                    cnt += 1
                            new_row[x] = total / cnt if cnt else 0.0
                        else:
                            new_row[x] = new_hp

                # 4b. Источники: рост intensity
                elif (x, y) in sources:
                    if (x, y) in aw:
                        new_row[x] = 0.0
                    else:
                        sources[(x, y)] += FIRE_SOURCE_GROWTH
                        new_row[x] = sources[(x, y)]

                # 4c. Горящие клетки: рост температуры
                elif val > 0:
                    if (x, y) in aw:
                        new_row[x] = 0.0
                    else:
                        new_row[x] = val + FIRE_GROWTH_PER_TICK

                # 4d. Пустые клетки: распространение огня
                elif can_spread:  # val == 0
                    total = 0.0
                    cnt = 0
                    for dx, dy in _NEIGHBORS_8:
                        ax, ay = x + dx, y + dy
                        if not (0 <= ax < W and 0 <= ay < H):
                            continue
                        nv = grid[ay][ax]
                        if nv <= 0:
                            continue
                        # Клетка под водой — огонь через неё не распространяется
                        if (ax, ay) in aw:
                            continue
                        # Диагональное перекрытие через угол барьера
                        if dx != 0 and dy != 0:
                            if grid[y][ax] < 0 or grid[ay][x] < 0:
                                continue
                        total += nv
                        cnt += 1
                    if cnt > 0:
                        new_row[x] = total / cnt

        self.grid = new_grid

        # ── 5. Очистка потухших источников ───────────────────────────
        self.sources = {p: v for p, v in sources.items() if v > 0}

        return True

    # ── Сериализация ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Полное состояние для JSON."""
        return {
            "ticks": self.ticks,
            "width": self.width,
            "height": self.height,
            "grid": self.grid,
            "sources": [
                {"x": x, "y": y, "intensity": v}
                for (x, y), v in self.sources.items()
            ],
            "active_water": [{"x": x, "y": y} for x, y in self.active_water],
            "trucks": [t.to_dict() for t in self.firetrucks.values()],
        }
