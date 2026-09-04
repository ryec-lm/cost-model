"""Data-access layer.

Wraps a `Store` (storage.py) and exposes domain-level operations (add/get/
update/delete lines & components, id generation, tree loading) that the CLI
commands call. This is the seam a future SQLite-backed implementation would
replace -- `cli.py` only ever talks to a `Repository`, never to a `Store` or
to raw dicts/files.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import PBSTree, PBSLine, CostComponent
from .storage import Store


class RepositoryError(Exception):
    """Raised for data-access-level problems (not found, duplicate id, ...)."""


class Repository:
    def __init__(self, store: Store):
        self._store = store
        self.tree: PBSTree = PBSTree.from_dict(self._store.load())

    # -- persistence ------------------------------------------------------

    def reload(self) -> None:
        self.tree = PBSTree.from_dict(self._store.load())

    def save(self) -> None:
        self._store.save(self.tree.to_dict())

    # -- line reads ---------------------------------------------------------

    def get_line(self, line_id: str) -> Optional[PBSLine]:
        return self.tree.lines.get(line_id)

    def require_line(self, line_id: str) -> PBSLine:
        line = self.get_line(line_id)
        if line is None:
            raise RepositoryError(f"No such line_id: {line_id!r}")
        return line

    def list_lines(self) -> List[PBSLine]:
        return list(self.tree.lines.values())

    def children_of(self, line_id: Optional[str]) -> List[PBSLine]:
        return self.tree.children_of(line_id)

    def roots(self) -> List[PBSLine]:
        return self.tree.roots()

    def has_children(self, line_id: str) -> bool:
        return self.tree.has_children(line_id)

    def descendants_of(self, line_id: str) -> List[PBSLine]:
        """All descendants (children, grandchildren, ...) of a line."""
        result: List[PBSLine] = []
        stack = [c.line_id for c in self.children_of(line_id)]
        while stack:
            cur = stack.pop()
            line = self.tree.lines[cur]
            result.append(line)
            stack.extend(c.line_id for c in self.children_of(cur))
        return result

    def ancestors_of(self, line_id: str) -> List[PBSLine]:
        result: List[PBSLine] = []
        line = self.get_line(line_id)
        seen = set()
        while line and line.parent_line_id:
            if line.parent_line_id in seen:
                break  # defensive: don't loop forever on corrupt data
            seen.add(line.parent_line_id)
            parent = self.get_line(line.parent_line_id)
            if parent is None:
                break
            result.append(parent)
            line = parent
        return result

    # -- line id generation -------------------------------------------------

    def _next_line_id(self) -> str:
        while True:
            candidate = f"L{self.tree.next_line_seq}"
            self.tree.next_line_seq += 1
            if candidate not in self.tree.lines:
                return candidate

    # -- line writes ----------------------------------------------------

    def add_line(
        self,
        line_name: str,
        parent_line_id: Optional[str] = None,
        cost_method: Optional[str] = None,
        line_id: Optional[str] = None,
        **attrs: Any,
    ) -> PBSLine:
        if parent_line_id is not None and parent_line_id not in self.tree.lines:
            raise RepositoryError(f"parent_line_id {parent_line_id!r} does not exist")

        if line_id is None:
            line_id = self._next_line_id()
        elif line_id in self.tree.lines:
            raise RepositoryError(f"line_id {line_id!r} already exists")

        line = PBSLine(
            line_id=line_id,
            line_name=line_name,
            parent_line_id=parent_line_id,
            cost_method=cost_method,
            **attrs,
        )
        self.tree.lines[line_id] = line
        return line

    def update_line(self, line_id: str, **changes: Any) -> PBSLine:
        line = self.require_line(line_id)
        if "parent_line_id" in changes:
            new_parent = changes["parent_line_id"]
            if new_parent is not None and new_parent not in self.tree.lines:
                raise RepositoryError(f"parent_line_id {new_parent!r} does not exist")
            if new_parent == line_id:
                raise RepositoryError("a line cannot be its own parent")
            if new_parent is not None and line in self.ancestors_of(new_parent):
                raise RepositoryError("cannot move a line under its own descendant")
        for key, value in changes.items():
            setattr(line, key, value)
        return line

    def delete_line(self, line_id: str) -> None:
        self.require_line(line_id)
        del self.tree.lines[line_id]

    def reassign_children(self, line_id: str, new_parent_id: Optional[str]) -> None:
        """Re-point every direct child of `line_id` to `new_parent_id`."""
        for child in self.children_of(line_id):
            child.parent_line_id = new_parent_id

    def cascade_delete(self, line_id: str) -> List[str]:
        """Delete `line_id` and its entire subtree. Returns deleted ids."""
        to_delete = [d.line_id for d in self.descendants_of(line_id)] + [line_id]
        for lid in to_delete:
            self.tree.lines.pop(lid, None)
        return to_delete

    # -- component id generation ---------------------------------------

    @staticmethod
    def _next_component_id(existing: List[CostComponent]) -> str:
        max_seq = 0
        for c in existing:
            m = re.match(r"^C(\d+)$", c.component_id)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"C{max_seq + 1}"

    # -- component writes ------------------------------------------------

    def add_component(
        self,
        line_id: str,
        cost_type: str,
        cost_method: str,
        component_id: Optional[str] = None,
        **attrs: Any,
    ) -> CostComponent:
        line = self.require_line(line_id)
        if component_id is None:
            component_id = self._next_component_id(line.cost_components)
        elif any(c.component_id == component_id for c in line.cost_components):
            raise RepositoryError(
                f"component_id {component_id!r} already exists on line {line_id!r}"
            )
        component = CostComponent(
            component_id=component_id,
            cost_type=cost_type,
            cost_method=cost_method,
            **attrs,
        )
        line.cost_components.append(component)
        return component

    def get_component(self, line_id: str, component_id: str) -> Optional[CostComponent]:
        line = self.get_line(line_id)
        if line is None:
            return None
        for c in line.cost_components:
            if c.component_id == component_id:
                return c
        return None

    def require_component(self, line_id: str, component_id: str) -> CostComponent:
        component = self.get_component(line_id, component_id)
        if component is None:
            raise RepositoryError(
                f"No such component_id {component_id!r} on line {line_id!r}"
            )
        return component

    def update_component(self, line_id: str, component_id: str, **changes: Any) -> CostComponent:
        component = self.require_component(line_id, component_id)
        for key, value in changes.items():
            setattr(component, key, value)
        return component

    def delete_component(self, line_id: str, component_id: str) -> None:
        line = self.require_line(line_id)
        self.require_component(line_id, component_id)
        line.cost_components = [
            c for c in line.cost_components if c.component_id != component_id
        ]
