"""Assay definition ('v' command) and filter configuration ('w'/'m') encoding.

Layout from the FLx800 operator's manual, Appendix C, "SET ASSAY DEFINITION ('v')".
The 'v' argument block is 108 data bytes followed
by a 3-digit checksum (8-bit sum of the 108 bytes) = 111 bytes.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Optional

from .framing import ProtocolError, checksum_field, num

PLUG_WL = 0
OPEN_WL = 1


# --------------------------------------------------------------------------- enums
class ReadMode(enum.IntEnum):
    ENDPOINT = 1
    KINETIC = 2
    DISPENSE_READ = 5


class AssayType(enum.IntEnum):
    FLUORESCENCE = 2
    LUMINESCENCE = 3


class Optics(enum.IntEnum):
    BOTTOM = 0
    TOP = 1


class SensitivityMode(enum.IntEnum):
    FIXED = 0
    AUTO_ONBOARD = 1  # "scale to high well": uses bytes 42-50, query result with 'f'
    AUTO_SERIAL = 2   # auto-sense blocks returned to the host


class ShakeMode(enum.IntEnum):
    NONE = 0
    BEFORE_READ = 1
    BEFORE_EACH_KINETIC_READ = 2


class ShakeSpeed(enum.IntEnum):
    SLOW = 0
    MEDIUM = 1
    FAST = 2
    VARIABLE = 3


class SeriesPosition(enum.IntEnum):
    SINGLE = 0
    FIRST = 1
    NEXT = 2
    FINAL = 3


@dataclass(frozen=True)
class Geometry:
    code: int
    rows: int
    cols: int
    name: str

    @property
    def wells(self) -> int:
        return self.rows * self.cols


# Plate geometries of the FLx800 (geometry code, rows, columns, name), as listed in Appendix C.
GEOMETRIES: dict[str, Geometry] = {
    "2x3": Geometry(8, 2, 3, "2x3 (6 wells)"),
    "3x4": Geometry(9, 3, 4, "3x4 (12 wells)"),
    "4x6": Geometry(0, 4, 6, "4x6 (24 wells)"),
    "6x8": Geometry(1, 6, 8, "6x8 (48 wells)"),            # Appendix C misprints "48 wells (8x12)"
    "8x12": Geometry(2, 8, 12, "8x12 (96 wells)"),
    "8x12H": Geometry(7, 8, 12, "8x12 Hellma (96 wells)"),
    "8x12M": Geometry(10, 8, 12, "8x12 Metric (96 wells)"),
    "16x24": Geometry(3, 16, 24, "16x24 (384 wells)"),
}
GEOMETRY_ALIASES: dict[str, str] = {
    "6": "2x3", "12": "3x4", "24": "4x6", "48": "6x8", "96": "8x12", "96H": "8x12H", "96M": "8x12M", "384": "16x24",
}
for _alias, _key in GEOMETRY_ALIASES.items():
    GEOMETRIES[_alias] = GEOMETRIES[_key]
GEOMETRY_BY_CODE = {g.code: g for g in GEOMETRIES.values()}


def geometry(name: str) -> Geometry:
    """Look up a geometry by name ('8x12', '6x8', '8x12H') or alias ('96', '384')."""
    key = name.strip().replace(" ", "").replace("(", "").replace(")", "")
    for k in (key, key.upper(), key.lower(), key[:-1] + key[-1].upper()):
        if k in GEOMETRIES:
            return GEOMETRIES[k]
    raise KeyError(f"unknown plate geometry {name!r}; choose from {', '.join(PLATE_CHOICES)}")


PLATE_CHOICES = ["2x3", "3x4", "4x6", "6x8", "8x12", "8x12H", "8x12M", "16x24"]


# --------------------------------------------------------------------------- filters
@dataclass(frozen=True)
class Filter:
    """A filter-wheel position: a band-pass filter, a plug or an open hole."""

    wavelength: int  # nm; 0 = plug, 1 = open
    bandpass: int = 0

    @classmethod
    def plug(cls) -> "Filter":
        return cls(PLUG_WL, 0)

    @classmethod
    def open(cls) -> "Filter":
        return cls(OPEN_WL, 0)

    @classmethod
    def parse(cls, text: str) -> "Filter":
        """Parse '485/20', 'plug' or 'open'."""
        t = text.strip().lower()
        if t in ("plug", "p"):
            return cls.plug()
        if t in ("open", "o", "air"):
            return cls.open()
        wl, _, bw = t.partition("/")
        return cls(int(wl), int(bw or 0))

    @property
    def is_plug(self) -> bool:
        return self.wavelength == PLUG_WL

    @property
    def is_open(self) -> bool:
        return self.wavelength == OPEN_WL

    @property
    def is_filter(self) -> bool:
        return self.wavelength >= 200

    def encode(self) -> bytes:
        return num(self.wavelength, 4) + num(self.bandpass, 3)

    @classmethod
    def decode(cls, raw: bytes) -> "Filter":
        return cls(int(raw[:4]), int(raw[4:7]))

    def __str__(self) -> str:
        if self.is_plug:
            return "Plug"
        if self.is_open:
            return "Open"
        return f"{self.wavelength}/{self.bandpass}"


@dataclass
class FilterTable:
    """The reader's filter configuration: four excitation + four emission positions."""

    excitation: list[Filter]
    emission: list[Filter]

    def encode(self) -> bytes:
        if len(self.excitation) != 4 or len(self.emission) != 4:
            raise ValueError("filter table needs exactly 4 excitation and 4 emission entries")
        return b"".join(f.encode() for f in [*self.excitation, *self.emission])

    @classmethod
    def decode(cls, raw: bytes) -> "FilterTable":
        if len(raw) != 56:
            raise ProtocolError(f"filter table must be 56 bytes, got {len(raw)}")
        f = [Filter.decode(raw[i * 7:(i + 1) * 7]) for i in range(8)]
        return cls(f[:4], f[4:])

    def has(self, flt: Filter, emission: bool) -> bool:
        table = self.emission if emission else self.excitation
        return any(t.wavelength == flt.wavelength and (flt.wavelength < 200 or t.bandpass == flt.bandpass)
                   for t in table)


# --------------------------------------------------------------------------- assay
@dataclass(frozen=True)
class WellRect:
    first_row: int = 1
    first_col: int = 1
    last_row: int = 8
    last_col: int = 12

    @classmethod
    def parse(cls, text: str) -> "WellRect":
        """Parse 'A1:H12' (or 'A1-H12')."""
        a, _, b = text.replace("-", ":").partition(":")
        r1, c1 = well_to_rc(a)
        r2, c2 = well_to_rc(b or a)
        return cls(r1, c1, r2, c2)

    @classmethod
    def full(cls, geometry: Geometry) -> "WellRect":
        return cls(1, 1, geometry.rows, geometry.cols)

    @property
    def n_rows(self) -> int:
        return self.last_row - self.first_row + 1

    @property
    def n_cols(self) -> int:
        return self.last_col - self.first_col + 1

    def encode(self) -> bytes:
        return num(self.first_row, 2) + num(self.first_col, 2) + num(self.last_row, 2) + num(self.last_col, 2)

    @classmethod
    def decode(cls, raw: bytes) -> "WellRect":
        return cls(int(raw[0:2]), int(raw[2:4]), int(raw[4:6]), int(raw[6:8]))


UNUSED_RECT_BYTES = b"00000000"


def well_to_rc(well: str) -> tuple[int, int]:
    """'B7' -> (2, 7) (1-based)."""
    w = well.strip().upper()
    return ord(w[0]) - ord("A") + 1, int(w[1:])


def rc_to_well(row: int, col: int) -> str:
    return f"{chr(ord('A') + row - 1)}{col}"


@dataclass(frozen=True)
class FilterSet:
    excitation: Filter
    emission: Filter
    optics: Optics = Optics.TOP
    samples_per_well: int = 10
    delay_before_ms: int = 350
    delay_between_ms: int = 1   # defaults: 10 samples, 350 ms before, 1 ms between
    sensitivity_mode: SensitivityMode = SensitivityMode.FIXED
    sensitivity: int = 100

    def encode(self) -> bytes:
        if self.delay_before_ms % 10:
            raise ValueError("delay before sampling must be a multiple of 10 ms")
        return (
            num(int(self.optics), 1)
            + self.excitation.encode()
            + self.emission.encode()
            + num(self.samples_per_well, 3)
            + num(self.delay_before_ms // 10, 3)
            + num(self.delay_between_ms, 3)
            + num(int(self.sensitivity_mode), 1)
            + num(self.sensitivity, 3)
        )

    @classmethod
    def decode(cls, raw: bytes) -> "FilterSet":
        if len(raw) != 28:
            raise ProtocolError("filter set block must be 28 bytes")
        return cls(
            optics=Optics(int(raw[0:1])),
            excitation=Filter.decode(raw[1:8]),
            emission=Filter.decode(raw[8:15]),
            samples_per_well=int(raw[15:18]),
            delay_before_ms=int(raw[18:21]) * 10,
            delay_between_ms=int(raw[21:24]),
            sensitivity_mode=SensitivityMode(int(raw[24:25])),
            sensitivity=int(raw[25:28]),
        )


# Values sent in fields that the selected mode does not use.
UNUSED_FILTERSET_BYTES = b"0" * 28
ENDPOINT_KINETIC_READS = 10      # kinetic fields of an endpoint definition (not used by the reader)
ENDPOINT_KINETIC_INTERVAL = 10


@dataclass(frozen=True)
class AssayDefinition:
    filter_sets: tuple[FilterSet, ...]
    read_mode: ReadMode = ReadMode.ENDPOINT
    assay_type: AssayType = AssayType.FLUORESCENCE
    geometry: Geometry = GEOMETRIES["8x12"]
    wells: Optional[WellRect] = None  # None = full plate
    onboard_autosense_well: tuple[int, int] = (1, 1)
    onboard_autosense_max: int = 90000
    kinetic_reads: int = ENDPOINT_KINETIC_READS
    kinetic_interval_s: int = ENDPOINT_KINETIC_INTERVAL
    shake_mode: ShakeMode = ShakeMode.NONE
    shake_speed: ShakeSpeed = ShakeSpeed.MEDIUM
    series_position: SeriesPosition = SeriesPosition.SINGLE
    eject_between: bool = False
    autosense_block2: Optional[WellRect] = None
    autosense_block3: Optional[WellRect] = None
    shake_time_s: int = 0

    # ---------------------------------------------------------------- helpers
    @property
    def read_rect(self) -> WellRect:
        return self.wells or WellRect.full(self.geometry)

    @property
    def n_kinetic(self) -> int:
        return self.kinetic_reads if self.read_mode == ReadMode.KINETIC else 1

    def estimated_pass_s(self) -> float:
        """Estimated time for one pass over the plate (all filter sets).

        Fitted to measured reads (FLx800T, 96 wells, 10 samples, 350 ms + 10 ms):
        78 s per filter set = 0.81 s/well = samples x (10 ms + between) + delay before + ~0.26 s move/settle.
        """
        r = self.read_rect
        per_well = sum(fs.samples_per_well * (0.010 + fs.delay_between_ms / 1000) + fs.delay_before_ms / 1000 + 0.26
                       for fs in self.filter_sets)
        return r.n_rows * r.n_cols * per_well + self.shake_time_s

    def serial_autosense_blocks(self) -> list[WellRect]:
        """Well rectangles returned as auto-sense pre-read blocks (sensitivity mode 2)."""
        if self.filter_sets[0].sensitivity_mode != SensitivityMode.AUTO_SERIAL:
            return []
        blocks = [self.read_rect]
        if len(self.filter_sets) == 1:
            for b in (self.autosense_block2, self.autosense_block3):
                if b is None:
                    break
                blocks.append(b)
        return blocks

    # ---------------------------------------------------------------- codec
    def data_bytes(self) -> bytes:
        """The 108 data bytes (without checksum)."""
        if not 1 <= len(self.filter_sets) <= 2:
            raise ValueError("an assay definition holds 1 or 2 filter sets")
        fs2 = self.filter_sets[1].encode() if len(self.filter_sets) == 2 else UNUSED_FILTERSET_BYTES
        row, col = self.onboard_autosense_well
        b = (
            num(int(self.read_mode), 1)
            + num(int(self.assay_type), 1)
            + num(self.geometry.code, 2)
            + self.read_rect.encode()
            + num(len(self.filter_sets), 1)
            + self.filter_sets[0].encode()
            + num(row, 2) + num(col, 2)
            + num(self.onboard_autosense_max, 5)
            + num(self.kinetic_reads, 3)
            + num(self.kinetic_interval_s, 4)
            + num(int(self.shake_mode), 1)
            + num(int(self.shake_speed), 1)
            + num(int(self.series_position), 1)
            + num(int(self.eject_between), 1)
            + (self.autosense_block2.encode() if self.autosense_block2 else UNUSED_RECT_BYTES)
            + (self.autosense_block3.encode() if self.autosense_block3 else UNUSED_RECT_BYTES)
            + fs2
            + num(self.shake_time_s, 3)
        )
        assert len(b) == 108, len(b)
        return b

    def encode(self) -> bytes:
        """The full 111-byte argument block for the 'v' command."""
        d = self.data_bytes()
        return d + checksum_field(d)

    @classmethod
    def decode(cls, raw: bytes) -> "AssayDefinition":
        if len(raw) != 111:
            raise ProtocolError(f"assay definition must be 111 bytes, got {len(raw)}")
        d, ck = raw[:108], raw[108:]
        if checksum_field(d) != ck:
            raise ProtocolError("assay definition checksum mismatch")
        nfs = int(d[12:13])
        fs = [FilterSet.decode(d[13:41])]
        if nfs == 2:
            fs.append(FilterSet.decode(d[77:105]))
        b2, b3 = d[61:69], d[69:77]
        return cls(
            read_mode=ReadMode(int(d[0:1])),
            assay_type=AssayType(int(d[1:2])),
            geometry=GEOMETRY_BY_CODE[int(d[2:4])],
            wells=WellRect.decode(d[4:12]),
            filter_sets=tuple(fs),
            onboard_autosense_well=(int(d[41:43]), int(d[43:45])),
            onboard_autosense_max=int(d[45:50]),
            kinetic_reads=int(d[50:53]),
            kinetic_interval_s=int(d[53:57]),
            shake_mode=ShakeMode(int(d[57:58])),
            shake_speed=ShakeSpeed(int(d[58:59])),
            series_position=SeriesPosition(int(d[59:60])),
            eject_between=d[60:61] == b"1",
            autosense_block2=None if b2[:2] == b"00" else WellRect.decode(b2),
            autosense_block3=None if b3[:2] == b"00" else WellRect.decode(b3),
            shake_time_s=int(d[105:108]),
        )

    def with_(self, **changes) -> "AssayDefinition":
        return replace(self, **changes)

    # ---------------------------------------------------------------- validation
    def validate(self, table: Optional[FilterTable] = None) -> list[str]:
        """Return a list of problems (empty = OK): the reader's own checks plus PMT protection."""
        p: list[str] = []
        g, r = self.geometry, self.read_rect
        if not (1 <= r.first_row <= r.last_row <= g.rows and 1 <= r.first_col <= r.last_col <= g.cols):
            p.append(f"well range {rc_to_well(r.first_row, r.first_col)}:{rc_to_well(r.last_row, r.last_col)} "
                     f"outside {g.name}")
        if not 1 <= len(self.filter_sets) <= 2:
            p.append("1 or 2 filter sets per definition (use read series for more)")
        for i, fs in enumerate(self.filter_sets, 1):
            tag = f"filter set {i}"
            if not 1 <= fs.samples_per_well <= 255:
                p.append(f"{tag}: samples per well must be 1-255")
            if not (10 <= fs.delay_before_ms <= 2550 and fs.delay_before_ms % 10 == 0):
                p.append(f"{tag}: delay before sampling must be 10-2550 ms in 10 ms steps")
            if not 0 <= fs.delay_between_ms <= 255:
                p.append(f"{tag}: delay between samples must be 0-255 ms")
            fixed = fs.sensitivity_mode == SensitivityMode.FIXED
            if fixed and not (fs.sensitivity == 0 or 25 <= fs.sensitivity <= 255):
                p.append(f"{tag}: sensitivity must be 0 or 25-255")
            for name, f in (("excitation", fs.excitation), ("emission", fs.emission)):
                if f.is_filter and not (200 <= f.wavelength <= 999 and 1 <= f.bandpass <= 99):
                    p.append(f"{tag}: {name} {f} out of range (200-999 nm, bandpass 1-99)")
                elif not f.is_filter and f.wavelength not in (PLUG_WL, OPEN_WL):
                    p.append(f"{tag}: {name} wavelength {f.wavelength} invalid")
            ex, em = fs.excitation, fs.emission
            if self.assay_type == AssayType.FLUORESCENCE:
                if not ex.is_filter or not em.is_filter:
                    p.append(f"{tag}: fluorescence needs real excitation and emission filters (no Plug/Open)")
                elif abs(ex.wavelength - em.wavelength) <= (ex.bandpass + em.bandpass) / 2:
                    p.append(f"{tag}: excitation {ex} and emission {em} band-passes overlap (PMT risk)")
            else:
                if ex.is_filter or ex.is_open:
                    p.append(f"{tag}: luminescence should use a Plug in the excitation wheel")
                if em.is_plug:
                    p.append(f"{tag}: luminescence needs an emission filter or Open, not a Plug")
            if table is not None:
                if not table.has(ex, emission=False):
                    p.append(f"{tag}: excitation {ex} not in reader filter table")
                if not table.has(em, emission=True):
                    p.append(f"{tag}: emission {em} not in reader filter table")
        if self.read_mode == ReadMode.KINETIC:
            if not 2 <= self.kinetic_reads <= 300:
                p.append("kinetic reads must be 2-300")
            if not 0 <= self.kinetic_interval_s <= 9999:
                p.append("kinetic interval must be 0-9999 s")
        if not 100 <= self.onboard_autosense_max <= 90000:
            p.append("on-board auto-sensitivity max must be 100-90000")
        if not 0 <= self.shake_time_s <= 999:
            p.append("shake time must be 0-999 s")
        return p


@dataclass
class ReadSeries:
    """More than 2 filter sets: split into several definitions read as a series."""

    definitions: list[AssayDefinition] = field(default_factory=list)

    @classmethod
    def split(cls, base: AssayDefinition, filter_sets: list[FilterSet]) -> "ReadSeries":
        chunks = [tuple(filter_sets[i:i + 2]) for i in range(0, len(filter_sets), 2)]
        if len(chunks) == 1:
            return cls([base.with_(filter_sets=chunks[0], series_position=SeriesPosition.SINGLE)])
        defs = []
        for i, ch in enumerate(chunks):
            last = i == len(chunks) - 1
            pos = SeriesPosition.FIRST if i == 0 else SeriesPosition.FINAL if last else SeriesPosition.NEXT
            defs.append(base.with_(filter_sets=ch, series_position=pos))
        return cls(defs)
