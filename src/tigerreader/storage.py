"""Parameter database: SQLite for protocols and settings only.

Measurement data never goes here; it is written as ASCII run folders (``export_ascii``).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .readsetup import ReadSetup


def setup_from_json(text: str):
    """A stored protocol: ``AbsSetup`` (Model 680, ``"instrument": "m680"``) or ``ReadSetup`` (FLx800)."""
    d = json.loads(text)
    if d.get("instrument") == "m680":
        from .m680.setup import AbsSetup
        return AbsSetup.from_dict(d)
    return ReadSetup.from_dict(d)


def instrument_of(setup) -> str:
    return getattr(setup, "instrument", "flx800")

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS protocol (
    name      TEXT PRIMARY KEY,
    body      TEXT NOT NULL,            -- ReadSetup (FLx800) or AbsSetup (Model 680) as JSON
    source    TEXT NOT NULL DEFAULT '',
    created   TEXT NOT NULL,
    modified  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


DATA_DIR_NAME = "TigerReaderData"
DB_NAME = "tigerreader_params.db"


def default_data_dir() -> Path:
    return Path.home() / DATA_DIR_NAME


def default_db_path() -> Path:
    return default_data_dir() / DB_NAME


class ParamStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(_SCHEMA)
        if self.db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self.db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ protocols
    def protocol_names(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT name FROM protocol ORDER BY name COLLATE NOCASE")]

    def protocols(self, instrument: Optional[str] = None) -> list[tuple[str, str, str]]:
        """(name, source, modified) for every stored protocol (of *instrument*: "flx800" or "m680")."""
        rows = self.db.execute("SELECT name, source, modified, body FROM protocol ORDER BY name COLLATE NOCASE")
        return [(n, s, m) for n, s, m, body in rows
                if instrument is None or json.loads(body).get("instrument", "flx800") == instrument]

    def load(self, name: str):
        row = self.db.execute("SELECT body FROM protocol WHERE name = ?", (name,)).fetchone()
        return setup_from_json(row[0]) if row else None

    def save(self, setup, overwrite: bool = True) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        old = self.load(setup.name)
        if old is not None and not overwrite:
            raise FileExistsError(setup.name)
        if old is not None and instrument_of(old) != instrument_of(setup):
            raise ValueError(f"the name \u201c{setup.name}\u201d is used by a protocol of the other instrument")
        source = getattr(setup, "source", "")
        if old is not None:
            self.db.execute("UPDATE protocol SET body = ?, source = ?, modified = ? WHERE name = ?",
                            (setup.to_json(), source, now, setup.name))
        else:
            self.db.execute("INSERT INTO protocol VALUES (?, ?, ?, ?, ?)",
                            (setup.name, setup.to_json(), source, now, now))
        self.db.commit()

    def delete(self, name: str) -> None:
        self.db.execute("DELETE FROM protocol WHERE name = ?", (name,))
        self.db.commit()

    def unique_name(self, name: str) -> str:
        if self.load(name) is None:
            return name
        i = 2
        while self.load(f"{name} ({i})") is not None:
            i += 1
        return f"{name} ({i})"

    # ------------------------------------------------------------------ settings
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO setting VALUES (?, ?)", (key, value))
        self.db.commit()
