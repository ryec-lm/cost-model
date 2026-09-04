"""Full validation sweep over the tree.

Distinguishes hard errors (bad references, cycles, structurally invalid
data) from warnings (ambiguous but allowed combinations) and incomplete
lines (missing attributes for the declared method - not an error, just not
ready to be included in a calc total yet).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .calc import calculate_tree
from .models import CostMethod, PBSTree, ancestors_of, descendants_of, has_children


@dataclass
class ValidationReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    incomplete: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_tree(lines: PBSTree) -> ValidationReport:
    report = ValidationReport()

    _check_uniqueness(lines, report)
    _check_percentage_refs(lines, report)
    _check_component_methods(lines, report)
    _check_leaf_with_children(lines, report)

    results = calculate_tree(lines)
    for line_id, result in results.items():
        if not result.resolved:
            if result.reason and "circular reference" in result.reason:
                report.add_error(f"{line_id}: {result.reason}")
            else:
                report.incomplete.append(f"{line_id}: {result.reason}")

    return report


def _check_uniqueness(lines: PBSTree, report: ValidationReport) -> None:
    for line_id, line in lines.items():
        if line.line_id != line_id:
            report.add_error(
                f"{line_id}: stored key does not match line_id '{line.line_id}'"
            )
        if line.parent_line_id is not None and line.parent_line_id not in lines:
            report.add_error(
                f"{line_id}: parent_line_id '{line.parent_line_id}' does not exist"
            )
        seen = set()
        for comp in line.cost_components:
            if comp.component_id in seen:
                report.add_error(
                    f"{line_id}: duplicate component_id '{comp.component_id}'"
                )
            seen.add(comp.component_id)


def _check_percentage_refs(lines: PBSTree, report: ValidationReport) -> None:
    for line_id, line in lines.items():
        if line.cost_method == CostMethod.PERCENTAGE and line.basis_line_ref is not None:
            _validate_line_basis_ref(lines, line_id, line.basis_line_ref, report)

        for comp in line.cost_components:
            if comp.cost_method != CostMethod.PERCENTAGE or comp.ref_type is None:
                continue
            if comp.ref_type == "line" and comp.basis_ref is not None:
                _validate_line_basis_ref(
                    lines,
                    f"{line_id}/{comp.component_id}",
                    comp.basis_ref,
                    report,
                )
            elif comp.ref_type == "sibling_component" and comp.basis_ref is not None:
                if comp.basis_ref == comp.component_id:
                    report.add_error(
                        f"{line_id}/{comp.component_id}: basis_ref references itself"
                    )
                elif not any(
                    c.component_id == comp.basis_ref for c in line.cost_components
                ):
                    report.add_error(
                        f"{line_id}/{comp.component_id}: sibling component "
                        f"'{comp.basis_ref}' not found"
                    )
            elif comp.ref_type not in ("line", "sibling_component"):
                report.add_error(
                    f"{line_id}/{comp.component_id}: invalid ref_type '{comp.ref_type}'"
                )


def _validate_line_basis_ref(
    lines: PBSTree, source_label: str, basis_line_ref: str, report: ValidationReport
) -> None:
    if basis_line_ref not in lines:
        report.add_error(f"{source_label}: basis_line_ref '{basis_line_ref}' not found")
        return

    line_id = source_label.split("/")[0]
    if basis_line_ref == line_id:
        report.add_error(f"{source_label}: basis_line_ref references itself")
        return
    if line_id in lines:
        if basis_line_ref in ancestors_of(lines, line_id):
            report.add_error(
                f"{source_label}: basis_line_ref '{basis_line_ref}' is an ancestor "
                "(circular reference)"
            )
            return
        if basis_line_ref in descendants_of(lines, line_id):
            report.add_error(
                f"{source_label}: basis_line_ref '{basis_line_ref}' is a descendant "
                "(circular reference)"
            )
            return

    if not has_children(lines, basis_line_ref):
        report.add_error(
            f"{source_label}: basis_line_ref '{basis_line_ref}' is a leaf line "
            "(percentage basis must be a rollup with children)"
        )


def _check_component_methods(lines: PBSTree, report: ValidationReport) -> None:
    for line_id, line in lines.items():
        for comp in line.cost_components:
            if comp.cost_method == CostMethod.FIRST_PRINCIPLES:
                report.add_error(
                    f"{line_id}/{comp.component_id}: cost_method 'first_principles' "
                    "is not allowed on a cost_component"
                )


def _check_leaf_with_children(lines: PBSTree, report: ValidationReport) -> None:
    for line_id, line in lines.items():
        if line.cost_method is not None and has_children(lines, line_id):
            report.add_warning(
                f"{line_id}: has both cost_method '{line.cost_method}' and children - "
                "treated as a leaf-equivalent cost line, children are NOT rolled into it"
            )
