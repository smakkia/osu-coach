"""osu! stable mod bitflags."""

from enum import IntFlag


class Mods(IntFlag):
    NoFail = 1
    Easy = 2
    TouchDevice = 4
    Hidden = 8
    HardRock = 16
    SuddenDeath = 32
    DoubleTime = 64
    Relax = 128
    HalfTime = 256
    Nightcore = 512
    Flashlight = 1024
    Autoplay = 2048
    SpunOut = 4096
    Autopilot = 8192
    Perfect = 16384
    ScoreV2 = 1 << 29


_SHORT = [
    (Mods.NoFail, "NF"), (Mods.Easy, "EZ"), (Mods.TouchDevice, "TD"), (Mods.Hidden, "HD"),
    (Mods.HardRock, "HR"), (Mods.SuddenDeath, "SD"), (Mods.Nightcore, "NC"),
    (Mods.DoubleTime, "DT"), (Mods.Relax, "RX"), (Mods.HalfTime, "HT"),
    (Mods.Flashlight, "FL"), (Mods.Autoplay, "AT"), (Mods.SpunOut, "SO"),
    (Mods.Autopilot, "AP"), (Mods.Perfect, "PF"), (Mods.ScoreV2, "V2"),
]


def mods_string(mods: int) -> str:
    names = []
    for flag, name in _SHORT:
        if mods & flag:
            if name == "DT" and mods & Mods.Nightcore:
                continue
            if name == "SD" and mods & Mods.Perfect:
                continue
            names.append(name)
    return "".join(names) or "NM"


def clock_rate(mods: int) -> float:
    if mods & (Mods.DoubleTime | Mods.Nightcore):
        return 1.5
    if mods & Mods.HalfTime:
        return 0.75
    return 1.0
