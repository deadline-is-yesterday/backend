"""
Консольная визуализация симуляции пожара.
Запуск: python3 run_sim.py
"""

import os
import time

from firesim.engine import FireSystem

# ── ANSI цвета ───────────────────────────────────────────────────────────────
RED    = '\033[91m'
BLUE   = '\033[94m'
GREY   = '\033[90m'
YELLOW = '\033[93m'
GREEN  = '\033[92m'
RESET  = '\033[0m'


def draw(sim: FireSystem) -> None:
    os.system('cls' if os.name == 'nt' else 'clear')

    trucks_by_pos = {(t.x, t.y): t for t in sim.firetrucks.values()}
    nozzles_by_pos = {
        (t.nozzle_x, t.nozzle_y): t
        for t in sim.firetrucks.values()
        if t.nozzle_x is not None
    }

    # Заголовок
    print(f"{YELLOW}━━━ Симуляция пожара | Тик: {sim.ticks} ━━━{RESET}")
    trucks_info = "  ".join(
        f"{GREEN}🚒 {t.id}: {t.water:.0f}л{RESET}"
        for t in sim.firetrucks.values()
    )
    if trucks_info:
        print(trucks_info)
    print()

    for y in range(sim.height):
        line = ""
        for x in range(sim.width):
            cell = sim.grid[y][x]

            if (x, y) in trucks_by_pos:
                line += f"{GREEN}🚒    {RESET} "
            elif (x, y) in nozzles_by_pos:
                t = nozzles_by_pos[(x, y)]
                marker = "🚿" if t.hose_open else "⛔"
                line += f"{BLUE}{marker}    {RESET} "
            elif (x, y) in sim.active_water:
                val = f"{cell:.0f}" if cell > 0 else "0"
                line += f"{BLUE}💧{val:<4}{RESET} "
            elif (x, y) in sim.sources:
                line += f"{RED}🔥{int(cell):<4}{RESET} "
            elif cell < 0:
                hp = abs(int(cell))
                line += f"{GREY}█{hp:<4}█{RESET}"
            elif cell == 0:
                line += ".      "
            elif cell > 100:
                line += f"{RED}{cell:<6.0f}{RESET} "
            else:
                line += f"{YELLOW}{cell:<6.1f}{RESET} "

        print(line)

    print()


# ── Конфигурация сцены ───────────────────────────────────────────────────────

sim = FireSystem(width=20, height=12, speed_n=1)

# Стены — комната с проходом
for x in range(2, 18):
    sim.set_wall(x, 1, -100)
    if x not in (9, 10):           # проход
        sim.set_wall(x, 8, -100)

for y in range(1, 9):
    sim.set_wall(2,  y, -100)
    sim.set_wall(17, y, -100)

# Источник огня
sim.set_source(6, 4, intensity=1000)

# Машина снаружи
sim.set_firetruck("truck_1", x=9, y=11, water=240000)

# ── Симуляция ────────────────────────────────────────────────────────────────

DEPLOY_TICK  = 15   # тик, когда подают рукав
SPRAY_TICK   = 15   # тик, когда начинают тушить

for tick in range(200):
    # На 15-м тике — разворачиваем рукав в дверь и тушим
    if tick == DEPLOY_TICK:
        sim.set_hose_nozzle("truck_1", nozzle_x=7, nozzle_y=5, is_open=True)

    draw(sim)
    sim.update()

    # Конец: если все источники потушены
    if not sim.sources:
        draw(sim)
        print(f"{GREEN}✅ Пожар потушен на тике {sim.ticks}!{RESET}")
        break
