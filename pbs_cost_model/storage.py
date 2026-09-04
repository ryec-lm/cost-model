"""Persistence layer for the PBS tree.

The CLI/command layer never touches the storage format directly - it loads
the whole tree via a Repository, mutates the in-memory dict, and saves it
back. Swapping JSON for SQLite later means writing a new Repository
implementation; nothing in cli.py, calc.py, or validation.py would change.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

from .models import PBSLine, PBSTree


class PBSRepository(ABC):
    @abstractmethod
    def load(self) -> PBSTree:
        """Return the full tree as a dict of line_id -> PBSLine."""

    @abstractmethod
    def save(self, lines: PBSTree) -> None:
        """Persist the full tree, replacing whatever was stored before."""


class JSONRepository(PBSRepository):
    SCHEMA_VERSION = 1

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def load(self) -> PBSTree:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text())
        lines_data = raw.get("lines", [])
        return {d["line_id"]: PBSLine.from_dict(d) for d in lines_data}

    def save(self, lines: PBSTree) -> None:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "lines": [line.to_dict() for line in lines.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False))
        tmp_path.replace(self.path)


def next_line_id(lines: PBSTree) -> str:
    n = 1
    while f"L{n:03d}" in lines:
        n += 1
    return f"L{n:03d}"


def next_component_id(line: PBSLine) -> str:
    existing = {c.component_id for c in line.cost_components}
    n = 1
    while f"C{n}" in existing:
        n += 1
    return f"C{n}"
