"""Catalogue of FLx800 serial commands (operator's manual, Appendix C) with safety classes.

The driver consults this table before sending anything: FORBIDDEN commands are
never sent, SERVICE commands require an explicit service-mode flag.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Safety(enum.Enum):
    SAFE = "safe"
    SERVICE = "service"      # writes NVRAM / fluidics: needs explicit confirmation
    FORBIDDEN = "forbidden"  # factory settings or undocumented


@dataclass(frozen=True)
class Command:
    char: str
    name: str
    safety: Safety = Safety.SAFE
    feature: Optional[int] = None  # 'z' object code that advertises it
    timeout_s: float = 6.0


_C = Command
COMMANDS: dict[str, Command] = {c.char: c for c in [
    _C("A", "store plate carrier", timeout_s=15),
    _C("B", "set serial number", Safety.FORBIDDEN, 1021),
    _C("C", "get serial number", feature=1021, timeout_s=2),
    _C("c", "run area scan", feature=1003, timeout_s=120),
    _C("d", "read well set", feature=1001, timeout_s=120),
    _C("E", "get dispense/read features", feature=1024, timeout_s=3),
    _C("e", "get basecode version", timeout_s=2),
    _C("f", "get sensitivity", feature=1010, timeout_s=2),
    _C("G", "set dispense/read definition", Safety.SERVICE, 1024),
    _C("g", "set temperature", feature=1009, timeout_s=3),
    _C("H", "get incubation setpoint", feature=1022, timeout_s=2),
    _C("h", "get temperature", feature=1009, timeout_s=2),
    _C("I", "dispenser control", Safety.SERVICE, 1024, timeout_s=300),
    _C("i", "include temperature response", feature=1009, timeout_s=2),
    _C("J", "present plate carrier", timeout_s=15),
    _C("m", "configure wavelengths (filter table)", Safety.SERVICE, 1004, timeout_s=30),
    _C("o", "get current status", timeout_s=2),
    _C("P", "get probe configuration", timeout_s=2),
    _C("p", "basecode checksum", Safety.FORBIDDEN, 1015),
    _C("q", "set quiet mode", timeout_s=2),
    _C("R", "set baud rate", feature=1028, timeout_s=2),
    _C("S", "read plate", timeout_s=120),
    _C("v", "set assay definition", timeout_s=6),
    _C("w", "get filter configuration", timeout_s=3),
    _C("X", "halt", feature=1017, timeout_s=15),
    _C("x", "terminate read series", feature=1017, timeout_s=15),
    _C("Y", "run well-mode dispense/read", Safety.SERVICE, 1024, timeout_s=600),
    _C("Z", "run plate-mode dispense/read", Safety.SERVICE, 1024, timeout_s=600),
    _C("z", "check feature", timeout_s=2),
    _C("$", "get minimum kinetic interval", timeout_s=3),
    _C("*", "self-test", timeout_s=180),
    _C("}", "get instrument configuration", timeout_s=2),
    _C(">", "get dispenser calibration", feature=1024, timeout_s=3),
    _C("#", "set dispenser calibration", Safety.FORBIDDEN, 1024),
]}

FEATURES: dict[int, str] = {
    1001: "well set reads", 1002: "reads using two wavelength sets", 1003: "area scanning",
    1004: "serial wavelength configuration", 1005: "shaking", 1006: "absorbance reads",
    1007: "high-speed sweep reads", 1008: "partial plate reads", 1009: "incubation",
    1010: "sensitivity response", 1011: "multi-wavelength read format",
    1012: "serial read mode selection", 1013: "ELx format status response",
    1014: "serial-only instrument", 1015: "basecode checksum", 1016: "barcode scanning",
    1017: "soft abort", 1018: "custom geometry", 1019: "align test", 1020: "timestamp",
    1021: "serial number", 1022: "get setpoint", 1023: "home filter wheel",
    1024: "dispense/read", 1025: "high temperature incubation", 1027: "handshaking mode",
    1028: "high baud rate", 1029: "time-resolved fluorescence", 1030: "strip code ID",
    1031: "return incubation ranges", 1032: "CTS", 1036: "rear injectors only",
}

INSTRUMENT_CONFIG: dict[int, str] = {
    0x0144: "FLx800 bottom probe", 0x014C: "FLx800 bottom probe + incubator",
    0x0544: "FLx800 top probe", 0x054C: "FLx800 top probe + incubator",
    0x0944: "FLx800 dual probes", 0x094C: "FLx800 dual probes + incubator",
    0x0044: "FLx800 bottom probe (basecode <=1.03)", 0x0444: "FLx800 top probe (basecode <=1.03)",
    0x0844: "FLx800 dual probes (basecode <=1.03)",
}
