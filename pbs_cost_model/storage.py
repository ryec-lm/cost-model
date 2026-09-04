"""Persistence layer.

Deliberately separated from the CLI/command layer (see repository.py) and
kept behind a small `Store` interface: today `JSONFileStore` reads/writes a
single JSON document, but swapping in a `SqliteStore` later only requires
implementing `load()` / `save()` against the same raw-dict contract -- the
CLI commands and Repository never touch the file system directly.
"""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Dict


class Store(ABC):
    """Abstract persistence backend for the PBS tree document."""

    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """Return the persisted document as a plain dict (empty-doc default
        if nothing has been persisted yet)."""

    @abstractmethod
    def save(self, data: Dict[str, Any]) -> None:
        """Persist the given document, replacing whatever was there."""


EMPTY_DOCUMENT: Dict[str, Any] = {
    "schema_version": 1,
    "next_line_seq": 1,
    "lines": {},
}


class JSONFileStore(Store):
    """JSON-file-backed store. Human readable, diff/version-control friendly."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return json.loads(json.dumps(EMPTY_DOCUMENT))
        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return json.loads(json.dumps(EMPTY_DOCUMENT))
        return json.loads(content)

    def save(self, data: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        # Write atomically: temp file + rename, so a crash mid-write never
        # corrupts the existing document.
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".pbs_tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
