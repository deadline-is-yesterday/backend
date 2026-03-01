"""
Append-only JSONL logger for game events.

Log file: firemap/games/<game_id>_log.jsonl
Each line is a JSON object: {"ts": "...", "event": "...", "data": {...}}
"""

import json
import os
from datetime import datetime, timezone

_GAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "firemap",
    "games",
)


def log_event(game_id: str, event: str, data: dict | None = None) -> None:
    """Append a single log entry to the game's JSONL file."""
    if not game_id or game_id == "0":
        return
    path = os.path.join(_GAMES_DIR, f"{game_id}_log.jsonl")
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "data": data or {},
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_logs(game_id: str) -> list[dict]:
    """Read all log entries for a game."""
    path = os.path.join(_GAMES_DIR, f"{game_id}_log.jsonl")
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
