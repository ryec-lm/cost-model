"""Command-line interface for building and reporting on a PBS cost tree."""

from __future__ import annotations

import sys
from typing import Optional

import click

from . import fields
from .calc import CostCalculator, LineResult
from .export import export_csv
from .models import (
    CostComponent,
    CostMethod,
    CostType,
    PBSLine,
    RefType,
    children_of,
    confidence_for_basis,
    root_lines,
)
from .operations import (
    OperationError,
    cascade_delete_line,
    move_line,
    reassign_children_and_delete_line,
    validate_new_parent,
)
from .storage import JSONRepository, next_component_id, next_line_id, next_sort_index
from .validation import validate_tree
from .wbs import compute_wbs_numbers, display_wbs

METHOD_CHOICES = [m.value for m in CostMethod] + ["none"]
COMPONENT_METHOD_CHOICES = [m.value for m in CostMethod if m != CostMethod.FIRST_PRINCIPLES]


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _repo(ctx) -> JSONRepository:
    return JSONRepository(ctx.obj["file"])


def _load(ctx):
    return _repo(ctx).load()


def _save(ctx, lines) -> None:
    _repo(ctx).save(lines)


def _require_line(lines, line_id: str) -> PBSLine:
    line = lines.get(line_id)
    if line is None:
        raise click.UsageError(f"line '{line_id}' not found")
    return line


def _require_component(line: PBSLine, component_id: str) -> CostComponent:
    for c in line.cost_components:
        if c.component_id == component_id:
            return c
    raise click.UsageError(f"component '{component_id}' not found on line '{line.line_id}'")


@click.group()
@click.option(
    "--file",
    "-f",
    "file_",
    default="pbs_tree.json",
    show_default=True,
    help="Path to the JSON file storing the PBS tree.",
)
@click.pass_context
def main(ctx, file_):
    """Build and report on a first-principles PBS cost model."""
    ctx.ensure_object(dict)
    ctx.obj["file"] = file_


# --------------------------------------------------------------------------
# add-line / edit-line
# --------------------------------------------------------------------------


def _apply_method_attrs(line: PBSLine, method: Optional[str], opts: dict, interactive: bool) -> None:
    if method == CostMethod.LUMP_SUM.value:
        basis, amount = fields.collect_lump_sum(
            opts.get("lump_sum_basis"), opts.get("amount"), interactive
        )
        line.lump_sum_basis, line.amount = basis, amount
    elif method == CostMethod.PARAMETRIC.value:
        qty, unit, rate = fields.collect_parametric(
            opts.get("quantity"), opts.get("unit_of_measure"), opts.get("unit_rate"), interactive
        )
        line.quantity, line.unit_of_measure, line.unit_rate = qty, unit, rate
    elif method == CostMethod.PERCENTAGE.value:
        basis_ref, rate = fields.collect_percentage_line(
            opts.get("basis_line_ref"), opts.get("percentage_rate"), interactive
        )
        line.basis_line_ref, line.percentage_rate = basis_ref, rate
    elif method == CostMethod.FIRST_PRINCIPLES.value:
        pass  # components are added separately via add-component


@main.command("add-line")
@click.option("--name", "-n", help="Line description / scope.")
@click.option("--parent", "-p", "parent_line_id", help="Parent line_id (omit for a root line).")
@click.option("--cost-method", "-m", type=click.Choice(METHOD_CHOICES))
@click.option("--lump-sum-basis", type=click.Choice(["quote", "historical", "analogous", "allowance"]))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit_of_measure")
@click.option("--unit-rate", type=float)
@click.option("--basis-line", "basis_line_ref", help="line_id this percentage is based on.")
@click.option("--rate", "percentage_rate", type=float, help="Percentage rate as a decimal (0.05 = 5%).")
@click.option("--wbs", "wbs_override", help="Pin an explicit WBS number instead of auto-numbering.")
@click.pass_context
def add_line(ctx, name, parent_line_id, cost_method, wbs_override, **method_opts):
    """Add a new PBS line."""
    lines = _load(ctx)
    interactive = _is_interactive()

    if not name:
        if interactive:
            name = click.prompt("Line name")
        else:
            raise click.UsageError("--name is required")

    if parent_line_id and parent_line_id not in lines:
        raise click.UsageError(f"parent line '{parent_line_id}' not found")

    if cost_method is None:
        if interactive:
            cost_method = click.prompt(
                "Cost method", type=click.Choice(METHOD_CHOICES), default="none"
            )
        else:
            cost_method = "none"
    cost_method = None if cost_method == "none" else cost_method

    line_id = next_line_id(lines)
    sort_index = next_sort_index(lines, parent_line_id)
    line = PBSLine(
        line_id=line_id,
        line_name=name,
        parent_line_id=parent_line_id,
        cost_method=cost_method,
        sort_index=sort_index,
        wbs_override=wbs_override,
    )
    _apply_method_attrs(line, cost_method, method_opts, interactive)

    lines[line_id] = line
    _save(ctx, lines)
    wbs = display_wbs(line, compute_wbs_numbers(lines))
    click.echo(f"Added line {line_id} (WBS {wbs}): {name}")


@main.command("edit-line")
@click.argument("line_id")
@click.option("--name", "-n")
@click.option("--parent", "-p", "parent_line_id", help="New parent line_id, or '' for a root line.")
@click.option("--cost-method", "-m", type=click.Choice(METHOD_CHOICES))
@click.option("--clear-cost-method", is_flag=True, help="Unset cost_method (make this a pure rollup).")
@click.option("--lump-sum-basis", type=click.Choice(["quote", "historical", "analogous", "allowance"]))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit_of_measure")
@click.option("--unit-rate", type=float)
@click.option("--basis-line", "basis_line_ref")
@click.option("--rate", "percentage_rate", type=float)
@click.option("--wbs", "wbs_override", help="Pin an explicit WBS number instead of auto-numbering.")
@click.option("--clear-wbs", is_flag=True, help="Go back to auto-numbering for this line's WBS.")
@click.pass_context
def edit_line(ctx, line_id, name, parent_line_id, cost_method, clear_cost_method, wbs_override, clear_wbs, **method_opts):
    """Modify an existing line's attributes."""
    lines = _load(ctx)
    line = _require_line(lines, line_id)

    any_flag = (
        clear_cost_method
        or clear_wbs
        or any(v is not None for v in [name, parent_line_id, cost_method, wbs_override, *method_opts.values()])
    )

    if not any_flag:
        if not _is_interactive():
            raise click.UsageError("no fields provided to update")
        _interactive_edit_line(lines, line)
    else:
        if name is not None:
            line.line_name = name
        if parent_line_id is not None:
            new_parent = parent_line_id or None
            _validate_new_parent(lines, line_id, new_parent)
            line.parent_line_id = new_parent
            line.sort_index = next_sort_index(
                {k: v for k, v in lines.items() if k != line_id}, new_parent
            )
        if clear_wbs:
            line.wbs_override = None
        elif wbs_override is not None:
            line.wbs_override = wbs_override
        if clear_cost_method:
            line.cost_method = None
        elif cost_method is not None:
            line.cost_method = None if cost_method == "none" else cost_method
        if method_opts.get("lump_sum_basis") is not None:
            line.lump_sum_basis = method_opts["lump_sum_basis"]
        if method_opts.get("amount") is not None:
            line.amount = method_opts["amount"]
        if method_opts.get("quantity") is not None:
            line.quantity = method_opts["quantity"]
        if method_opts.get("unit_of_measure") is not None:
            line.unit_of_measure = method_opts["unit_of_measure"]
        if method_opts.get("unit_rate") is not None:
            line.unit_rate = method_opts["unit_rate"]
        if method_opts.get("basis_line_ref") is not None:
            line.basis_line_ref = method_opts["basis_line_ref"]
        if method_opts.get("percentage_rate") is not None:
            line.percentage_rate = method_opts["percentage_rate"]

    _save(ctx, lines)
    wbs = display_wbs(line, compute_wbs_numbers(lines))
    click.echo(f"Updated line {line_id} (WBS {wbs})")


def _validate_new_parent(lines, line_id: str, new_parent: Optional[str]) -> None:
    try:
        validate_new_parent(lines, line_id, new_parent)
    except OperationError as e:
        raise click.UsageError(str(e))


def _interactive_edit_line(lines, line: PBSLine) -> None:
    line.line_name = click.prompt("Line name", default=line.line_name)
    new_parent = click.prompt(
        "Parent line_id (blank for root)", default=line.parent_line_id or "", show_default=False
    )
    new_parent = new_parent or None
    if new_parent != line.parent_line_id:
        _validate_new_parent(lines, line.line_id, new_parent)
        line.parent_line_id = new_parent
        line.sort_index = next_sort_index(
            {k: v for k, v in lines.items() if k != line.line_id}, new_parent
        )

    wbs_override = click.prompt(
        "WBS override (blank for auto-numbering)", default=line.wbs_override or "", show_default=False
    )
    line.wbs_override = wbs_override or None

    method = click.prompt(
        "Cost method",
        type=click.Choice(METHOD_CHOICES),
        default=line.cost_method or "none",
    )
    line.cost_method = None if method == "none" else method

    if line.cost_method == CostMethod.LUMP_SUM.value:
        basis_default = line.lump_sum_basis or "quote"
        line.lump_sum_basis = click.prompt(
            "Lump sum basis",
            type=click.Choice(["quote", "historical", "analogous", "allowance"]),
            default=basis_default,
        )
        line.amount = click.prompt("Amount ($)", type=float, default=line.amount or 0.0)
    elif line.cost_method == CostMethod.PARAMETRIC.value:
        line.quantity = click.prompt("Quantity", type=float, default=line.quantity or 0.0)
        line.unit_of_measure = click.prompt(
            "Unit of measure", default=line.unit_of_measure or ""
        )
        line.unit_rate = click.prompt("Unit rate ($/unit)", type=float, default=line.unit_rate or 0.0)
    elif line.cost_method == CostMethod.PERCENTAGE.value:
        line.basis_line_ref = click.prompt(
            "Basis line_id (rollup with children)", default=line.basis_line_ref or ""
        )
        line.percentage_rate = click.prompt(
            "Percentage rate (decimal)", type=float, default=line.percentage_rate or 0.0
        )


# --------------------------------------------------------------------------
# add-component / edit-component
# --------------------------------------------------------------------------


@main.command("add-component")
@click.argument("line_id")
@click.option("--cost-type", type=click.Choice([t.value for t in CostType]))
@click.option("--cost-method", "-m", type=click.Choice(COMPONENT_METHOD_CHOICES))
@click.option("--lump-sum-basis", type=click.Choice(["quote", "historical", "analogous", "allowance"]))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit_of_measure")
@click.option("--unit-rate", type=float)
@click.option("--ref-type", type=click.Choice([r.value for r in RefType]))
@click.option("--basis-ref", help="line_id (ref-type=line) or sibling component_id (ref-type=sibling_component).")
@click.option("--rate", "percentage_rate", type=float)
@click.pass_context
def add_component(ctx, line_id, cost_type, cost_method, **opts):
    """Add a cost_component to a first_principles line."""
    lines = _load(ctx)
    line = _require_line(lines, line_id)
    if line.cost_method != CostMethod.FIRST_PRINCIPLES.value:
        raise click.UsageError(
            f"line '{line_id}' is not cost_method=first_principles; "
            "components can only be added to first_principles lines"
        )

    interactive = _is_interactive()
    if not cost_type:
        if interactive:
            cost_type = click.prompt("Cost type", type=click.Choice([t.value for t in CostType]))
        else:
            raise click.UsageError("--cost-type is required")
    if not cost_method:
        if interactive:
            cost_method = click.prompt(
                "Cost method", type=click.Choice(COMPONENT_METHOD_CHOICES)
            )
        else:
            raise click.UsageError("--cost-method is required")

    component_id = next_component_id(line)
    comp = CostComponent(component_id=component_id, cost_type=cost_type, cost_method=cost_method)
    _apply_component_attrs(line, comp, cost_method, opts, interactive)

    line.cost_components.append(comp)
    _save(ctx, lines)
    click.echo(f"Added component {component_id} to line {line_id}")


def _apply_component_attrs(line, comp: CostComponent, method: str, opts: dict, interactive: bool) -> None:
    if method == CostMethod.LUMP_SUM.value:
        basis, amount = fields.collect_lump_sum(
            opts.get("lump_sum_basis"), opts.get("amount"), interactive
        )
        comp.lump_sum_basis, comp.amount = basis, amount
    elif method == CostMethod.PARAMETRIC.value:
        qty, unit, rate = fields.collect_parametric(
            opts.get("quantity"), opts.get("unit_of_measure"), opts.get("unit_rate"), interactive
        )
        comp.quantity, comp.unit_of_measure, comp.unit_rate = qty, unit, rate
    elif method == CostMethod.PERCENTAGE.value:
        ref_type, basis_ref, rate = fields.collect_percentage_component(
            opts.get("ref_type"), opts.get("basis_ref"), opts.get("percentage_rate"), interactive
        )
        comp.ref_type, comp.basis_ref, comp.percentage_rate = ref_type, basis_ref, rate
    elif method == CostMethod.FIRST_PRINCIPLES.value:
        raise click.UsageError("cost_method 'first_principles' is not allowed on a cost_component")


@main.command("edit-component")
@click.argument("line_id")
@click.argument("component_id")
@click.option("--cost-type", type=click.Choice([t.value for t in CostType]))
@click.option("--cost-method", "-m", type=click.Choice(COMPONENT_METHOD_CHOICES))
@click.option("--lump-sum-basis", type=click.Choice(["quote", "historical", "analogous", "allowance"]))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit_of_measure")
@click.option("--unit-rate", type=float)
@click.option("--ref-type", type=click.Choice([r.value for r in RefType]))
@click.option("--basis-ref")
@click.option("--rate", "percentage_rate", type=float)
@click.pass_context
def edit_component(ctx, line_id, component_id, cost_type, cost_method, **opts):
    """Modify an existing cost_component."""
    lines = _load(ctx)
    line = _require_line(lines, line_id)
    comp = _require_component(line, component_id)

    any_flag = any(v is not None for v in [cost_type, cost_method, *opts.values()])
    if not any_flag:
        if not _is_interactive():
            raise click.UsageError("no fields provided to update")
        _interactive_edit_component(line, comp)
    else:
        if cost_type is not None:
            comp.cost_type = cost_type
        if cost_method is not None:
            if cost_method == CostMethod.FIRST_PRINCIPLES.value:
                raise click.UsageError(
                    "cost_method 'first_principles' is not allowed on a cost_component"
                )
            comp.cost_method = cost_method
        for key in ("lump_sum_basis", "amount", "quantity", "unit_of_measure", "unit_rate", "ref_type", "basis_ref", "percentage_rate"):
            if opts.get(key) is not None:
                setattr(comp, key, opts[key])

    _save(ctx, lines)
    click.echo(f"Updated component {component_id} on line {line_id}")


def _interactive_edit_component(line, comp: CostComponent) -> None:
    comp.cost_type = click.prompt(
        "Cost type", type=click.Choice([t.value for t in CostType]), default=comp.cost_type
    )
    comp.cost_method = click.prompt(
        "Cost method", type=click.Choice(COMPONENT_METHOD_CHOICES), default=comp.cost_method
    )
    if comp.cost_method == CostMethod.LUMP_SUM.value:
        comp.lump_sum_basis = click.prompt(
            "Lump sum basis",
            type=click.Choice(["quote", "historical", "analogous", "allowance"]),
            default=comp.lump_sum_basis or "quote",
        )
        comp.amount = click.prompt("Amount ($)", type=float, default=comp.amount or 0.0)
    elif comp.cost_method == CostMethod.PARAMETRIC.value:
        comp.quantity = click.prompt("Quantity", type=float, default=comp.quantity or 0.0)
        comp.unit_of_measure = click.prompt("Unit of measure", default=comp.unit_of_measure or "")
        comp.unit_rate = click.prompt("Unit rate ($/unit)", type=float, default=comp.unit_rate or 0.0)
    elif comp.cost_method == CostMethod.PERCENTAGE.value:
        comp.ref_type = click.prompt(
            "Reference type", type=click.Choice([r.value for r in RefType]), default=comp.ref_type or "line"
        )
        comp.basis_ref = click.prompt("Basis ref", default=comp.basis_ref or "")
        comp.percentage_rate = click.prompt(
            "Percentage rate (decimal)", type=float, default=comp.percentage_rate or 0.0
        )


# --------------------------------------------------------------------------
# remove-line / remove-component
# --------------------------------------------------------------------------


@main.command("remove-line")
@click.argument("line_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.option("--cascade", is_flag=True, help="Delete the entire subtree under this line.")
@click.option("--reassign-to", help="Reassign children to this parent line_id ('' for root) instead of deleting them.")
@click.pass_context
def remove_line(ctx, line_id, yes, cascade, reassign_to):
    """Remove a PBS line."""
    lines = _load(ctx)
    line = _require_line(lines, line_id)
    kids = children_of(lines, line_id)
    interactive = _is_interactive()

    if cascade and reassign_to is not None:
        raise click.UsageError("--cascade and --reassign-to are mutually exclusive")

    if kids and not cascade and reassign_to is None:
        if not interactive:
            raise click.UsageError(
                f"line '{line_id}' has children ({', '.join(kids)}); "
                "specify --cascade to delete the subtree or --reassign-to to reparent them"
            )
        click.echo(f"Line {line_id} has {len(kids)} child line(s): {', '.join(kids)}")
        choice = click.prompt(
            "Reassign children to line_id, 'cascade' to delete the whole subtree, "
            "or 'abort' (blank = reassign to this line's own parent)",
            default="",
        )
        if choice == "abort":
            click.echo("Aborted.")
            return
        if choice == "cascade":
            cascade = True
        else:
            reassign_to = choice or (line.parent_line_id or "")

    if not yes:
        what = "line and its entire subtree" if cascade else "line"
        click.confirm(f"Remove {what} '{line_id}'?", abort=True)

    try:
        if cascade:
            cascade_delete_line(lines, line_id)
        else:
            new_parent = (reassign_to or None) if reassign_to is not None else None
            reassign_children_and_delete_line(lines, line_id, new_parent)
    except OperationError as e:
        raise click.UsageError(str(e))

    _save(ctx, lines)
    click.echo(f"Removed line {line_id}")


@main.command("remove-component")
@click.argument("line_id")
@click.argument("component_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def remove_component(ctx, line_id, component_id, yes):
    """Remove a cost_component from a first_principles line."""
    lines = _load(ctx)
    line = _require_line(lines, line_id)
    comp = _require_component(line, component_id)

    if not yes:
        click.confirm(f"Remove component '{component_id}' from line '{line_id}'?", abort=True)

    line.cost_components = [c for c in line.cost_components if c.component_id != component_id]
    _save(ctx, lines)
    click.echo(f"Removed component {component_id} from line {line_id}")


# --------------------------------------------------------------------------
# move-line
# --------------------------------------------------------------------------


@main.command("move-line")
@click.argument("line_id")
@click.argument("direction", type=click.Choice(["up", "down"]))
@click.pass_context
def move_line_cmd(ctx, line_id, direction):
    """Move a line up or down among its siblings (changes its WBS number)."""
    lines = _load(ctx)
    _require_line(lines, line_id)
    moved = move_line(lines, line_id, direction)
    if not moved:
        click.echo(f"{line_id} is already at the {'top' if direction == 'up' else 'bottom'} of its siblings")
        return
    _save(ctx, lines)
    wbs = display_wbs(lines[line_id], compute_wbs_numbers(lines))
    click.echo(f"Moved {line_id} {direction} (WBS {wbs})")


# --------------------------------------------------------------------------
# show-tree / show-line
# --------------------------------------------------------------------------


def _method_tag(result: LineResult) -> str:
    if result.method is None:
        return "rollup" if result.has_children else "empty"
    return result.method


def _format_cost(result: LineResult) -> str:
    if not result.resolved:
        return f"UNRESOLVED ({result.reason})"
    return f"${result.cost:,.2f}"


@main.command("show-tree")
@click.argument("line_id", required=False)
@click.pass_context
def show_tree(ctx, line_id):
    """Print the PBS hierarchy with rolled-up costs (indented tree view)."""
    lines = _load(ctx)
    if not lines:
        click.echo("(empty tree)")
        return
    if line_id:
        _require_line(lines, line_id)
        roots = [line_id]
    else:
        roots = root_lines(lines)

    calculator = CostCalculator(lines)
    wbs_numbers = compute_wbs_numbers(lines)
    for root in roots:
        _print_line(lines, calculator, wbs_numbers, root, depth=0)


def _print_line(lines, calculator: CostCalculator, wbs_numbers, line_id: str, depth: int) -> None:
    line = lines[line_id]
    result = calculator.calculate_line(line_id)
    indent = "  " * depth
    flag = "  [!] has children, not rolled up" if result.leaf_equivalent_with_children else ""
    wbs = display_wbs(line, wbs_numbers)
    click.echo(
        f"{indent}{wbs}  {line_id} [{_method_tag(result)}] {line.line_name} - {_format_cost(result)}{flag}"
    )
    for child_id in children_of(lines, line_id):
        _print_line(lines, calculator, wbs_numbers, child_id, depth + 1)


@main.command("show-line")
@click.argument("line_id")
@click.pass_context
def show_line(ctx, line_id):
    """Print full detail for one line."""
    lines = _load(ctx)
    line = _require_line(lines, line_id)
    calculator = CostCalculator(lines)
    result = calculator.calculate_line(line_id)
    wbs = display_wbs(line, compute_wbs_numbers(lines))

    click.echo(f"line_id:          {line.line_id}")
    click.echo(f"wbs:              {wbs}{' (pinned)' if line.wbs_override else ''}")
    click.echo(f"line_name:        {line.line_name}")
    click.echo(f"parent_line_id:   {line.parent_line_id or '(root)'}")
    click.echo(f"cost_method:      {line.cost_method or '(none - rollup)'}")
    kids = children_of(lines, line_id)
    click.echo(f"children:         {', '.join(kids) if kids else '(none)'}")
    if result.leaf_equivalent_with_children:
        click.echo("NOTE:             has both a cost_method and children - children are NOT rolled up into this line")

    if line.cost_method == CostMethod.LUMP_SUM.value:
        click.echo(f"lump_sum_basis:   {line.lump_sum_basis}")
        click.echo(f"confidence:       {confidence_for_basis(line.lump_sum_basis)}")
        click.echo(f"amount:           {line.amount}")
    elif line.cost_method == CostMethod.PARAMETRIC.value:
        click.echo(f"quantity:         {line.quantity}")
        click.echo(f"unit_of_measure:  {line.unit_of_measure}")
        click.echo(f"unit_rate:        {line.unit_rate}")
    elif line.cost_method == CostMethod.PERCENTAGE.value:
        click.echo(f"basis_line_ref:   {line.basis_line_ref}")
        click.echo(f"percentage_rate:  {line.percentage_rate}")
    elif line.cost_method == CostMethod.FIRST_PRINCIPLES.value:
        click.echo("cost_components:")
        if not line.cost_components:
            click.echo("  (none)")
        for comp in line.cost_components:
            comp_result = calculator.calculate_component(line, comp)
            click.echo(
                f"  {comp.component_id} [{comp.cost_type}/{comp.cost_method}] "
                f"- {_format_cost(comp_result) if comp_result.resolved else 'UNRESOLVED (' + str(comp_result.reason) + ')'}"
            )

    click.echo(f"rolled_up_cost:   {_format_cost(result)}")


# --------------------------------------------------------------------------
# calc
# --------------------------------------------------------------------------


@main.command("calc")
@click.argument("line_id", required=False)
@click.pass_context
def calc_cmd(ctx, line_id):
    """Recalculate and display the rolled-up cost for the tree, or a subtree."""
    lines = _load(ctx)
    if not lines:
        click.echo("(empty tree)")
        return

    calculator = CostCalculator(lines)
    results = calculator.calculate_all()

    if line_id:
        _require_line(lines, line_id)
        result = results[line_id]
        click.echo(f"{line_id}: {_format_cost(result)}")
        return

    incomplete = {lid: r for lid, r in results.items() if not r.resolved}
    total = 0.0
    any_unresolved_root = False
    for root in root_lines(lines):
        r = results[root]
        if r.resolved:
            total += r.cost or 0.0
        else:
            any_unresolved_root = True

    if any_unresolved_root:
        click.echo(f"Total (partial - some root lines unresolved): ${total:,.2f}")
    else:
        click.echo(f"Total: ${total:,.2f}")

    if incomplete:
        click.echo(f"\n{len(incomplete)} unresolved line(s):")
        for lid, r in incomplete.items():
            click.echo(f"  {lid}: {r.reason}")


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------


@main.command("validate")
@click.pass_context
def validate_cmd(ctx):
    """Run all validation rules and report errors/warnings."""
    lines = _load(ctx)
    report = validate_tree(lines)

    if report.errors:
        click.echo(f"ERRORS ({len(report.errors)}):")
        for e in report.errors:
            click.echo(f"  [E] {e}")
    if report.warnings:
        click.echo(f"WARNINGS ({len(report.warnings)}):")
        for w in report.warnings:
            click.echo(f"  [W] {w}")
    if report.incomplete:
        click.echo(f"INCOMPLETE ({len(report.incomplete)}):")
        for i in report.incomplete:
            click.echo(f"  [I] {i}")

    if not (report.errors or report.warnings or report.incomplete):
        click.echo("OK - no issues found.")

    if not report.ok:
        sys.exit(1)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


@main.command("export")
@click.argument("output_path")
@click.pass_context
def export_cmd(ctx, output_path):
    """Export the tree to a flattened CSV file."""
    lines = _load(ctx)
    export_csv(lines, output_path)
    click.echo(f"Exported {len(lines)} line(s) to {output_path}")


# --------------------------------------------------------------------------
# save-as
# --------------------------------------------------------------------------


@main.command("save-as")
@click.argument("new_file")
@click.pass_context
def save_as_cmd(ctx, new_file):
    """Save the current tree to a different JSON file (a copy/snapshot)."""
    lines = _load(ctx)
    JSONRepository(new_file).save(lines)
    click.echo(f"Saved {len(lines)} line(s) to {new_file}")


# --------------------------------------------------------------------------
# tui
# --------------------------------------------------------------------------


@main.command("tui")
@click.pass_context
def tui_cmd(ctx):
    """Launch the interactive terminal UI."""
    from .tui import run_tui

    run_tui(ctx.obj["file"])


if __name__ == "__main__":
    main()
