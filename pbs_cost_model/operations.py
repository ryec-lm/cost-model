"""Tree-mutation rules shared by the CLI and TUI front ends.

Each front end collects input in its own idiom (flags/prompts for the CLI,
form widgets for the TUI) and reports errors in its own idiom, but the
underlying invariants - no cycles, no dangling reassignment targets - must
stay identical. Both front ends call into this module rather than
re-implementing the checks.
"""

from __future__ import annotations

from typing import Optional

from .models import PBSTree, children_of, descendants_of


class OperationError(Exception):
    """A mutation that would violate a tree invariant."""


def validate_new_parent(lines: PBSTree, line_id: str, new_parent: Optional[str]) -> None:
    if new_parent is None:
        return
    if new_parent == line_id:
        raise OperationError("a line cannot be its own parent")
    if new_parent not in lines:
        raise OperationError(f"parent '{new_parent}' not found")
    if new_parent in descendants_of(lines, line_id):
        raise OperationError(
            f"cannot set parent to '{new_parent}': it is a descendant of "
            f"'{line_id}' (would create a cycle)"
        )


def cascade_delete_line(lines: PBSTree, line_id: str) -> None:
    for lid in [line_id] + descendants_of(lines, line_id):
        lines.pop(lid, None)


def reassign_children_and_delete_line(
    lines: PBSTree, line_id: str, new_parent: Optional[str]
) -> None:
    if new_parent is not None:
        if new_parent not in lines:
            raise OperationError(f"reassign-to target '{new_parent}' not found")
        if new_parent == line_id:
            raise OperationError("cannot reassign children to the line being removed")
    for kid in children_of(lines, line_id):
        lines[kid].parent_line_id = new_parent
    lines.pop(line_id, None)
