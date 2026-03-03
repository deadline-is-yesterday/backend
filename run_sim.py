"""
Консольная визуализация симуляции пожара (FireSystem).
Запуск: python3 run_sim.py
"""

import math
import os
import time

from firesim.engine import FireSystem, Nozzle

# ── ANSI цвета ───────────────────────────────────────────────────────────────
RED    = '\033[91m'
BLUE   = '\033[94m'
GREY   = '\033[90m'
YELLOW = '\033[93m'
GREEN  = '\033[92m'
CYAN   = '\033[96m'
RESET  = '\033[0m'
BOLD   = '\033[1m'


def draw(sim: FireSystem) -> None:
    os.system('cls' if os.name == 'nt' else 'clear')

    trucks_by_pos: dict[tuple[int, int], str] = {}
    nozzles_by_pos: dict[tuple[int, int], tuple[str, bool]] = {}

    for t in sim.firetrucks.values():
        trucks_by_pos[(int(t.x), int(t.y))] = t.id
        for n in t.nozzles:
            nozzles_by_pos[(int(round(n.x)), int(round(n.y)))] = (t.id, n.is_open)

    # Заголовок
    sources_count = len(sim.sources)
    fire_cells = sum(1 for row in sim.grid for c in row if c > 0)
    water_cells = len(sim.active_water)
    print(f"{BOLD}{YELLOW}━━━ Симуляция пожара | Тик: {sim.ticks} ━━━{RESET}")
    print(f"  🔥 Очагов: {sources_count}  |  Горит клеток: {fire_cells}  |  💧 Вода: {water_cells} клеток")

    for t in sim.firetrucks.values():
        pct = t.water / t.max_water * 100 if t.max_water > 0 else 0
        hydrant = " ⛲ гидрант" if t.hydrant_connected else ""
        noz_info = f"{len(t.nozzles)} стволов" if t.nozzles else "нет стволов"
        print(f"  {GREEN}🚒 {t.id}: {t.water:.0f}/{t.max_water:.0f}л ({pct:.0f}%) | {noz_info}{hydrant}{RESET}")
    print()

    for y in range(sim.height):
        line = ""
        for x in range(sim.width):
            cell = sim.grid[y][x]

            if (x, y) in trucks_by_pos:
                line += f"{GREEN}🚒{RESET}"
            elif (x, y) in nozzles_by_pos:
                _, is_open = nozzles_by_pos[(x, y)]
                line += f"{CYAN}{'💦' if is_open else '⛔'}{RESET}"
            elif (x, y) in sim.active_water:
                line += f"{BLUE}~~{RESET}"
            elif (x, y) in sim.sources:
                line += f"{RED}🔥{RESET}"
            elif cell < 0:
                hp = abs(int(cell))
                if hp >= 9999:
                    line += f"{CYAN}HH{RESET}"
                else:
                    line += f"{GREY}██{RESET}"
            elif cell == 0:
                line += "· "
            elif cell > 500:
                line += f"{RED}▓▓{RESET}"
            elif cell > 100:
                line += f"{RED}▒▒{RESET}"
            elif cell > 10:
                line += f"{YELLOW}░░{RESET}"
            else:
                line += f"{YELLOW}··{RESET}"

        print(line)

    print()


# ── Конфигурация сцены ───────────────────────────────────────────────────────

sim = FireSystem(width=30, height=18, speed_n=1)

# Стены — здание (комната)
for x in range(3, 25):
    sim.set_barrier(x, 2, kind="wall")
    sim.set_barrier(x, 14, kind="wall")

for y in range(2, 15):
    sim.set_barrier(3, y, kind="wall")
    sim.set_barrier(24, y, kind="wall")

# Внутренняя перегородка с дверью
for y in range(2, 15):
    if y not in (7, 8):  # дверной проём
        sim.set_barrier(13, y, kind="wall")
    else:
        sim.set_barrier(13, y, kind="door")

# Окна
sim.set_barrier(8, 2, kind="window")
sim.set_barrier(18, 2, kind="window")
sim.set_barrier(8, 14, kind="window")
sim.set_barrier(18, 14, kind="window")

# Гидрант снаружи
sim.set_barrier(1, 16, kind="hydrant")

# Источник огня в левой комнате
sim.set_source(7, 8, intensity=1000)

# Машина снаружи
sim.set_firetruck("truck_1", x=10, y=16, water=24000)

# ── Симуляция ────────────────────────────────────────────────────────────────

DEPLOY_TICK = 20    # тик, когда ствол подаётся в комнату
TICK_DELAY  = 0.15  # секунды между тиками (визуализация)

for tick in range(500):
    # На 20-м тике — устанавливаем ствол, направленный на огонь
    if tick == DEPLOY_TICK:
        # Ствол через окно, целимся в сторону огня
        nozzle_x, nozzle_y = 8.0, 3.0  # внутри через окно
        angle = math.atan2(8 - nozzle_y, 7 - nozzle_x)  # в сторону (7, 8)
        sim.sync_nozzles("truck_1", [{
            "id": "nozzle_1",
            "x": nozzle_x,
            "y": nozzle_y,
            "angle": angle,
            "spread_deg": 60.0,
            "is_open": True,
        }])

    draw(sim)
    sim.update()
    time.sleep(TICK_DELAY)

    # Конец: если все источники потушены
    if not sim.sources and tick > DEPLOY_TICK + 5:
        draw(sim)
        print(f"{GREEN}✅ Пожар потушен на тике {sim.ticks}!{RESET}")
        break
