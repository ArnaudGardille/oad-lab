"""Lecture des replays 0 A.D. : en-tête de commands.txt + metadata.json."""

import json
from pathlib import Path


def parse(d: Path):
    commands = d / "commands.txt"
    if not commands.exists():
        return None
    text = commands.read_text(errors="replace")
    first, _, _ = text.partition("\n")
    if not first.startswith("start "):
        return None
    try:
        settings = json.loads(first[len("start "):]).get("settings", {})
    except json.JSONDecodeError:
        return None

    players = [p or {} for p in settings.get("PlayerData", [])]
    ai_slots = [(p.get("AI"), p.get("AIDiff")) for p in players if p.get("AI")]

    out = {
        "dir": d.name,
        "path": d,
        "seed": settings.get("Seed"),
        "aiseed": settings.get("AISeed"),
        "biome": settings.get("Biome"),
        "ais": [a for a, _ in ai_slots],
        "diffs": [df for _, df in ai_slots],
        "turns": text.count("\nturn "),
        "states": None,
        "game_s": None,
    }
    meta = d / "metadata.json"
    if meta.exists():
        try:
            m = json.loads(meta.read_text(errors="replace"))
        except json.JSONDecodeError:
            # metadata tronqué par un SIGKILL en pleine écriture :
            # la partie reste "sans résultat", le batch continue.
            m = {}
        out["game_s"] = m.get("timeElapsed", 0) / 1000 or None
        states = [p.get("state") for p in m.get("playerStates", [])][1:]
        out["states"] = states or None
    return out


def key(parsed):
    return (parsed["seed"], parsed["aiseed"],
            tuple(parsed["ais"]), tuple(parsed["diffs"]))
