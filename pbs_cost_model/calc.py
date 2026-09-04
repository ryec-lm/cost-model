"""Cost roll-up calculation.

Every line and cost_component resolves to either a numeric cost or
"unresolved" (missing required attributes, a bad reference, or a circular
reference) - calc never raises on incomplete data, it reports which lines
are incomplete so the tree can be built out incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import CostComponent, CostMethod, PBSLine, PBSTree, children_of


@dataclass
class LineResult:
    line_id: str
    method: Optional[str]
    has_children: bool
    resolved: bool
    cost: Optional[float]
    reason: Optional[str] = None
    # True when the line has BOTH a cost_method and children: its own
    # cost_method total is used and children are NOT rolled into it.
    leaf_equivalent_with_children: bool = False


@dataclass
class ComponentResult:
    component_id: str
    resolved: bool
    cost: Optional[float]
    reason: Optional[str] = None


class CostCalculator:
    """Stateful, single-use calculator over one snapshot of the tree."""

    def __init__(self, lines: PBSTree):
        self.lines = lines
        self._line_memo: Dict[str, LineResult] = {}
        self._line_visiting: set = set()
        self._component_memo: Dict[tuple, ComponentResult] = {}
        self._component_visiting: set = set()

    def calculate_all(self) -> Dict[str, LineResult]:
        for line_id in self.lines:
            self.calculate_line(line_id)
        return self._line_memo

    def calculate_line(self, line_id: str) -> LineResult:
        if line_id in self._line_memo:
            return self._line_memo[line_id]
        if line_id not in self.lines:
            result = LineResult(line_id, None, False, False, None, "line not found")
            self._line_memo[line_id] = result
            return result
        if line_id in self._line_visiting:
            result = LineResult(
                line_id,
                self.lines[line_id].cost_method,
                False,
                False,
                None,
                "circular reference detected",
            )
            self._line_memo[line_id] = result
            return result

        self._line_visiting.add(line_id)
        line = self.lines[line_id]
        child_ids = children_of(self.lines, line_id)
        has_kids = bool(child_ids)

        if line.cost_method is not None:
            cost, resolved, reason = self._own_cost(line)
            result = LineResult(
                line_id,
                line.cost_method,
                has_kids,
                resolved,
                cost,
                reason,
                leaf_equivalent_with_children=has_kids,
            )
        elif has_kids:
            total = 0.0
            ok = True
            problems: List[str] = []
            for cid in child_ids:
                child_result = self.calculate_line(cid)
                if not child_result.resolved:
                    ok = False
                    problems.append(cid)
                else:
                    total += child_result.cost or 0.0
            if ok:
                result = LineResult(line_id, None, True, True, total)
            else:
                reason = "unresolved children: " + ", ".join(problems)
                result = LineResult(line_id, None, True, False, None, reason)
        else:
            result = LineResult(
                line_id, None, False, False, None, "no cost_method and no children"
            )

        self._line_visiting.discard(line_id)
        self._line_memo[line_id] = result
        return result

    def _own_cost(self, line: PBSLine):
        method = line.cost_method
        if method == CostMethod.LUMP_SUM:
            if line.amount is None or line.lump_sum_basis is None:
                return None, False, "missing amount and/or lump_sum_basis"
            return line.amount, True, None

        if method == CostMethod.PARAMETRIC:
            if line.quantity is None or line.unit_rate is None:
                return None, False, "missing quantity and/or unit_rate"
            return line.quantity * line.unit_rate, True, None

        if method == CostMethod.PERCENTAGE:
            if line.basis_line_ref is None or line.percentage_rate is None:
                return None, False, "missing basis_line_ref and/or percentage_rate"
            if line.basis_line_ref not in self.lines:
                return None, False, f"basis_line_ref '{line.basis_line_ref}' not found"
            basis_result = self.calculate_line(line.basis_line_ref)
            if not basis_result.resolved:
                return None, False, self._propagate_unresolved_reason(
                    basis_result.reason, f"basis line '{line.basis_line_ref}'"
                )
            return basis_result.cost * line.percentage_rate, True, None

        if method == CostMethod.FIRST_PRINCIPLES:
            if not line.cost_components:
                return None, False, "first_principles line has no cost_components"
            total = 0.0
            problems = []
            for comp in line.cost_components:
                comp_result = self.calculate_component(line, comp)
                if not comp_result.resolved:
                    problems.append(f"{comp.component_id} ({comp_result.reason})")
                else:
                    total += comp_result.cost or 0.0
            if problems:
                return None, False, "incomplete components: " + "; ".join(problems)
            return total, True, None

        return None, False, f"unknown cost_method '{method}'"

    def calculate_component(self, line: PBSLine, comp: CostComponent) -> ComponentResult:
        key = (line.line_id, comp.component_id)
        if key in self._component_memo:
            return self._component_memo[key]
        if key in self._component_visiting:
            result = ComponentResult(
                comp.component_id, False, None, "circular reference detected"
            )
            self._component_memo[key] = result
            return result

        self._component_visiting.add(key)
        cost, resolved, reason = self._component_cost(line, comp)
        result = ComponentResult(comp.component_id, resolved, cost, reason)
        self._component_visiting.discard(key)
        self._component_memo[key] = result
        return result

    def _component_cost(self, line: PBSLine, comp: CostComponent):
        method = comp.cost_method
        if method == CostMethod.FIRST_PRINCIPLES:
            return None, False, "cost_method 'first_principles' is not allowed on a cost_component"

        if method == CostMethod.LUMP_SUM:
            if comp.amount is None or comp.lump_sum_basis is None:
                return None, False, "missing amount and/or lump_sum_basis"
            return comp.amount, True, None

        if method == CostMethod.PARAMETRIC:
            if comp.quantity is None or comp.unit_rate is None:
                return None, False, "missing quantity and/or unit_rate"
            return comp.quantity * comp.unit_rate, True, None

        if method == CostMethod.PERCENTAGE:
            if comp.ref_type is None or comp.basis_ref is None or comp.percentage_rate is None:
                return None, False, "missing ref_type/basis_ref/percentage_rate"
            if comp.ref_type == "line":
                if comp.basis_ref not in self.lines:
                    return None, False, f"basis line '{comp.basis_ref}' not found"
                basis_result = self.calculate_line(comp.basis_ref)
                if not basis_result.resolved:
                    return None, False, self._propagate_unresolved_reason(
                        basis_result.reason, f"basis line '{comp.basis_ref}'"
                    )
                return basis_result.cost * comp.percentage_rate, True, None
            elif comp.ref_type == "sibling_component":
                if comp.basis_ref == comp.component_id:
                    return None, False, "component references itself"
                sibling = next(
                    (c for c in line.cost_components if c.component_id == comp.basis_ref),
                    None,
                )
                if sibling is None:
                    return None, False, f"sibling component '{comp.basis_ref}' not found"
                sibling_result = self.calculate_component(line, sibling)
                if not sibling_result.resolved:
                    return None, False, self._propagate_unresolved_reason(
                        sibling_result.reason, f"sibling component '{comp.basis_ref}'"
                    )
                return sibling_result.cost * comp.percentage_rate, True, None
            else:
                return None, False, f"unknown ref_type '{comp.ref_type}'"

    @staticmethod
    def _propagate_unresolved_reason(upstream_reason: Optional[str], basis_label: str) -> str:
        if upstream_reason and "circular reference" in upstream_reason:
            return f"circular reference detected (via {basis_label})"
        return f"{basis_label} is unresolved"

        return None, False, f"unknown cost_method '{method}'"


def calculate_tree(lines: PBSTree) -> Dict[str, LineResult]:
    return CostCalculator(lines).calculate_all()
