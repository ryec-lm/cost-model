"""Data model for the PBS cost tree.

Plain dataclasses, no ORM. A line's `cost_method` is one of the CostMethod
values or None (pure rollup / organizational node). A line with a
cost_method is treated as a leaf-equivalent cost line even if it has
children: its own cost_method total is used, children are NOT rolled into
it (this ambiguous combination is flagged, not silently resolved - see
calc.py / validation.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional


class CostMethod(str, Enum):
    LUMP_SUM = "lump_sum"
    PARAMETRIC = "parametric"
    PERCENTAGE = "percentage"
    FIRST_PRINCIPLES = "first_principles"


COMPONENT_COST_METHODS = (
    CostMethod.LUMP_SUM,
    CostMethod.PARAMETRIC,
    CostMethod.PERCENTAGE,
)


class LumpSumBasis(str, Enum):
    QUOTE = "quote"
    HISTORICAL = "historical"
    ANALOGOUS = "analogous"
    ALLOWANCE = "allowance"


CONFIDENCE_BY_BASIS = {
    LumpSumBasis.QUOTE: "High",
    LumpSumBasis.HISTORICAL: "Medium-High",
    LumpSumBasis.ANALOGOUS: "Medium",
    LumpSumBasis.ALLOWANCE: "Low",
}


class CostType(str, Enum):
    LABOR = "labor"
    MATERIAL = "material"
    EQUIPMENT = "equipment"
    SHIPPING = "shipping"
    SUBCONTRACT = "subcontract"


class RefType(str, Enum):
    LINE = "line"
    SIBLING_COMPONENT = "sibling_component"


def confidence_for_basis(basis: Optional[str]) -> Optional[str]:
    if basis is None:
        return None
    return CONFIDENCE_BY_BASIS.get(LumpSumBasis(basis))


@dataclass
class CostComponent:
    component_id: str
    cost_type: str
    cost_method: str

    # lump_sum
    lump_sum_basis: Optional[str] = None
    amount: Optional[float] = None

    # parametric
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    unit_rate: Optional[float] = None

    # percentage
    ref_type: Optional[str] = None  # "line" or "sibling_component"
    basis_ref: Optional[str] = None  # line_id or component_id, per ref_type
    percentage_rate: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "CostComponent":
        return CostComponent(**data)


@dataclass
class PBSLine:
    line_id: str
    line_name: str
    parent_line_id: Optional[str] = None
    cost_method: Optional[str] = None

    # Position among siblings (lower sorts first) - controls both display
    # order and the auto-computed WBS number. Assigned at creation time via
    # storage.next_sort_index() and changed only by operations.move_line().
    sort_index: int = 0

    # WBS number is auto-computed from tree position by default (see wbs.py);
    # setting this pins an explicit value instead. Blank/None means "auto".
    wbs_override: Optional[str] = None

    # lump_sum
    lump_sum_basis: Optional[str] = None
    amount: Optional[float] = None

    # parametric
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    unit_rate: Optional[float] = None

    # percentage
    basis_line_ref: Optional[str] = None
    percentage_rate: Optional[float] = None

    # first_principles
    cost_components: List[CostComponent] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cost_components"] = [c.to_dict() for c in self.cost_components]
        return d

    @staticmethod
    def from_dict(data: dict) -> "PBSLine":
        data = dict(data)
        components = [CostComponent.from_dict(c) for c in data.pop("cost_components", [])]
        return PBSLine(cost_components=components, **data)


PBSTree = Dict[str, PBSLine]


def children_map(lines: PBSTree) -> Dict[Optional[str], List[str]]:
    result: Dict[Optional[str], List[str]] = {}
    for line in lines.values():
        result.setdefault(line.parent_line_id, []).append(line.line_id)
    return result


def children_of(lines: PBSTree, line_id: str) -> List[str]:
    kids = [l for l in lines.values() if l.parent_line_id == line_id]
    kids.sort(key=lambda l: l.sort_index)
    return [l.line_id for l in kids]


def has_children(lines: PBSTree, line_id: str) -> bool:
    return any(l.parent_line_id == line_id for l in lines.values())


def ancestors_of(lines: PBSTree, line_id: str) -> List[str]:
    result = []
    seen = set()
    current = lines.get(line_id)
    while current is not None and current.parent_line_id is not None:
        parent_id = current.parent_line_id
        if parent_id in seen or parent_id not in lines:
            break
        result.append(parent_id)
        seen.add(parent_id)
        current = lines[parent_id]
    return result


def descendants_of(lines: PBSTree, line_id: str) -> List[str]:
    result = []
    stack = children_of(lines, line_id)
    while stack:
        cid = stack.pop()
        result.append(cid)
        stack.extend(children_of(lines, cid))
    return result


def root_lines(lines: PBSTree) -> List[str]:
    roots = [l for l in lines.values() if l.parent_line_id is None]
    roots.sort(key=lambda l: l.sort_index)
    return [l.line_id for l in roots]
