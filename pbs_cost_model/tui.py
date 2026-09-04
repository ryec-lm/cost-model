"""Interactive terminal UI for browsing and editing the PBS cost tree.

Built on Textual. Every mutation goes through the same storage.py /
operations.py / calc.py / validation.py layers the CLI uses - this module
only adds a persistent, navigable front end on top of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, Tree
from textual.widgets.tree import TreeNode

from .calc import CostCalculator, ComponentResult, LineResult
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
    reassign_children_and_delete_line,
    validate_new_parent,
)
from .storage import JSONRepository, next_component_id, next_line_id
from .validation import validate_tree

LUMP_SUM_BASES = ["quote", "historical", "analogous", "allowance"]
LINE_METHODS = [m.value for m in CostMethod] + ["none"]
COMPONENT_METHODS = [m.value for m in CostMethod if m != CostMethod.FIRST_PRINCIPLES]
COST_TYPES = [t.value for t in CostType]
REF_TYPES = [r.value for r in RefType]


def _f(value: str, label: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise OperationError(f"'{label}' must be a number, got '{value}'")


def _method_tag(result: LineResult) -> str:
    if result.method is None:
        return "rollup" if result.has_children else "empty"
    return result.method


def _format_cost(resolved: bool, cost: Optional[float], reason: Optional[str]) -> str:
    if not resolved:
        return f"UNRESOLVED ({reason})"
    return f"${cost:,.2f}"


def _line_label(line: PBSLine, result: LineResult) -> str:
    flag = "  [!]" if result.leaf_equivalent_with_children else ""
    cost = _format_cost(result.resolved, result.cost, result.reason)
    return f"{line.line_id} [{_method_tag(result)}] {line.line_name} - {cost}{flag}"


def _component_label(comp: CostComponent, result: ComponentResult) -> str:
    cost = _format_cost(result.resolved, result.cost, result.reason)
    return f"{comp.component_id} ({comp.cost_type}/{comp.cost_method}) - {cost}"


# --------------------------------------------------------------------------
# Modal screens
# --------------------------------------------------------------------------


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No confirmation."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 60; padding: 1 2; border: thick $primary; background: $surface; }
    #buttons { align: center middle; height: auto; margin-top: 1; }
    Button { margin: 0 1; }
    """

    def __init__(self, message: str, danger: bool = False):
        super().__init__()
        self.message = message
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.message)
            with Horizontal(id="buttons"):
                yield Button("Yes", variant="error" if self.danger else "primary", id="yes")
                yield Button("No", id="no")

    @on(Button.Pressed, "#yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def cancel(self) -> None:
        self.dismiss(False)


class MessageScreen(ModalScreen[None]):
    """Scrollable read-only message (used for validate output)."""

    CSS = """
    MessageScreen { align: center middle; }
    #dialog { width: 90%; height: auto; max-height: 80%; padding: 1 2; border: thick $primary; background: $surface; }
    #body { height: auto; max-height: 100%; }
    #buttons { align: center middle; height: auto; margin-top: 1; }
    """

    def __init__(self, title: str, body: str):
        super().__init__()
        self.title_text = title
        self.body_text = body

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text)
            with VerticalScroll(id="body"):
                yield Static(self.body_text, markup=False)
            with Horizontal(id="buttons"):
                yield Button("Close", id="close")

    @on(Button.Pressed, "#close")
    def close(self) -> None:
        self.dismiss(None)


class TextPromptScreen(ModalScreen[Optional[str]]):
    """Single text-field prompt (used for export path)."""

    CSS = """
    TextPromptScreen { align: center middle; }
    #dialog { width: 60; padding: 1 2; border: thick $primary; background: $surface; }
    #buttons { align: center middle; height: auto; margin-top: 1; }
    """

    def __init__(self, prompt: str, default: str = ""):
        super().__init__()
        self.prompt = prompt
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.prompt)
            yield Input(value=self.default, id="value")
            with Horizontal(id="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#ok")
    @on(Input.Submitted)
    def submit(self) -> None:
        self.dismiss(self.query_one("#value", Input).value)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class RemoveChildrenScreen(ModalScreen[Optional[dict]]):
    """Choose how to handle a line's children before removing it."""

    CSS = """
    RemoveChildrenScreen { align: center middle; }
    #dialog { width: 70; padding: 1 2; border: thick $primary; background: $surface; }
    #buttons { align: center middle; height: auto; margin-top: 1; }
    Button { margin: 0 1; }
    """

    def __init__(self, line_id: str, children: list, default_parent: Optional[str]):
        super().__init__()
        self.line_id = line_id
        self.children = children
        self.default_parent = default_parent or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"Line '{self.line_id}' has {len(self.children)} child line(s): "
                f"{', '.join(self.children)}"
            )
            yield Label("Reassign children to line_id (blank = this line's own parent):")
            yield Input(value=self.default_parent, id="reassign_to")
            with Horizontal(id="buttons"):
                yield Button("Reassign && Remove", variant="primary", id="reassign")
                yield Button("Cascade Delete", variant="error", id="cascade")
                yield Button("Abort", id="abort")

    @on(Button.Pressed, "#reassign")
    def reassign(self) -> None:
        new_parent = self.query_one("#reassign_to", Input).value or None
        self.dismiss({"action": "reassign", "new_parent": new_parent})

    @on(Button.Pressed, "#cascade")
    def cascade(self) -> None:
        self.dismiss({"action": "cascade"})

    @on(Button.Pressed, "#abort")
    def abort(self) -> None:
        self.dismiss(None)


class LineFormScreen(ModalScreen[Optional[dict]]):
    """Add/edit a PBS line. Returns a dict of field values, or None if cancelled."""

    CSS = """
    LineFormScreen { align: center middle; }
    #dialog { width: 70; height: auto; max-height: 90%; padding: 1 2; border: thick $primary; background: $surface; }
    #error { color: $error; height: auto; }
    #buttons { align: center middle; height: auto; margin-top: 1; }
    .field { margin-top: 1; }
    """

    def __init__(self, title: str, line: Optional[PBSLine] = None, parent_default: Optional[str] = None):
        super().__init__()
        self.title_text = title
        self.line = line
        self.parent_default = parent_default or (line.parent_line_id if line else None) or ""

    def compose(self) -> ComposeResult:
        line = self.line
        with VerticalScroll(id="dialog"):
            yield Label(self.title_text)
            yield Label("Name")
            yield Input(value=line.line_name if line else "", id="name")
            yield Label("Parent line_id (blank for root)")
            yield Input(value=self.parent_default, id="parent")
            yield Label("Cost method")
            yield Select(
                [(m, m) for m in LINE_METHODS],
                value=(line.cost_method if line and line.cost_method else "none"),
                id="cost_method",
                allow_blank=False,
            )

            with Vertical(id="fields_lump_sum", classes="field"):
                yield Label("Lump sum basis")
                yield Select(
                    [(b, b) for b in LUMP_SUM_BASES],
                    value=(line.lump_sum_basis if line and line.lump_sum_basis else LUMP_SUM_BASES[0]),
                    id="lump_sum_basis",
                    allow_blank=False,
                )
                yield Label("Amount ($)")
                yield Input(value=str(line.amount) if line and line.amount is not None else "", id="amount")

            with Vertical(id="fields_parametric", classes="field"):
                yield Label("Quantity")
                yield Input(value=str(line.quantity) if line and line.quantity is not None else "", id="quantity")
                yield Label("Unit of measure (e.g. LF, SF, EA)")
                yield Input(value=line.unit_of_measure if line and line.unit_of_measure else "", id="unit_of_measure")
                yield Label("Unit rate ($/unit)")
                yield Input(value=str(line.unit_rate) if line and line.unit_rate is not None else "", id="unit_rate")

            with Vertical(id="fields_percentage", classes="field"):
                yield Label("Basis line_id (must be a rollup with children)")
                yield Input(value=line.basis_line_ref if line and line.basis_line_ref else "", id="basis_line_ref")
                yield Label("Percentage rate (decimal, e.g. 0.05 for 5%)")
                yield Input(
                    value=str(line.percentage_rate) if line and line.percentage_rate is not None else "",
                    id="percentage_rate",
                )

            yield Static("", id="error", markup=False)
            with Horizontal(id="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._sync_visible_fields(self.query_one("#cost_method", Select).value)

    @on(Select.Changed, "#cost_method")
    def method_changed(self, event: Select.Changed) -> None:
        self._sync_visible_fields(event.value)

    def _sync_visible_fields(self, method: str) -> None:
        self.query_one("#fields_lump_sum").display = method == "lump_sum"
        self.query_one("#fields_parametric").display = method == "parametric"
        self.query_one("#fields_percentage").display = method == "percentage"

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        try:
            result = self._collect()
        except OperationError as e:
            self.query_one("#error", Static).update(str(e))
            return
        self.dismiss(result)

    def _collect(self) -> dict:
        name = self.query_one("#name", Input).value.strip()
        if not name:
            raise OperationError("Name is required")
        parent = self.query_one("#parent", Input).value.strip() or None
        method = self.query_one("#cost_method", Select).value
        method = None if method == "none" else method

        result = {
            "line_name": name,
            "parent_line_id": parent,
            "cost_method": method,
            "lump_sum_basis": None,
            "amount": None,
            "quantity": None,
            "unit_of_measure": None,
            "unit_rate": None,
            "basis_line_ref": None,
            "percentage_rate": None,
        }

        if method == CostMethod.LUMP_SUM.value:
            result["lump_sum_basis"] = self.query_one("#lump_sum_basis", Select).value
            amount = self.query_one("#amount", Input).value.strip()
            if not amount:
                raise OperationError("Amount is required for cost_method lump_sum")
            result["amount"] = _f(amount, "Amount")
        elif method == CostMethod.PARAMETRIC.value:
            qty = self.query_one("#quantity", Input).value.strip()
            unit = self.query_one("#unit_of_measure", Input).value.strip()
            rate = self.query_one("#unit_rate", Input).value.strip()
            if not qty or not unit or not rate:
                raise OperationError("Quantity, unit, and unit rate are all required for parametric")
            result["quantity"] = _f(qty, "Quantity")
            result["unit_of_measure"] = unit
            result["unit_rate"] = _f(rate, "Unit rate")
        elif method == CostMethod.PERCENTAGE.value:
            basis = self.query_one("#basis_line_ref", Input).value.strip()
            rate = self.query_one("#percentage_rate", Input).value.strip()
            if not basis or not rate:
                raise OperationError("Basis line and rate are both required for percentage")
            result["basis_line_ref"] = basis
            result["percentage_rate"] = _f(rate, "Percentage rate")

        return result


class ComponentFormScreen(ModalScreen[Optional[dict]]):
    """Add/edit a cost_component under a first_principles line."""

    CSS = LineFormScreen.CSS

    def __init__(self, title: str, comp: Optional[CostComponent] = None):
        super().__init__()
        self.title_text = title
        self.comp = comp

    def compose(self) -> ComposeResult:
        comp = self.comp
        with VerticalScroll(id="dialog"):
            yield Label(self.title_text)
            yield Label("Cost type")
            yield Select(
                [(t, t) for t in COST_TYPES],
                value=(comp.cost_type if comp else COST_TYPES[0]),
                id="cost_type",
                allow_blank=False,
            )
            yield Label("Cost method")
            yield Select(
                [(m, m) for m in COMPONENT_METHODS],
                value=(comp.cost_method if comp else COMPONENT_METHODS[0]),
                id="cost_method",
                allow_blank=False,
            )

            with Vertical(id="fields_lump_sum", classes="field"):
                yield Label("Lump sum basis")
                yield Select(
                    [(b, b) for b in LUMP_SUM_BASES],
                    value=(comp.lump_sum_basis if comp and comp.lump_sum_basis else LUMP_SUM_BASES[0]),
                    id="lump_sum_basis",
                    allow_blank=False,
                )
                yield Label("Amount ($)")
                yield Input(value=str(comp.amount) if comp and comp.amount is not None else "", id="amount")

            with Vertical(id="fields_parametric", classes="field"):
                yield Label("Quantity")
                yield Input(value=str(comp.quantity) if comp and comp.quantity is not None else "", id="quantity")
                yield Label("Unit of measure")
                yield Input(value=comp.unit_of_measure if comp and comp.unit_of_measure else "", id="unit_of_measure")
                yield Label("Unit rate ($/unit)")
                yield Input(value=str(comp.unit_rate) if comp and comp.unit_rate is not None else "", id="unit_rate")

            with Vertical(id="fields_percentage", classes="field"):
                yield Label("Reference type")
                yield Select(
                    [(r, r) for r in REF_TYPES],
                    value=(comp.ref_type if comp and comp.ref_type else REF_TYPES[0]),
                    id="ref_type",
                    allow_blank=False,
                )
                yield Label("Basis ref (line_id, or sibling component_id)")
                yield Input(value=comp.basis_ref if comp and comp.basis_ref else "", id="basis_ref")
                yield Label("Percentage rate (decimal)")
                yield Input(
                    value=str(comp.percentage_rate) if comp and comp.percentage_rate is not None else "",
                    id="percentage_rate",
                )

            yield Static("", id="error", markup=False)
            with Horizontal(id="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._sync_visible_fields(self.query_one("#cost_method", Select).value)

    @on(Select.Changed, "#cost_method")
    def method_changed(self, event: Select.Changed) -> None:
        self._sync_visible_fields(event.value)

    def _sync_visible_fields(self, method: str) -> None:
        self.query_one("#fields_lump_sum").display = method == "lump_sum"
        self.query_one("#fields_parametric").display = method == "parametric"
        self.query_one("#fields_percentage").display = method == "percentage"

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        try:
            result = self._collect()
        except OperationError as e:
            self.query_one("#error", Static).update(str(e))
            return
        self.dismiss(result)

    def _collect(self) -> dict:
        cost_type = self.query_one("#cost_type", Select).value
        method = self.query_one("#cost_method", Select).value

        result = {
            "cost_type": cost_type,
            "cost_method": method,
            "lump_sum_basis": None,
            "amount": None,
            "quantity": None,
            "unit_of_measure": None,
            "unit_rate": None,
            "ref_type": None,
            "basis_ref": None,
            "percentage_rate": None,
        }

        if method == CostMethod.LUMP_SUM.value:
            result["lump_sum_basis"] = self.query_one("#lump_sum_basis", Select).value
            amount = self.query_one("#amount", Input).value.strip()
            if not amount:
                raise OperationError("Amount is required for cost_method lump_sum")
            result["amount"] = _f(amount, "Amount")
        elif method == CostMethod.PARAMETRIC.value:
            qty = self.query_one("#quantity", Input).value.strip()
            unit = self.query_one("#unit_of_measure", Input).value.strip()
            rate = self.query_one("#unit_rate", Input).value.strip()
            if not qty or not unit or not rate:
                raise OperationError("Quantity, unit, and unit rate are all required for parametric")
            result["quantity"] = _f(qty, "Quantity")
            result["unit_of_measure"] = unit
            result["unit_rate"] = _f(rate, "Unit rate")
        elif method == CostMethod.PERCENTAGE.value:
            ref_type = self.query_one("#ref_type", Select).value
            basis_ref = self.query_one("#basis_ref", Input).value.strip()
            rate = self.query_one("#percentage_rate", Input).value.strip()
            if not basis_ref or not rate:
                raise OperationError("Basis ref and rate are both required for percentage")
            result["ref_type"] = ref_type
            result["basis_ref"] = basis_ref
            result["percentage_rate"] = _f(rate, "Percentage rate")

        return result


# --------------------------------------------------------------------------
# Main app
# --------------------------------------------------------------------------


class PBSApp(App[None]):
    """Browse and edit the PBS cost tree."""

    TITLE = "PBS Cost Model"

    CSS = """
    #body { height: 1fr; }
    #tree { width: 55%; border-right: solid $primary-background; }
    #detail { width: 45%; padding: 1 2; }
    #status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("a", "add_line", "Add line"),
        Binding("shift+a", "add_component", "Add component"),
        Binding("e", "edit_selected", "Edit"),
        Binding("d", "remove_selected", "Remove"),
        Binding("v", "validate", "Validate"),
        Binding("x", "export", "Export CSV"),
        Binding("r", "reload", "Reload"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, file_path: Union[str, Path]):
        super().__init__()
        self.repo = JSONRepository(file_path)
        self.lines = self.repo.load()
        self.calculator = CostCalculator(self.lines)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Tree("PBS Cost Model", id="tree")
            yield Static("Select a line or component to see its detail.", id="detail", markup=False)
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_tree()

    # -- persistence -------------------------------------------------

    def save(self) -> None:
        self.repo.save(self.lines)

    def set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.update(message)
        status.set_class(error, "-error")

    # -- tree rendering ------------------------------------------------

    def refresh_tree(self, select_line_id: Optional[str] = None) -> None:
        self.calculator = CostCalculator(self.lines)
        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.data = None
        for root_id in root_lines(self.lines):
            self._add_line_node(tree.root, root_id)
        tree.root.expand_all()
        self.update_detail(None)

    def _add_line_node(self, parent_node: TreeNode, line_id: str) -> None:
        line = self.lines[line_id]
        result = self.calculator.calculate_line(line_id)
        # Text(), not a plain str, so literal "[method]" tags aren't parsed as Rich markup.
        node = parent_node.add(
            Text(_line_label(line, result)), data={"type": "line", "line_id": line_id}
        )
        for comp in line.cost_components:
            comp_result = self.calculator.calculate_component(line, comp)
            node.add_leaf(
                Text(_component_label(comp, comp_result)),
                data={"type": "component", "line_id": line_id, "component_id": comp.component_id},
            )
        for child_id in children_of(self.lines, line_id):
            self._add_line_node(node, child_id)

    @on(Tree.NodeHighlighted, "#tree")
    def node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self.update_detail(event.node.data)

    def update_detail(self, data: Optional[dict]) -> None:
        detail = self.query_one("#detail", Static)
        if not data:
            detail.update("Select a line or component to see its detail.")
            return
        if data["type"] == "line":
            detail.update(self._render_line_detail(data["line_id"]))
        else:
            detail.update(self._render_component_detail(data["line_id"], data["component_id"]))

    def _render_line_detail(self, line_id: str) -> str:
        line = self.lines[line_id]
        result = self.calculator.calculate_line(line_id)
        kids = children_of(self.lines, line_id)
        lines_out = [
            f"line_id:         {line.line_id}",
            f"line_name:       {line.line_name}",
            f"parent_line_id:  {line.parent_line_id or '(root)'}",
            f"cost_method:     {line.cost_method or '(none - rollup)'}",
            f"children:        {', '.join(kids) if kids else '(none)'}",
        ]
        if result.leaf_equivalent_with_children:
            lines_out.append("")
            lines_out.append("[!] has both a cost_method and children -")
            lines_out.append("    children are NOT rolled up into this line")

        if line.cost_method == CostMethod.LUMP_SUM.value:
            lines_out += [
                "",
                f"lump_sum_basis:  {line.lump_sum_basis}",
                f"confidence:      {confidence_for_basis(line.lump_sum_basis)}",
                f"amount:          {line.amount}",
            ]
        elif line.cost_method == CostMethod.PARAMETRIC.value:
            lines_out += [
                "",
                f"quantity:        {line.quantity}",
                f"unit_of_measure: {line.unit_of_measure}",
                f"unit_rate:       {line.unit_rate}",
            ]
        elif line.cost_method == CostMethod.PERCENTAGE.value:
            lines_out += [
                "",
                f"basis_line_ref:  {line.basis_line_ref}",
                f"percentage_rate: {line.percentage_rate}",
            ]
        elif line.cost_method == CostMethod.FIRST_PRINCIPLES.value:
            lines_out.append("")
            lines_out.append(f"cost_components: {len(line.cost_components)}")

        lines_out.append("")
        lines_out.append(f"rolled_up_cost:  {_format_cost(result.resolved, result.cost, result.reason)}")
        return "\n".join(lines_out)

    def _render_component_detail(self, line_id: str, component_id: str) -> str:
        line = self.lines[line_id]
        comp = next(c for c in line.cost_components if c.component_id == component_id)
        result = self.calculator.calculate_component(line, comp)
        lines_out = [
            f"line_id:         {line_id}",
            f"component_id:    {comp.component_id}",
            f"cost_type:       {comp.cost_type}",
            f"cost_method:     {comp.cost_method}",
        ]
        if comp.cost_method == CostMethod.LUMP_SUM.value:
            lines_out += [
                f"lump_sum_basis:  {comp.lump_sum_basis}",
                f"confidence:      {confidence_for_basis(comp.lump_sum_basis)}",
                f"amount:          {comp.amount}",
            ]
        elif comp.cost_method == CostMethod.PARAMETRIC.value:
            lines_out += [
                f"quantity:        {comp.quantity}",
                f"unit_of_measure: {comp.unit_of_measure}",
                f"unit_rate:       {comp.unit_rate}",
            ]
        elif comp.cost_method == CostMethod.PERCENTAGE.value:
            lines_out += [
                f"ref_type:        {comp.ref_type}",
                f"basis_ref:       {comp.basis_ref}",
                f"percentage_rate: {comp.percentage_rate}",
            ]
        lines_out.append("")
        lines_out.append(f"cost:            {_format_cost(result.resolved, result.cost, result.reason)}")
        return "\n".join(lines_out)

    # -- selection helpers ------------------------------------------------

    def _selected_data(self) -> Optional[dict]:
        node = self.query_one("#tree", Tree).cursor_node
        return node.data if node else None

    # -- actions ------------------------------------------------

    @work
    async def action_add_line(self) -> None:
        data = self._selected_data()
        parent_default = data["line_id"] if data and data["type"] == "line" else None
        result = await self.push_screen_wait(LineFormScreen("Add line", parent_default=parent_default))
        if result is None:
            return
        parent = result["parent_line_id"]
        if parent is not None and parent not in self.lines:
            self.set_status(f"Error: parent line '{parent}' not found", error=True)
            return
        line_id = next_line_id(self.lines)
        self.lines[line_id] = PBSLine(line_id=line_id, **result)
        self.save()
        self.refresh_tree()
        self.set_status(f"Added line {line_id}")

    @work
    async def action_edit_selected(self) -> None:
        data = self._selected_data()
        if data is None:
            self.set_status("Select a line or component first", error=True)
            return
        if data["type"] == "line":
            await self._edit_line(data["line_id"])
        else:
            await self._edit_component(data["line_id"], data["component_id"])

    async def _edit_line(self, line_id: str) -> None:
        line = self.lines[line_id]
        result = await self.push_screen_wait(LineFormScreen(f"Edit line {line_id}", line=line))
        if result is None:
            return
        try:
            validate_new_parent(self.lines, line_id, result["parent_line_id"])
        except OperationError as e:
            self.set_status(f"Error: {e}", error=True)
            return
        for key, value in result.items():
            setattr(line, key, value)
        self.save()
        self.refresh_tree()
        self.set_status(f"Updated line {line_id}")

    async def _edit_component(self, line_id: str, component_id: str) -> None:
        line = self.lines[line_id]
        comp = next(c for c in line.cost_components if c.component_id == component_id)
        result = await self.push_screen_wait(ComponentFormScreen(f"Edit component {component_id}", comp=comp))
        if result is None:
            return
        for key, value in result.items():
            setattr(comp, key, value)
        self.save()
        self.refresh_tree()
        self.set_status(f"Updated component {component_id}")

    @work
    async def action_add_component(self) -> None:
        data = self._selected_data()
        if data is None or data["type"] != "line":
            self.set_status("Select a first_principles line first", error=True)
            return
        line = self.lines[data["line_id"]]
        if line.cost_method != CostMethod.FIRST_PRINCIPLES.value:
            self.set_status(f"Line '{line.line_id}' is not cost_method=first_principles", error=True)
            return
        result = await self.push_screen_wait(ComponentFormScreen(f"Add component to {line.line_id}"))
        if result is None:
            return
        component_id = next_component_id(line)
        line.cost_components.append(CostComponent(component_id=component_id, **result))
        self.save()
        self.refresh_tree()
        self.set_status(f"Added component {component_id} to line {line.line_id}")

    @work
    async def action_remove_selected(self) -> None:
        data = self._selected_data()
        if data is None:
            self.set_status("Select a line or component first", error=True)
            return
        if data["type"] == "component":
            await self._remove_component(data["line_id"], data["component_id"])
        else:
            await self._remove_line(data["line_id"])

    async def _remove_component(self, line_id: str, component_id: str) -> None:
        confirmed = await self.push_screen_wait(
            ConfirmScreen(f"Remove component '{component_id}' from line '{line_id}'?", danger=True)
        )
        if not confirmed:
            return
        line = self.lines[line_id]
        line.cost_components = [c for c in line.cost_components if c.component_id != component_id]
        self.save()
        self.refresh_tree()
        self.set_status(f"Removed component {component_id} from line {line_id}")

    async def _remove_line(self, line_id: str) -> None:
        line = self.lines[line_id]
        kids = children_of(self.lines, line_id)

        if kids:
            choice = await self.push_screen_wait(
                RemoveChildrenScreen(line_id, kids, default_parent=line.parent_line_id)
            )
            if choice is None:
                return
            try:
                if choice["action"] == "cascade":
                    confirmed = await self.push_screen_wait(
                        ConfirmScreen(f"Delete '{line_id}' and its entire subtree?", danger=True)
                    )
                    if not confirmed:
                        return
                    cascade_delete_line(self.lines, line_id)
                else:
                    reassign_children_and_delete_line(self.lines, line_id, choice["new_parent"])
            except OperationError as e:
                self.set_status(f"Error: {e}", error=True)
                return
        else:
            confirmed = await self.push_screen_wait(ConfirmScreen(f"Remove line '{line_id}'?", danger=True))
            if not confirmed:
                return
            del self.lines[line_id]

        self.save()
        self.refresh_tree()
        self.set_status(f"Removed line {line_id}")

    @work
    async def action_validate(self) -> None:
        report = validate_tree(self.lines)
        parts = []
        if report.errors:
            parts.append(f"ERRORS ({len(report.errors)}):\n" + "\n".join(f"  [E] {e}" for e in report.errors))
        if report.warnings:
            parts.append(f"WARNINGS ({len(report.warnings)}):\n" + "\n".join(f"  [W] {w}" for w in report.warnings))
        if report.incomplete:
            parts.append(f"INCOMPLETE ({len(report.incomplete)}):\n" + "\n".join(f"  [I] {i}" for i in report.incomplete))
        body = "\n\n".join(parts) if parts else "OK - no issues found."
        await self.push_screen_wait(MessageScreen("Validation results", body))

    @work
    async def action_export(self) -> None:
        path = await self.push_screen_wait(TextPromptScreen("Export to CSV path:", default="export.csv"))
        if not path:
            return
        export_csv(self.lines, path)
        self.set_status(f"Exported {len(self.lines)} line(s) to {path}")

    def action_reload(self) -> None:
        self.lines = self.repo.load()
        self.refresh_tree()
        self.set_status("Reloaded from disk")


def run_tui(file_path: Union[str, Path]) -> None:
    PBSApp(file_path).run()
