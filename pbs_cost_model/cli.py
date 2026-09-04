"""CLI commands (click). Talks only to a Repository -- never to Store/files
directly -- and to calc.py / validation.py for business logic."""

from __future__ import annotations

import csv
import sys
from typing import Optional

import click

from .models import (
    COST_METHODS,
    COMPONENT_COST_METHODS,
    LUMP_SUM_BASES,
    COST_TYPES,
    REF_TYPES,
    confidence_for_basis,
)
from .repository import Repository, RepositoryError
from .storage import JSONFileStore
from .calc import CostCalculator, compute
from .validation import (
    validate as run_validate,
    check_percentage_basis_at_input,
    check_no_cycle_at_input,
)

METHOD_CHOICES = list(COST_METHODS) + ["none"]
COMPONENT_METHOD_CHOICES = list(COMPONENT_COST_METHODS)


def fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def die(message: str) -> None:
    raise click.ClickException(message)


# --------------------------------------------------------------------------
# root group
# --------------------------------------------------------------------------


@click.group()
@click.option(
    "--file",
    "-f",
    "file_path",
    default="pbs_tree.json",
    show_default=True,
    type=click.Path(dir_okay=False),
    help="Path to the JSON data file.",
)
@click.pass_context
def cli(ctx: click.Context, file_path: str) -> None:
    """PBS first-principles cost model builder."""
    ctx.ensure_object(dict)
    ctx.obj["repo"] = Repository(JSONFileStore(file_path))
    ctx.obj["file_path"] = file_path


def get_repo(ctx: click.Context) -> Repository:
    return ctx.obj["repo"]


# --------------------------------------------------------------------------
# shared attribute gathering for lines & components
# --------------------------------------------------------------------------


def _prompt_method_attrs(cost_method: str, existing=None, prefix: str = "") -> dict:
    """Interactively prompt for the attributes required by cost_method.
    `existing` (an object with attributes) supplies defaults when editing."""

    def default(attr, fallback=None):
        return getattr(existing, attr, None) if existing is not None else fallback

    attrs: dict = {}
    if cost_method == "lump_sum":
        attrs["lump_sum_basis"] = click.prompt(
            f"{prefix}lump_sum_basis",
            type=click.Choice(LUMP_SUM_BASES),
            default=default("lump_sum_basis"),
        )
        attrs["amount"] = click.prompt(
            f"{prefix}amount", type=float, default=default("amount")
        )
    elif cost_method == "parametric":
        attrs["quantity"] = click.prompt(
            f"{prefix}quantity", type=float, default=default("quantity")
        )
        attrs["unit_of_measure"] = click.prompt(
            f"{prefix}unit_of_measure", type=str, default=default("unit_of_measure")
        )
        attrs["unit_rate"] = click.prompt(
            f"{prefix}unit_rate", type=float, default=default("unit_rate")
        )
    elif cost_method == "percentage":
        attrs["basis_line_ref"] = click.prompt(
            f"{prefix}basis_line_ref (line_id)", type=str, default=default("basis_line_ref")
        )
        attrs["percentage_rate"] = click.prompt(
            f"{prefix}percentage_rate (decimal, e.g. 0.05 for 5%)",
            type=float,
            default=default("percentage_rate"),
        )
    return attrs


def _collect_line_method_attrs(
    ctx_params: dict, cost_method: Optional[str], interactive: bool, existing=None
) -> dict:
    """Merge explicitly-passed flags with (optionally) interactive prompts
    for whatever attrs `cost_method` requires. first_principles has no
    line-level attrs of its own (components are added separately)."""
    if cost_method in (None, "none"):
        return {}

    flag_map = {
        "lump_sum": {
            "lump_sum_basis": ctx_params.get("lump_sum_basis"),
            "amount": ctx_params.get("amount"),
        },
        "parametric": {
            "quantity": ctx_params.get("quantity"),
            "unit_of_measure": ctx_params.get("unit"),
            "unit_rate": ctx_params.get("unit_rate"),
        },
        "percentage": {
            "basis_line_ref": ctx_params.get("basis_line"),
            "percentage_rate": ctx_params.get("percentage_rate"),
        },
        "first_principles": {},
    }
    attrs = {k: v for k, v in flag_map.get(cost_method, {}).items() if v is not None}
    required = {
        "lump_sum": ("lump_sum_basis", "amount"),
        "parametric": ("quantity", "unit_of_measure", "unit_rate"),
        "percentage": ("basis_line_ref", "percentage_rate"),
        "first_principles": (),
    }[cost_method]
    missing = [a for a in required if a not in attrs]
    if missing and interactive:
        prompted = _prompt_method_attrs(cost_method, existing=existing)
        attrs.update({k: v for k, v in prompted.items() if k in missing or k not in attrs})
    elif missing:
        click.echo(
            f"warning: {cost_method} line left incomplete, missing: {', '.join(missing)}",
            err=True,
        )
    return attrs


def _clear_method_attrs() -> dict:
    return {
        "lump_sum_basis": None,
        "amount": None,
        "quantity": None,
        "unit_of_measure": None,
        "unit_rate": None,
        "basis_line_ref": None,
        "percentage_rate": None,
    }


def _validate_percentage_ref(repo: Repository, line_id: Optional[str], basis_line_ref: str) -> None:
    err = check_percentage_basis_at_input(repo, basis_line_ref)
    if err:
        die(err)
    if line_id is not None:
        err = check_no_cycle_at_input(repo, line_id, basis_line_ref)
        if err:
            die(err)


# --------------------------------------------------------------------------
# add-line
# --------------------------------------------------------------------------


@cli.command("add-line")
@click.option("--name", "-n", "line_name", help="Line description/scope.")
@click.option("--parent", "-p", "parent_line_id", help="Parent line_id (omit for a root line).")
@click.option(
    "--cost-method",
    "-m",
    type=click.Choice(METHOD_CHOICES),
    help="lump_sum | parametric | percentage | first_principles | none",
)
@click.option("--lump-sum-basis", type=click.Choice(LUMP_SUM_BASES))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit", help="unit_of_measure, e.g. LF, SF, mile, EA")
@click.option("--unit-rate", type=float)
@click.option("--basis-line", help="basis_line_ref for cost_method=percentage")
@click.option("--percentage-rate", type=float)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Never prompt; leave missing attributes unset (useful for scripting).",
)
@click.pass_context
def add_line(
    ctx,
    line_name,
    parent_line_id,
    cost_method,
    lump_sum_basis,
    amount,
    quantity,
    unit,
    unit_rate,
    basis_line,
    percentage_rate,
    non_interactive,
):
    """Add a new PBS line."""
    repo = get_repo(ctx)
    interactive = not non_interactive and sys.stdin.isatty()

    if not line_name:
        if interactive:
            line_name = click.prompt("Line name")
        else:
            die("--name is required")

    if parent_line_id is None and interactive:
        parent_line_id = click.prompt("Parent line_id (blank for root)", default="", show_default=False)
    parent_line_id = parent_line_id or None
    if parent_line_id is not None and repo.get_line(parent_line_id) is None:
        die(f"parent_line_id {parent_line_id!r} does not exist")

    if cost_method is None and interactive:
        cost_method = click.prompt(
            "cost_method", type=click.Choice(METHOD_CHOICES), default="none"
        )
    if cost_method == "none":
        cost_method = None

    attrs = _collect_line_method_attrs(
        {
            "lump_sum_basis": lump_sum_basis,
            "amount": amount,
            "quantity": quantity,
            "unit": unit,
            "unit_rate": unit_rate,
            "basis_line": basis_line,
            "percentage_rate": percentage_rate,
        },
        cost_method,
        interactive,
    )

    if cost_method == "percentage" and attrs.get("basis_line_ref"):
        _validate_percentage_ref(repo, None, attrs["basis_line_ref"])

    line = repo.add_line(
        line_name=line_name, parent_line_id=parent_line_id, cost_method=cost_method, **attrs
    )
    repo.save()
    click.echo(f"Added line {line.line_id}: {line.line_name}")


# --------------------------------------------------------------------------
# edit-line
# --------------------------------------------------------------------------


@cli.command("edit-line")
@click.argument("line_id")
@click.option("--name", "-n", "line_name")
@click.option("--parent", "-p", "parent_line_id", help='New parent line_id, or "" for root.')
@click.option("--cost-method", "-m", type=click.Choice(METHOD_CHOICES))
@click.option("--lump-sum-basis", type=click.Choice(LUMP_SUM_BASES))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit")
@click.option("--unit-rate", type=float)
@click.option("--basis-line", help="basis_line_ref for cost_method=percentage")
@click.option("--percentage-rate", type=float)
@click.option("--non-interactive", is_flag=True, default=False)
@click.pass_context
def edit_line(
    ctx,
    line_id,
    line_name,
    parent_line_id,
    cost_method,
    lump_sum_basis,
    amount,
    quantity,
    unit,
    unit_rate,
    basis_line,
    percentage_rate,
    non_interactive,
):
    """Edit an existing PBS line's attributes."""
    repo = get_repo(ctx)
    line = repo.get_line(line_id)
    if line is None:
        die(f"No such line_id: {line_id!r}")
    interactive = not non_interactive and sys.stdin.isatty()

    changes: dict = {}
    if line_name is not None:
        changes["line_name"] = line_name
    if parent_line_id is not None:
        changes["parent_line_id"] = parent_line_id or None

    method_changed = cost_method is not None and cost_method != (line.cost_method or "none")
    effective_method = line.cost_method if cost_method is None else (
        None if cost_method == "none" else cost_method
    )

    if method_changed:
        changes.update(_clear_method_attrs())
        changes["cost_method"] = effective_method

    if effective_method:
        attrs = _collect_line_method_attrs(
            {
                "lump_sum_basis": lump_sum_basis,
                "amount": amount,
                "quantity": quantity,
                "unit": unit,
                "unit_rate": unit_rate,
                "basis_line": basis_line,
                "percentage_rate": percentage_rate,
            },
            effective_method,
            # Only prompt if the user is actively changing to/within this
            # method; a bare `edit-line L1 --amount 500` shouldn't re-prompt
            # for every other attribute.
            interactive and method_changed,
            existing=line,
        )
        changes.update(attrs)

    if effective_method == "percentage":
        basis = changes.get("basis_line_ref", line.basis_line_ref)
        if basis:
            _validate_percentage_ref(repo, line_id, basis)

    if not changes:
        click.echo("Nothing to change.")
        return

    repo.update_line(line_id, **changes)
    repo.save()
    click.echo(f"Updated line {line_id}")


# --------------------------------------------------------------------------
# add-component / edit-component
# --------------------------------------------------------------------------


@cli.command("add-component")
@click.argument("line_id")
@click.option("--cost-type", type=click.Choice(COST_TYPES), required=False)
@click.option("--cost-method", "-m", type=click.Choice(COMPONENT_METHOD_CHOICES), required=False)
@click.option("--lump-sum-basis", type=click.Choice(LUMP_SUM_BASES))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit")
@click.option("--unit-rate", type=float)
@click.option("--ref-type", type=click.Choice(REF_TYPES), help="For percentage: line | sibling_component")
@click.option("--basis-line", help="basis_line_ref: a line_id (ref_type=line) or component_id (ref_type=sibling_component)")
@click.option("--percentage-rate", type=float)
@click.option("--non-interactive", is_flag=True, default=False)
@click.pass_context
def add_component(
    ctx,
    line_id,
    cost_type,
    cost_method,
    lump_sum_basis,
    amount,
    quantity,
    unit,
    unit_rate,
    ref_type,
    basis_line,
    percentage_rate,
    non_interactive,
):
    """Add a cost_component to a first_principles PBS line."""
    repo = get_repo(ctx)
    line = repo.get_line(line_id)
    if line is None:
        die(f"No such line_id: {line_id!r}")
    if line.cost_method != "first_principles":
        die(
            f"line {line_id!r} has cost_method={line.cost_method!r}; components can only be "
            f"added to a first_principles line (set it first: edit-line {line_id} -m first_principles)"
        )

    interactive = not non_interactive and sys.stdin.isatty()

    if cost_type is None:
        if interactive:
            cost_type = click.prompt("cost_type", type=click.Choice(COST_TYPES))
        else:
            die("--cost-type is required")
    if cost_method is None:
        if interactive:
            cost_method = click.prompt("cost_method", type=click.Choice(COMPONENT_METHOD_CHOICES))
        else:
            die("--cost-method is required")

    attrs: dict = {}
    if cost_method == "lump_sum":
        attrs["lump_sum_basis"] = lump_sum_basis
        attrs["amount"] = amount
        missing = [k for k in ("lump_sum_basis", "amount") if attrs.get(k) is None]
        if missing and interactive:
            attrs.update(_prompt_method_attrs("lump_sum"))
        elif missing:
            click.echo(f"warning: component left incomplete, missing: {', '.join(missing)}", err=True)
    elif cost_method == "parametric":
        attrs["quantity"] = quantity
        attrs["unit_of_measure"] = unit
        attrs["unit_rate"] = unit_rate
        missing = [k for k in ("quantity", "unit_of_measure", "unit_rate") if attrs.get(k) is None]
        if missing and interactive:
            attrs.update(_prompt_method_attrs("parametric"))
        elif missing:
            click.echo(f"warning: component left incomplete, missing: {', '.join(missing)}", err=True)
    elif cost_method == "percentage":
        if ref_type is None and interactive:
            ref_type = click.prompt("ref_type", type=click.Choice(REF_TYPES))
        if basis_line is None and interactive:
            basis_line = click.prompt("basis_line_ref (line_id or sibling component_id)")
        if percentage_rate is None and interactive:
            percentage_rate = click.prompt("percentage_rate (decimal, e.g. 0.05 for 5%)", type=float)
        attrs["ref_type"] = ref_type
        attrs["basis_line_ref"] = basis_line
        attrs["percentage_rate"] = percentage_rate
        missing = [k for k in ("ref_type", "basis_line_ref", "percentage_rate") if attrs.get(k) is None]
        if missing:
            click.echo(f"warning: component left incomplete, missing: {', '.join(missing)}", err=True)
        if ref_type == "line" and basis_line:
            err = check_percentage_basis_at_input(repo, basis_line)
            if err:
                die(err)
        elif ref_type == "sibling_component" and basis_line:
            if not any(c.component_id == basis_line for c in line.cost_components):
                die(f"sibling_component {basis_line!r} does not exist on line {line_id!r}")

    component = repo.add_component(line_id, cost_type=cost_type, cost_method=cost_method, **attrs)
    repo.save()
    click.echo(f"Added component {component.component_id} to line {line_id}")


@cli.command("edit-component")
@click.argument("line_id")
@click.argument("component_id")
@click.option("--cost-type", type=click.Choice(COST_TYPES))
@click.option("--cost-method", "-m", type=click.Choice(COMPONENT_METHOD_CHOICES))
@click.option("--lump-sum-basis", type=click.Choice(LUMP_SUM_BASES))
@click.option("--amount", type=float)
@click.option("--quantity", type=float)
@click.option("--unit", "unit")
@click.option("--unit-rate", type=float)
@click.option("--ref-type", type=click.Choice(REF_TYPES))
@click.option("--basis-line", help="basis_line_ref: a line_id or sibling component_id")
@click.option("--percentage-rate", type=float)
@click.pass_context
def edit_component(
    ctx,
    line_id,
    component_id,
    cost_type,
    cost_method,
    lump_sum_basis,
    amount,
    quantity,
    unit,
    unit_rate,
    ref_type,
    basis_line,
    percentage_rate,
):
    """Edit an existing cost_component."""
    repo = get_repo(ctx)
    comp = repo.get_component(line_id, component_id)
    if comp is None:
        die(f"No such component {component_id!r} on line {line_id!r}")

    changes: dict = {}
    if cost_type is not None:
        changes["cost_type"] = cost_type

    method_changed = cost_method is not None and cost_method != comp.cost_method
    if method_changed:
        changes.update(
            {
                "lump_sum_basis": None,
                "amount": None,
                "quantity": None,
                "unit_of_measure": None,
                "unit_rate": None,
                "basis_line_ref": None,
                "ref_type": None,
                "percentage_rate": None,
                "cost_method": cost_method,
            }
        )

    if lump_sum_basis is not None:
        changes["lump_sum_basis"] = lump_sum_basis
    if amount is not None:
        changes["amount"] = amount
    if quantity is not None:
        changes["quantity"] = quantity
    if unit is not None:
        changes["unit_of_measure"] = unit
    if unit_rate is not None:
        changes["unit_rate"] = unit_rate
    if ref_type is not None:
        changes["ref_type"] = ref_type
    if basis_line is not None:
        changes["basis_line_ref"] = basis_line
    if percentage_rate is not None:
        changes["percentage_rate"] = percentage_rate

    effective_method = changes.get("cost_method", comp.cost_method)
    effective_ref_type = changes.get("ref_type", comp.ref_type)
    effective_basis = changes.get("basis_line_ref", comp.basis_line_ref)
    if effective_method == "percentage" and effective_basis:
        if effective_ref_type == "line":
            err = check_percentage_basis_at_input(repo, effective_basis)
            if err:
                die(err)
        elif effective_ref_type == "sibling_component":
            if effective_basis == component_id:
                die("a component cannot reference itself as basis_line_ref")
            line = repo.require_line(line_id)
            if not any(c.component_id == effective_basis for c in line.cost_components):
                die(f"sibling_component {effective_basis!r} does not exist on line {line_id!r}")

    if not changes:
        click.echo("Nothing to change.")
        return

    repo.update_component(line_id, component_id, **changes)
    repo.save()
    click.echo(f"Updated component {component_id} on line {line_id}")


# --------------------------------------------------------------------------
# remove-line / remove-component
# --------------------------------------------------------------------------


@cli.command("remove-line")
@click.argument("line_id")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.option("--cascade", is_flag=True, default=False, help="Delete the line and its entire subtree.")
@click.option(
    "--reassign-to",
    default=None,
    help='Re-parent this line\'s children to the given line_id (use "" for root) instead of deleting them.',
)
@click.pass_context
def remove_line(ctx, line_id, yes, cascade, reassign_to):
    """Remove a PBS line. If it has children, you must choose to either
    cascade-delete the whole subtree or reassign the children elsewhere."""
    repo = get_repo(ctx)
    line = repo.get_line(line_id)
    if line is None:
        die(f"No such line_id: {line_id!r}")

    children = repo.children_of(line_id)
    interactive = sys.stdin.isatty()

    if children:
        if cascade and reassign_to is not None:
            die("pass only one of --cascade / --reassign-to")

        if not cascade and reassign_to is None:
            if not interactive:
                die(
                    f"line {line_id!r} has {len(children)} child(ren); pass --cascade to delete "
                    f'them too, or --reassign-to <line_id|""> to re-parent them first'
                )
            click.echo(f"Line {line_id!r} has {len(children)} child(ren):")
            for c in children:
                click.echo(f"  - {c.line_id}: {c.line_name}")
            choice = click.prompt(
                "Cascade-delete the whole subtree, or reassign children?",
                type=click.Choice(["cascade", "reassign", "abort"]),
                default="abort",
            )
            if choice == "abort":
                click.echo("Aborted.")
                return
            if choice == "cascade":
                cascade = True
            else:
                reassign_to = click.prompt(
                    'New parent line_id for the children (blank for root)',
                    default=line.parent_line_id or "",
                    show_default=False,
                )

        if cascade:
            if not yes and not click.confirm(
                f"Delete line {line_id!r} and its {len(repo.descendants_of(line_id))} descendant(s)?"
            ):
                click.echo("Aborted.")
                return
            deleted = repo.cascade_delete(line_id)
            repo.save()
            click.echo(f"Deleted {len(deleted)} line(s): {', '.join(deleted)}")
            return

        if reassign_to is not None:
            new_parent = reassign_to or None
            if new_parent is not None and repo.get_line(new_parent) is None:
                die(f"reassign-to target {new_parent!r} does not exist")
            if new_parent == line_id:
                die("cannot reassign children to the line being deleted")
            if not yes and not click.confirm(
                f"Reassign {len(children)} child(ren) of {line_id!r} to "
                f"{new_parent or 'root'} and delete {line_id!r}?"
            ):
                click.echo("Aborted.")
                return
            repo.reassign_children(line_id, new_parent)
            repo.delete_line(line_id)
            repo.save()
            click.echo(f"Reassigned {len(children)} child(ren) to {new_parent or 'root'} and deleted {line_id}")
            return

    # no children (or already handled above)
    if not yes and not click.confirm(f"Delete line {line_id!r} ({line.line_name})?"):
        click.echo("Aborted.")
        return
    repo.delete_line(line_id)
    repo.save()
    click.echo(f"Deleted line {line_id}")


@cli.command("remove-component")
@click.argument("line_id")
@click.argument("component_id")
@click.option("--yes", "-y", is_flag=True, default=False)
@click.pass_context
def remove_component(ctx, line_id, component_id, yes):
    """Remove a cost_component from a first_principles line."""
    repo = get_repo(ctx)
    comp = repo.get_component(line_id, component_id)
    if comp is None:
        die(f"No such component {component_id!r} on line {line_id!r}")
    if not yes and not click.confirm(f"Delete component {component_id!r} from line {line_id!r}?"):
        click.echo("Aborted.")
        return
    repo.delete_component(line_id, component_id)
    repo.save()
    click.echo(f"Deleted component {component_id} from line {line_id}")


# --------------------------------------------------------------------------
# show-tree / show-line
# --------------------------------------------------------------------------


def _line_tag(line) -> str:
    tag = line.cost_method or "rollup"
    return tag


@cli.command("show-tree")
@click.option("--show-components", is_flag=True, default=False, help="Also list cost_components under first_principles lines.")
@click.pass_context
def show_tree(ctx, show_components):
    """Print the full PBS hierarchy with rolled-up costs at each level."""
    repo = get_repo(ctx)
    calculator = CostCalculator(repo)

    def render(line_id, depth):
        line = repo.get_line(line_id)
        lr = calculator.line_result(line_id)
        indent = "  " * depth
        tag = _line_tag(line)
        ambiguous = " [+children]" if line.cost_method and repo.has_children(line_id) else ""
        if lr.resolved:
            cost = fmt_money(lr.rolled_up_cost)
        else:
            cost = "INCOMPLETE"
        click.echo(f"{indent}[{line.line_id}] {line.line_name} -- {cost} ({tag}{ambiguous})")
        if not lr.resolved:
            for issue in lr.issues:
                click.echo(f"{indent}    ! {issue}")
        if show_components and line.cost_method == "first_principles":
            for comp in line.cost_components:
                cr = lr.component_results.get(comp.component_id)
                ccost = fmt_money(cr.calculated_cost) if cr and cr.resolved else "INCOMPLETE"
                click.echo(
                    f"{indent}    ({comp.component_id}) {comp.cost_type}/{comp.cost_method}: {ccost}"
                )
        for child in repo.children_of(line_id):
            render(child.line_id, depth + 1)

    roots = repo.roots()
    if not roots:
        click.echo("(empty tree)")
        return
    for r in roots:
        render(r.line_id, 0)

    all_resolved = all(calculator.line_result(r.line_id).resolved for r in roots)
    if all_resolved:
        total = sum(calculator.line_result(r.line_id).rolled_up_cost for r in roots)
        click.echo(f"\nTOTAL: {fmt_money(total)}")
    else:
        click.echo("\nTOTAL: INCOMPLETE (some lines unresolved -- see `calc` or `validate`)")


@cli.command("show-line")
@click.argument("line_id")
@click.pass_context
def show_line(ctx, line_id):
    """Print full detail and attributes for one line."""
    repo = get_repo(ctx)
    line = repo.get_line(line_id)
    if line is None:
        die(f"No such line_id: {line_id!r}")

    calculator = CostCalculator(repo)
    lr = calculator.line_result(line_id)

    click.echo(f"line_id:         {line.line_id}")
    click.echo(f"line_name:       {line.line_name}")
    click.echo(f"parent_line_id:  {line.parent_line_id or '(root)'}")
    click.echo(f"cost_method:     {line.cost_method or '(none -- rollup)'}")

    if line.cost_method == "lump_sum":
        click.echo(f"lump_sum_basis:  {line.lump_sum_basis}")
        click.echo(f"confidence:      {confidence_for_basis(line.lump_sum_basis)}")
        click.echo(f"amount:          {fmt_money(line.amount)}")
    elif line.cost_method == "parametric":
        click.echo(f"quantity:        {line.quantity}")
        click.echo(f"unit_of_measure: {line.unit_of_measure}")
        click.echo(f"unit_rate:       {line.unit_rate}")
    elif line.cost_method == "percentage":
        click.echo(f"basis_line_ref:  {line.basis_line_ref}")
        click.echo(f"percentage_rate: {line.percentage_rate}")
    elif line.cost_method == "first_principles":
        click.echo(f"cost_components ({len(line.cost_components)}):")
        for comp in line.cost_components:
            cr = lr.component_results.get(comp.component_id)
            ccost = fmt_money(cr.calculated_cost) if cr and cr.resolved else "INCOMPLETE"
            click.echo(f"  ({comp.component_id}) cost_type={comp.cost_type} cost_method={comp.cost_method} -> {ccost}")
            if comp.cost_method == "lump_sum":
                click.echo(f"      lump_sum_basis={comp.lump_sum_basis} confidence={confidence_for_basis(comp.lump_sum_basis)} amount={comp.amount}")
            elif comp.cost_method == "parametric":
                click.echo(f"      quantity={comp.quantity} unit_of_measure={comp.unit_of_measure} unit_rate={comp.unit_rate}")
            elif comp.cost_method == "percentage":
                click.echo(f"      ref_type={comp.ref_type} basis_line_ref={comp.basis_line_ref} percentage_rate={comp.percentage_rate}")
            if cr and not cr.resolved:
                for issue in cr.issues:
                    click.echo(f"      ! {issue}")

    children = repo.children_of(line_id)
    if children:
        click.echo(f"children ({len(children)}):")
        for c in children:
            click.echo(f"  - {c.line_id}: {c.line_name}")

    click.echo("")
    click.echo(f"own_cost:        {fmt_money(lr.own_cost)}")
    click.echo(f"children_cost:   {fmt_money(lr.children_cost)}")
    click.echo(f"rolled_up_cost:  {fmt_money(lr.rolled_up_cost) if lr.resolved else 'INCOMPLETE'}")
    if not lr.resolved:
        click.echo("issues:")
        for issue in lr.issues:
            click.echo(f"  ! {issue}")


# --------------------------------------------------------------------------
# calc
# --------------------------------------------------------------------------


@cli.command("calc")
@click.argument("line_id", required=False)
@click.pass_context
def calc_cmd(ctx, line_id):
    """Recalculate and display rolled-up cost for the whole tree, or a
    subtree if LINE_ID is given. Incomplete lines are flagged, not fatal."""
    repo = get_repo(ctx)
    results = compute(repo)

    if line_id:
        if line_id not in results:
            die(f"No such line_id: {line_id!r}")
        targets = [line_id]
    else:
        targets = [r.line_id for r in repo.roots()]

    any_unresolved = False
    grand_total = 0.0
    for lid in targets:
        lr = results[lid]
        line = repo.get_line(lid)
        if lr.resolved:
            click.echo(f"[{lid}] {line.line_name}: {fmt_money(lr.rolled_up_cost)}")
            grand_total += lr.rolled_up_cost
        else:
            any_unresolved = True
            click.echo(f"[{lid}] {line.line_name}: INCOMPLETE")

    click.echo("")
    unresolved = [r for r in results.values() if not r.resolved]
    if unresolved:
        click.echo(f"{len(unresolved)} line(s) unresolved / incomplete:")
        for lr in unresolved:
            line = repo.get_line(lr.line_id)
            name = line.line_name if line else "?"
            click.echo(f"  [{lr.line_id}] {name}:")
            for issue in lr.issues:
                click.echo(f"      ! {issue}")

    if not line_id:
        if any_unresolved:
            click.echo("\nTOTAL: INCOMPLETE (one or more root lines unresolved)")
        else:
            click.echo(f"\nTOTAL: {fmt_money(grand_total)}")


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------


@cli.command("validate")
@click.pass_context
def validate_cmd(ctx):
    """Run all validation rules and report errors/warnings with line_id refs."""
    repo = get_repo(ctx)
    issues = run_validate(repo)
    if not issues:
        click.echo("OK: no validation issues found.")
        return

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    for issue in issues:
        click.echo(str(issue))

    click.echo("")
    click.echo(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    if errors:
        sys.exit(1)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

CSV_COLUMNS = [
    "row_type",
    "line_id",
    "component_id",
    "parent_line_id",
    "line_name",
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
    "percentage_rate",
    "own_cost",
    "rolled_up_cost",
    "resolved",
    "issues",
]


@cli.command("export")
@click.option("--output", "-o", default="pbs_export.csv", show_default=True, type=click.Path(dir_okay=False))
@click.pass_context
def export_cmd(ctx, output):
    """Export the current tree to CSV (one row per line, one row per
    cost_component) for review outside the CLI."""
    repo = get_repo(ctx)
    results = compute(repo)

    rows = []
    for line in repo.list_lines():
        lr = results[line.line_id]
        rows.append(
            {
                "row_type": "line",
                "line_id": line.line_id,
                "component_id": "",
                "parent_line_id": line.parent_line_id or "",
                "line_name": line.line_name,
                "cost_type": "",
                "cost_method": line.cost_method or "",
                "lump_sum_basis": line.lump_sum_basis or "",
                "confidence": confidence_for_basis(line.lump_sum_basis) or "",
                "amount": line.amount if line.amount is not None else "",
                "quantity": line.quantity if line.quantity is not None else "",
                "unit_of_measure": line.unit_of_measure or "",
                "unit_rate": line.unit_rate if line.unit_rate is not None else "",
                "basis_line_ref": line.basis_line_ref or "",
                "ref_type": "",
                "percentage_rate": line.percentage_rate if line.percentage_rate is not None else "",
                "own_cost": lr.own_cost if lr.own_cost is not None else "",
                "rolled_up_cost": lr.rolled_up_cost if lr.rolled_up_cost is not None else "",
                "resolved": lr.resolved,
                "issues": "; ".join(lr.issues),
            }
        )
        for comp in line.cost_components:
            cr = lr.component_results.get(comp.component_id)
            rows.append(
                {
                    "row_type": "component",
                    "line_id": line.line_id,
                    "component_id": comp.component_id,
                    "parent_line_id": line.line_id,
                    "line_name": line.line_name,
                    "cost_type": comp.cost_type,
                    "cost_method": comp.cost_method,
                    "lump_sum_basis": comp.lump_sum_basis or "",
                    "confidence": confidence_for_basis(comp.lump_sum_basis) or "",
                    "amount": comp.amount if comp.amount is not None else "",
                    "quantity": comp.quantity if comp.quantity is not None else "",
                    "unit_of_measure": comp.unit_of_measure or "",
                    "unit_rate": comp.unit_rate if comp.unit_rate is not None else "",
                    "basis_line_ref": comp.basis_line_ref or "",
                    "ref_type": comp.ref_type or "",
                    "percentage_rate": comp.percentage_rate if comp.percentage_rate is not None else "",
                    "own_cost": cr.calculated_cost if cr and cr.calculated_cost is not None else "",
                    "rolled_up_cost": "",
                    "resolved": cr.resolved if cr else False,
                    "issues": "; ".join(cr.issues) if cr else "",
                }
            )

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    click.echo(f"Exported {len(rows)} row(s) to {output}")


def main() -> None:
    try:
        cli(obj={})
    except RepositoryError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
