"""Domain data model for the PBS cost model.

Plain dataclasses only -- no ORM, no premature abstraction. This module owns
the *shape* of the data; persistence lives in storage.py and business rules
(rollup calculation, validation) live in calc.py / validation.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


# --------------------------------------------------------------------------
# Enums (plain string constants -- kept as simple tuples so callers can do
# `value in LUMP_SUM_BASIS_VALUES` without importing an Enum class).
# --------------------------------------------------------------------------

COST_METHODS = ("lump_sum", "parametric", "percentage", "first_principles")
# cost_components may use any cost_method except first_principles (no
# recursive nesting -- see validation rule 3).
COMPONENT_COST_METHODS = ("lump_sum", "parametric", "percentage")

LUMP_SUM_BASES = ("quote", "historical", "analogous", "allowance")

COST_TYPES = ("labor", "material", "equipment", "shipping", "subcontract")

REF_TYPES = ("line", "sibling_component")

# lump_sum_basis -> derived confidence label. Confidence is NEVER stored,
# only looked up at display/report time.
CONFIDENCE_BY_LUMP_SUM_BASIS = {
    "quote": "High",
    "historical": "Medium-High",
    "analogous": "Medium",
    "allowance": "Low",
}


def confidence_for_basis(basis: Optional[str]) -> Optional[str]:
    if basis is None:
        return None
    return CONFIDENCE_BY_LUMP_SUM_BASIS.get(basis)


# --------------------------------------------------------------------------
# Cost component (nested only under first_principles PBS lines)
# --------------------------------------------------------------------------


@dataclass
class CostComponent:
    component_id: str
    cost_type: str
    cost_method: str  # one of COMPONENT_COST_METHODS

    # lump_sum attributes
    lump_sum_basis: Optional[str] = None
    amount: Optional[float] = None

    # parametric attributes
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    unit_rate: Optional[float] = None

    # percentage attributes
    basis_line_ref: Optional[str] = None
    ref_type: Optional[str] = None  # "line" | "sibling_component"
    percentage_rate: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "CostComponent":
        return CostComponent(**data)


# --------------------------------------------------------------------------
# PBS line (node in the tree)
# --------------------------------------------------------------------------


@dataclass
class PBSLine:
    line_id: str
    line_name: str
    parent_line_id: Optional[str] = None
    cost_method: Optional[str] = None  # one of COST_METHODS, or None

    # lump_sum attributes
    lump_sum_basis: Optional[str] = None
    amount: Optional[float] = None

    # parametric attributes
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    unit_rate: Optional[float] = None

    # percentage attributes
    basis_line_ref: Optional[str] = None
    percentage_rate: Optional[float] = None

    # first_principles attributes
    cost_components: List[CostComponent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cost_components"] = [c.to_dict() for c in self.cost_components]
        return d

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PBSLine":
        data = dict(data)
        components = data.pop("cost_components", []) or []
        line = PBSLine(**data)
        line.cost_components = [CostComponent.from_dict(c) for c in components]
        return line


# --------------------------------------------------------------------------
# Tree container -- the whole persisted document.
# --------------------------------------------------------------------------


@dataclass
class PBSTree:
    schema_version: int = 1
    next_line_seq: int = 1
    lines: Dict[str, PBSLine] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "next_line_seq": self.next_line_seq,
            "lines": {lid: line.to_dict() for lid, line in self.lines.items()},
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PBSTree":
        lines = {
            lid: PBSLine.from_dict(ldata)
            for lid, ldata in (data.get("lines") or {}).items()
        }
        return PBSTree(
            schema_version=data.get("schema_version", 1),
            next_line_seq=data.get("next_line_seq", 1),
            lines=lines,
        )

    # -- convenience helpers -------------------------------------------

    def children_of(self, line_id: Optional[str]) -> List[PBSLine]:
        return [l for l in self.lines.values() if l.parent_line_id == line_id]

    def roots(self) -> List[PBSLine]:
        return self.children_of(None)

    def has_children(self, line_id: str) -> bool:
        return any(l.parent_line_id == line_id for l in self.lines.values())
