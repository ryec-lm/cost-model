"""WBS numbering: 1, 1.1, 1.2, 1.2.1, 2, ... derived from tree position.

Auto-computed from each line's depth and position among its siblings
(models.children_of / models.root_lines, ordered by sort_index) - not
stored, since it must stay in sync as lines are added, removed, or
reordered. A line's displayed number is its wbs_override if one is set,
else its computed number.
"""

from __future__ import annotations

from typing import Dict, Optional

from .models import CostComponent, PBSLine, PBSTree, children_of, root_lines


def compute_wbs_numbers(lines: PBSTree) -> Dict[str, str]:
    numbers: Dict[str, str] = {}

    def walk(line_id: str, number: str) -> None:
        numbers[line_id] = number
        for i, child_id in enumerate(children_of(lines, line_id), start=1):
            walk(child_id, f"{number}.{i}")

    for i, root_id in enumerate(root_lines(lines), start=1):
        walk(root_id, str(i))

    return numbers


def display_wbs(line: PBSLine, computed: Dict[str, str]) -> str:
    return line.wbs_override or computed.get(line.line_id, "")


def display_component_wbs(line: PBSLine, component: CostComponent, computed: Dict[str, str]) -> str:
    """A component's number extends its parent line's displayed WBS with its
    1-based position among the line's cost_components - e.g. line "1.1"'s
    second component is "1.1.2". Components have no wbs_override of their
    own: they aren't independently reorderable, so their position is always
    just their index in cost_components."""
    index = line.cost_components.index(component) + 1
    return f"{display_wbs(line, computed)}.{index}"
