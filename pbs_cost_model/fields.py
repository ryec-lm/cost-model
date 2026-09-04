"""Shared prompt/flag collection for method-specific attributes.

Used by both line and cost_component add/edit commands: if a value was
given as a flag, use it; otherwise prompt for it when running
interactively, or raise a clear usage error when running non-interactively
(scripted/batch entry).
"""

from __future__ import annotations

import click

from .models import LumpSumBasis, RefType


def collect_lump_sum(basis, amount, interactive: bool):
    if basis is None:
        if interactive:
            basis = click.prompt(
                "Lump sum basis", type=click.Choice([b.value for b in LumpSumBasis])
            )
        else:
            raise click.UsageError("--lump-sum-basis is required for cost_method lump_sum")
    if amount is None:
        if interactive:
            amount = click.prompt("Amount ($)", type=float)
        else:
            raise click.UsageError("--amount is required for cost_method lump_sum")
    return basis, amount


def collect_parametric(quantity, unit_of_measure, unit_rate, interactive: bool):
    if quantity is None:
        if interactive:
            quantity = click.prompt("Quantity", type=float)
        else:
            raise click.UsageError("--quantity is required for cost_method parametric")
    if not unit_of_measure:
        if interactive:
            unit_of_measure = click.prompt("Unit of measure (e.g. LF, SF, EA)")
        else:
            raise click.UsageError("--unit is required for cost_method parametric")
    if unit_rate is None:
        if interactive:
            unit_rate = click.prompt("Unit rate ($/unit)", type=float)
        else:
            raise click.UsageError("--unit-rate is required for cost_method parametric")
    return quantity, unit_of_measure, unit_rate


def collect_percentage_line(basis_line_ref, percentage_rate, interactive: bool):
    if not basis_line_ref:
        if interactive:
            basis_line_ref = click.prompt(
                "Basis line_id (must be a rollup line with children)"
            )
        else:
            raise click.UsageError("--basis-line is required for cost_method percentage")
    if percentage_rate is None:
        if interactive:
            percentage_rate = click.prompt(
                "Percentage rate (decimal, e.g. 0.05 for 5%)", type=float
            )
        else:
            raise click.UsageError("--rate is required for cost_method percentage")
    return basis_line_ref, percentage_rate


def collect_percentage_component(ref_type, basis_ref, percentage_rate, interactive: bool):
    if not ref_type:
        if interactive:
            ref_type = click.prompt(
                "Reference type", type=click.Choice([r.value for r in RefType])
            )
        else:
            raise click.UsageError("--ref-type is required for percentage components")
    if not basis_ref:
        if interactive:
            label = "line_id" if ref_type == RefType.LINE.value else "sibling component_id"
            basis_ref = click.prompt(f"Basis ref ({label})")
        else:
            raise click.UsageError("--basis-ref is required for percentage components")
    if percentage_rate is None:
        if interactive:
            percentage_rate = click.prompt("Percentage rate (decimal, e.g. 0.05)", type=float)
        else:
            raise click.UsageError("--rate is required for percentage components")
    return ref_type, basis_ref, percentage_rate
