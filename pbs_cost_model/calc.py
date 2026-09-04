"""Rollup cost calculation.

Walks the PBS tree bottom-up (with memoization) to compute, for every line:
  - its own direct cost (from its cost_method, if any)
  - its children's summed rolled-up cost
  - rolled_up_cost = own + children

`percentage` lines/components depend on another line's rolled_up_cost, which
may itself depend on further lines -- so this is really a dependency graph,
not a strict tree walk. We resolve it with recursive memoized computation and
explicit cycle detection (a `visiting` set of node keys threaded through the
recursion), rather than erroring, so a bad reference degrades to "unresolved"
for just the affected line(s) per validation rule 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, FrozenSet

from .models import PBSLine, CostComponent
from .repository import Repository

LUMP_SUM_REQUIRED = ("lump_sum_basis", "amount")
PARAMETRIC_REQUIRED = ("quantity", "unit_of_measure", "unit_rate")
PERCENTAGE_LINE_REQUIRED = ("basis_line_ref", "percentage_rate")
PERCENTAGE_COMPONENT_REQUIRED = ("basis_line_ref", "ref_type", "percentage_rate")


@dataclass
class ComponentResult:
    component_id: str
    resolved: bool
    calculated_cost: Optional[float] = None
    issues: List[str] = field(default_factory=list)


@dataclass
class LineResult:
    line_id: str
    resolved: bool
    own_cost: Optional[float] = None
    children_cost: Optional[float] = None
    rolled_up_cost: Optional[float] = None
    issues: List[str] = field(default_factory=list)
    component_results: Dict[str, ComponentResult] = field(default_factory=dict)


def _missing(obj, required) -> List[str]:
    return [attr for attr in required if getattr(obj, attr, None) is None]


class CostCalculator:
    """Stateful helper -- construct once per calculation pass over a Repository."""

    def __init__(self, repo: Repository):
        self.repo = repo
        self._line_cache: Dict[str, LineResult] = {}
        self._component_cache: Dict[str, ComponentResult] = {}

    # -- public API ---------------------------------------------------------

    def line_result(self, line_id: str) -> LineResult:
        return self._line_result(line_id, frozenset())

    def compute_all(self) -> Dict[str, LineResult]:
        for line_id in list(self.repo.tree.lines.keys()):
            self.line_result(line_id)
        return dict(self._line_cache)

    # -- internals ------------------------------------------------------

    @staticmethod
    def _lkey(line_id: str) -> str:
        return f"L:{line_id}"

    @staticmethod
    def _ckey(line_id: str, component_id: str) -> str:
        return f"C:{line_id}:{component_id}"

    def _line_result(self, line_id: str, visiting: FrozenSet[str]) -> LineResult:
        if line_id in self._line_cache:
            return self._line_cache[line_id]

        key = self._lkey(line_id)
        if key in visiting:
            return LineResult(
                line_id=line_id,
                resolved=False,
                issues=[f"circular reference detected at line {line_id}"],
            )

        line = self.repo.get_line(line_id)
        if line is None:
            return LineResult(
                line_id=line_id, resolved=False, issues=[f"line {line_id} not found"]
            )

        visiting2 = visiting | {key}
        issues: List[str] = []
        own_cost: Optional[float] = None
        own_resolved = True
        component_results: Dict[str, ComponentResult] = {}

        if line.cost_method is None:
            own_cost = 0.0

        elif line.cost_method == "lump_sum":
            missing = _missing(line, LUMP_SUM_REQUIRED)
            if missing:
                own_resolved = False
                issues.append(f"lump_sum missing: {', '.join(missing)}")
            else:
                own_cost = float(line.amount)

        elif line.cost_method == "parametric":
            missing = _missing(line, PARAMETRIC_REQUIRED)
            if missing:
                own_resolved = False
                issues.append(f"parametric missing: {', '.join(missing)}")
            else:
                own_cost = float(line.quantity) * float(line.unit_rate)

        elif line.cost_method == "percentage":
            missing = _missing(line, PERCENTAGE_LINE_REQUIRED)
            if missing:
                own_resolved = False
                issues.append(f"percentage missing: {', '.join(missing)}")
            else:
                own_cost, pct_issue = self._resolve_percentage_basis(
                    line.basis_line_ref, line.percentage_rate, visiting2
                )
                if own_cost is None:
                    own_resolved = False
                    issues.append(pct_issue)

        elif line.cost_method == "first_principles":
            if not line.cost_components:
                own_resolved = False
                issues.append("first_principles line has no cost_components")
            else:
                total = 0.0
                all_resolved = True
                for comp in line.cost_components:
                    cres = self._component_result(line, comp, visiting2)
                    component_results[comp.component_id] = cres
                    if not cres.resolved:
                        all_resolved = False
                        issues.append(
                            f"component {comp.component_id} unresolved: "
                            + "; ".join(cres.issues)
                        )
                    else:
                        total += cres.calculated_cost  # type: ignore[operator]
                if all_resolved:
                    own_cost = total
                else:
                    own_resolved = False

        else:
            own_resolved = False
            issues.append(f"unknown cost_method: {line.cost_method!r}")

        children = self.repo.children_of(line_id)
        children_resolved = True
        children_cost: Optional[float] = 0.0
        if children:
            total = 0.0
            for child in children:
                cres = self._line_result(child.line_id, visiting2)
                if not cres.resolved:
                    children_resolved = False
                    issues.append(f"child {child.line_id} unresolved")
                else:
                    total += cres.rolled_up_cost  # type: ignore[operator]
            children_cost = total if children_resolved else None

        resolved = own_resolved and children_resolved
        rolled_up_cost = None
        if resolved:
            rolled_up_cost = (own_cost or 0.0) + (children_cost or 0.0)

        result = LineResult(
            line_id=line_id,
            resolved=resolved,
            own_cost=own_cost if own_resolved else None,
            children_cost=children_cost,
            rolled_up_cost=rolled_up_cost,
            issues=issues,
            component_results=component_results,
        )
        self._line_cache[line_id] = result
        return result

    def _resolve_percentage_basis(self, basis_line_ref, percentage_rate, visiting):
        """Shared by line- and component-level percentage resolution against
        a PBS line basis. Returns (calculated_cost_or_None, issue_or_None)."""
        basis_line = self.repo.get_line(basis_line_ref)
        if basis_line is None:
            return None, f"basis_line_ref {basis_line_ref!r} does not exist"
        if not self.repo.has_children(basis_line_ref):
            return (
                None,
                f"basis_line_ref {basis_line_ref!r} is not a rollup line (has no children)",
            )
        basis_result = self._line_result(basis_line_ref, visiting)
        if not basis_result.resolved:
            reason = "; ".join(basis_result.issues) or "unresolved"
            return None, f"basis line {basis_line_ref} is unresolved ({reason})"
        return basis_result.rolled_up_cost * float(percentage_rate), None

    def _component_result(
        self, parent_line: PBSLine, comp: CostComponent, visiting: FrozenSet[str]
    ) -> ComponentResult:
        key = self._ckey(parent_line.line_id, comp.component_id)
        if key in self._component_cache:
            return self._component_cache[key]
        if key in visiting:
            return ComponentResult(
                component_id=comp.component_id,
                resolved=False,
                issues=[f"circular reference detected at component {comp.component_id}"],
            )

        visiting2 = visiting | {key}

        if comp.cost_method == "first_principles":
            result = ComponentResult(
                component_id=comp.component_id,
                resolved=False,
                issues=["first_principles is not a valid cost_component method"],
            )
        elif comp.cost_method == "lump_sum":
            missing = _missing(comp, LUMP_SUM_REQUIRED)
            if missing:
                result = ComponentResult(
                    comp.component_id, False, issues=[f"lump_sum missing: {', '.join(missing)}"]
                )
            else:
                result = ComponentResult(comp.component_id, True, float(comp.amount))
        elif comp.cost_method == "parametric":
            missing = _missing(comp, PARAMETRIC_REQUIRED)
            if missing:
                result = ComponentResult(
                    comp.component_id, False, issues=[f"parametric missing: {', '.join(missing)}"]
                )
            else:
                result = ComponentResult(
                    comp.component_id, True, float(comp.quantity) * float(comp.unit_rate)
                )
        elif comp.cost_method == "percentage":
            missing = _missing(comp, PERCENTAGE_COMPONENT_REQUIRED)
            if missing:
                result = ComponentResult(
                    comp.component_id,
                    False,
                    issues=[f"percentage missing: {', '.join(missing)}"],
                )
            elif comp.ref_type == "line":
                cost, issue = self._resolve_percentage_basis(
                    comp.basis_line_ref, comp.percentage_rate, visiting2
                )
                if cost is None:
                    result = ComponentResult(comp.component_id, False, issues=[issue])
                else:
                    result = ComponentResult(comp.component_id, True, cost)
            elif comp.ref_type == "sibling_component":
                sibling = next(
                    (
                        c
                        for c in parent_line.cost_components
                        if c.component_id == comp.basis_line_ref
                    ),
                    None,
                )
                if sibling is None:
                    result = ComponentResult(
                        comp.component_id,
                        False,
                        issues=[f"sibling_component {comp.basis_line_ref!r} not found"],
                    )
                else:
                    sres = self._component_result(parent_line, sibling, visiting2)
                    if not sres.resolved:
                        reason = "; ".join(sres.issues) or "unresolved"
                        result = ComponentResult(
                            comp.component_id,
                            False,
                            issues=[
                                f"sibling component {sibling.component_id} is unresolved ({reason})"
                            ],
                        )
                    else:
                        result = ComponentResult(
                            comp.component_id,
                            True,
                            sres.calculated_cost * float(comp.percentage_rate),  # type: ignore[operator]
                        )
            else:
                result = ComponentResult(
                    comp.component_id,
                    False,
                    issues=[f"invalid ref_type: {comp.ref_type!r}"],
                )
        else:
            result = ComponentResult(
                comp.component_id, False, issues=[f"unknown cost_method: {comp.cost_method!r}"]
            )

        self._component_cache[key] = result
        return result


def compute(repo: Repository) -> Dict[str, LineResult]:
    """Convenience one-shot: compute rollup results for every line."""
    return CostCalculator(repo).compute_all()
