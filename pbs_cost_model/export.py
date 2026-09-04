"""Flatten the tree to CSV: one row per line, one row per cost_component."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Union

from .calc import CostCalculator
from .models import PBSTree, confidence_for_basis

FIELDNAMES = [
    "row_type",
    "line_id",
    "parent_line_id",
    "line_name",
    "component_id",
    "cost_type",
    "cost_method",
    "lump_sum_basis",
    "confidence",
    "amount",
    "quantity",
    "unit_of_measure",
    "unit_rate",
    "basis_line_ref",
    "ref_type",
    "basis_ref",
    "percentage_rate",
    "resolved",
    "cost",
    "reason",
]


def export_csv(lines: PBSTree, path: Union[str, Path]) -> None:
    calculator = CostCalculator(lines)
    results = calculator.calculate_all()
    rows = []

    for line_id, line in lines.items():
        result = results.get(line_id)
        rows.append(
            {
                "row_type": "line",
                "line_id": line.line_id,
                "parent_line_id": line.parent_line_id or "",
                "line_name": line.line_name,
                "cost_method": line.cost_method or "",
                "lump_sum_basis": line.lump_sum_basis or "",
                "confidence": confidence_for_basis(line.lump_sum_basis) or "",
                "amount": line.amount if line.amount is not None else "",
                "quantity": line.quantity if line.quantity is not None else "",
                "unit_of_measure": line.unit_of_measure or "",
                "unit_rate": line.unit_rate if line.unit_rate is not None else "",
                "basis_line_ref": line.basis_line_ref or "",
                "resolved": result.resolved if result else "",
                "cost": result.cost if result and result.cost is not None else "",
                "reason": result.reason or "" if result else "",
            }
        )
        for comp in line.cost_components:
            comp_result = calculator.calculate_component(line, comp)
            rows.append(
                {
                    "row_type": "component",
                    "line_id": line.line_id,
                    "parent_line_id": line.parent_line_id or "",
                    "line_name": line.line_name,
                    "component_id": comp.component_id,
                    "cost_type": comp.cost_type,
                    "cost_method": comp.cost_method,
                    "lump_sum_basis": comp.lump_sum_basis or "",
                    "confidence": confidence_for_basis(comp.lump_sum_basis) or "",
                    "amount": comp.amount if comp.amount is not None else "",
                    "quantity": comp.quantity if comp.quantity is not None else "",
                    "unit_of_measure": comp.unit_of_measure or "",
                    "unit_rate": comp.unit_rate if comp.unit_rate is not None else "",
                    "ref_type": comp.ref_type or "",
                    "basis_ref": comp.basis_ref or "",
                    "percentage_rate": comp.percentage_rate
                    if comp.percentage_rate is not None
                    else "",
                    "resolved": comp_result.resolved,
                    "cost": comp_result.cost if comp_result.cost is not None else "",
                    "reason": comp_result.reason or "",
                }
            )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})
