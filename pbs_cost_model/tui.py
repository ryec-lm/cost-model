"""Interactive terminal UI for browsing and editing the PBS cost tree.

Built on Textual as a single scrollable, spreadsheet-like table: every line
and cost_component is an always-editable row (no popup forms for routine
edits - just modals for the rarer, destructive/report actions: removing a
line with children, validate, export). Every mutation goes through the
same storage.py / operations.py / calc.py / validation.py layers the CLI
uses - this module only adds a persistent, navigable front end on top of
them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static

from .calc import CostCalculator, ComponentResult, LineResult
from .export import export_csv
from .models import (
    CostComponent,
    CostMethod,
    CostType,
    PBSLine,
    RefType,
    children_of,
    root_lines,
)
from .operations import (
    OperationError,
    cascade_delete_line,
    move_line,
    reassign_children_and_delete_line,
)
from .storage import JSONRepository, next_component_id, next_line_id, next_sort_index
from .validation import validate_tree
from .wbs import compute_wbs_numbers, display_wbs

LUMP_SUM_BASES = ["quote", "historical", "analogous", "allowance"]
LINE_METHOD_OPTIONS = [(m.value, m.value) for m in CostMethod] + [("none", "none")]
COMPONENT_METHOD_OPTIONS = [(m.value, m.value) for m in CostMethod if m != CostMethod.FIRST_PRINCIPLES]
COST_TYPE_OPTIONS = [(t.value, t.value) for t in CostType]
REF_TYPE_OPTIONS = [(r.value, r.value) for r in RefType]

TEXT_FIELDS = {"unit_of_measure", "basis_line_ref", "basis_ref"}
FLOAT_FIELDS = {"amount", "quantity", "unit_rate", "percentage_rate"}


class FieldSpec:
    __slots__ = ("key", "label", "kind")

    def __init__(self, key: str, label: str, kind: str):
        self.key = key
        self.label = label
        self.kind = kind


LINE_FIELDS = {
    CostMethod.LUMP_SUM.value: [
        FieldSpec("lump_sum_basis", "Basis", "basis_select"),
        FieldSpec("amount", "Amount ($)", "float"),
    ],
    CostMethod.PARAMETRIC.value: [
        FieldSpec("quantity", "Quantity", "float"),
        FieldSpec("unit_of_measure", "Unit", "text"),
        FieldSpec("unit_rate", "Unit rate ($/unit)", "float"),
    ],
    CostMethod.PERCENTAGE.value: [
        FieldSpec("basis_line_ref", "Basis line_id", "text"),
        FieldSpec("percentage_rate", "Rate (decimal)", "float"),
    ],
}
COMPONENT_FIELDS = {
    CostMethod.LUMP_SUM.value: LINE_FIELDS[CostMethod.LUMP_SUM.value],
    CostMethod.PARAMETRIC.value: LINE_FIELDS[CostMethod.PARAMETRIC.value],
    CostMethod.PERCENTAGE.value: [
        FieldSpec("ref_type", "Ref type", "reftype_select"),
        FieldSpec("basis_ref", "Basis ref (line_id or sibling component_id)", "text"),
        FieldSpec("percentage_rate", "Rate (decimal)", "float"),
    ],
}


def _parse_float_or_none(value: str) -> Optional[float]:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _format_cost(resolved: bool, cost: Optional[float]) -> str:
    if not resolved:
        return "unresolved"
    return f"${cost:,.2f}"


def _make_field_row(spec: FieldSpec, obj, field_owner) -> Horizontal:
    value = getattr(obj, spec.key)
    if spec.kind == "basis_select":
        widget = Select(
            [(b, b) for b in LUMP_SUM_BASES],
            value=(value if value in LUMP_SUM_BASES else LUMP_SUM_BASES[0]),
            allow_blank=False,
            compact=True,
            classes="field-input",
        )
    elif spec.kind == "reftype_select":
        widget = Select(
            [(r.value, r.value) for r in RefType],
            value=(value if value in [r.value for r in RefType] else RefType.LINE.value),
            allow_blank=False,
            compact=True,
            classes="field-input",
        )
    elif spec.kind == "float":
        widget = Input(value=("" if value is None else str(value)), compact=True, classes="field-input")
    else:
        widget = Input(value=(value or ""), compact=True, classes="field-input")
    widget.field_key = spec.key
    widget.field_owner = field_owner
    return Horizontal(Label(spec.label, classes="field-label"), widget, classes="field-row")


class TotalBar(Static):
    """Docked bar showing the grand total across all root lines."""


# --------------------------------------------------------------------------
# Row widgets
# --------------------------------------------------------------------------


class ComponentRow(Vertical):
    """One cost_component row, nested under its first_principles line."""

    can_focus = True

    def __init__(self, app_ref: "PBSApp", line_id: str, component_id: str, depth: int):
        super().__init__(classes="row component-row")
        self.app_ref = app_ref
        self.line_id = line_id
        self.component_id = component_id
        self.depth = depth

    def _comp(self) -> CostComponent:
        line = self.app_ref.lines[self.line_id]
        return next(c for c in line.cost_components if c.component_id == self.component_id)

    def compose(self) -> ComposeResult:
        comp = self._comp()
        indent = Static("")
        indent.styles.width = self.depth * 2 + 3
        id_label = Static(comp.component_id, classes="name-cell")
        cost_type = Select(
            COST_TYPE_OPTIONS, value=comp.cost_type, allow_blank=False, compact=True, classes="cost-type-select"
        )
        cost_type.field_key = "cost_type"
        cost_type.field_owner = self
        method = Select(
            COMPONENT_METHOD_OPTIONS,
            value=comp.cost_method,
            allow_blank=False,
            compact=True,
            classes="method-select",
        )
        method.field_key = "cost_method"
        method.field_owner = self
        yield Horizontal(
            indent, id_label, cost_type, method, Static("", classes="cost-label"), classes="row-header"
        )
        yield Vertical(*self._field_rows(comp), classes="fields")

    def _field_rows(self, comp: CostComponent):
        return [_make_field_row(spec, comp, self) for spec in COMPONENT_FIELDS.get(comp.cost_method, [])]

    async def rebuild_fields(self) -> None:
        comp = self._comp()
        fields = self.query_one(".fields", Vertical)
        await fields.remove_children()
        await fields.mount_all(self._field_rows(comp))

    def refresh_cost(self, calculator: CostCalculator) -> None:
        line = self.app_ref.lines[self.line_id]
        comp = self._comp()
        result = calculator.calculate_component(line, comp)
        label = self.query_one(".cost-label", Static)
        label.update(_format_cost(result.resolved, result.cost))
        label.set_class(not result.resolved, "-unresolved")

    def first_edit_target(self):
        return self.query_one(".cost-type-select", Select)

    @on(Select.Changed)
    async def _select_changed(self, event: Select.Changed) -> None:
        event.stop()
        key = getattr(event.select, "field_key", None)
        if key is None:
            return
        comp = self._comp()
        setattr(comp, key, event.value)
        if key == "cost_method":
            await self.rebuild_fields()
        self.app_ref.on_data_changed()

    @on(Input.Changed)
    def _input_changed(self, event: Input.Changed) -> None:
        event.stop()
        key = getattr(event.input, "field_key", None)
        if key is None:
            return
        comp = self._comp()
        if key in FLOAT_FIELDS:
            setattr(comp, key, _parse_float_or_none(event.value))
        else:
            setattr(comp, key, event.value.strip() or None)
        self.app_ref.on_data_changed()


class LineRow(Vertical):
    """One PBS line row, with children (lines and components) as sibling
    rows below it in the flattened table, indented further."""

    can_focus = True

    def __init__(self, app_ref: "PBSApp", line_id: str, depth: int):
        super().__init__(classes="row line-row")
        self.app_ref = app_ref
        self.line_id = line_id
        self.depth = depth

    def _line(self) -> PBSLine:
        return self.app_ref.lines[self.line_id]

    def compose(self) -> ComposeResult:
        line = self._line()
        has_kids = bool(children_of(self.app_ref.lines, self.line_id)) or bool(line.cost_components)

        indent = Static("")
        indent.styles.width = self.depth * 2

        if has_kids:
            toggle = Button(
                "v" if self.line_id not in self.app_ref.collapsed else ">",
                compact=True,
                classes="toggle-btn",
            )
        else:
            toggle = Static("", classes="toggle-btn")

        wbs = Input(
            value=display_wbs(line, self.app_ref.wbs_numbers),
            compact=True,
            classes="wbs-cell",
        )
        wbs.field_key = "wbs_override"
        wbs.field_owner = self

        name = Input(value=line.line_name, compact=True, classes="name-cell")
        name.field_key = "line_name"
        name.field_owner = self

        method = Select(
            LINE_METHOD_OPTIONS,
            value=(line.cost_method or "none"),
            allow_blank=False,
            compact=True,
            classes="method-select",
        )
        method.field_key = "cost_method"
        method.field_owner = self

        flag = Static("", classes="flag")

        yield Horizontal(
            toggle, indent, wbs, name, method, Static("", classes="cost-label"), flag, classes="row-header"
        )
        yield Vertical(*self._field_rows(line), classes="fields")

    def _field_rows(self, line: PBSLine):
        return [_make_field_row(spec, line, self) for spec in LINE_FIELDS.get(line.cost_method, [])]

    async def rebuild_fields(self) -> None:
        line = self._line()
        fields = self.query_one(".fields", Vertical)
        await fields.remove_children()
        await fields.mount_all(self._field_rows(line))

    def refresh_cost(self, calculator: CostCalculator) -> None:
        result = calculator.calculate_line(self.line_id)
        label = self.query_one(".cost-label", Static)
        label.update(_format_cost(result.resolved, result.cost))
        label.set_class(not result.resolved, "-unresolved")
        flag = self.query_one(".flag", Static)
        flag.update("!" if result.leaf_equivalent_with_children else "")

        wbs_input = self.query_one(".wbs-cell", Input)
        computed = display_wbs(self._line(), self.app_ref.wbs_numbers)
        if not wbs_input.has_focus and wbs_input.value != computed:
            # Setting .value fires Input.Changed same as a real keystroke would;
            # mark it so _input_changed doesn't treat this refresh as the user
            # having typed the auto-computed number (which would pin it).
            wbs_input._programmatic_update = True
            wbs_input.value = computed

    def first_edit_target(self):
        return self.query_one(".name-cell", Input)

    @on(Button.Pressed, ".toggle-btn")
    async def _toggle_pressed(self) -> None:
        await self.app_ref.toggle_collapse(self.line_id)

    @on(Select.Changed)
    async def _select_changed(self, event: Select.Changed) -> None:
        event.stop()
        key = getattr(event.select, "field_key", None)
        if key is None:
            return
        line = self._line()
        if key == "cost_method":
            line.cost_method = None if event.value == "none" else event.value
            await self.rebuild_fields()
        else:
            setattr(line, key, event.value)
        self.app_ref.on_data_changed()

    @on(Input.Changed)
    def _input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if getattr(event.input, "_programmatic_update", False):
            event.input._programmatic_update = False
            return
        key = getattr(event.input, "field_key", None)
        if key is None:
            return
        line = self._line()
        if key == "line_name":
            line.line_name = event.value
        elif key in FLOAT_FIELDS:
            setattr(line, key, _parse_float_or_none(event.value))
        else:
            setattr(line, key, event.value.strip() or None)
        self.app_ref.on_data_changed()


# --------------------------------------------------------------------------
# Modal screens (kept only for rare/destructive/report actions)
# --------------------------------------------------------------------------


class ConfirmScreen(ModalScreen[bool]):
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
    CSS = """
    RemoveChildrenScreen { align: center middle; }
    #dialog { width: 70; padding: 1 2; border: thick $primary; background: $surface; }
    #buttons { align: center middle; height: auto; margin-top: 1; }
    Button { margin: 0 1; }
    """

    def __init__(self, line_id: str, child_line_ids: list, default_parent: Optional[str]):
        super().__init__()
        self.line_id = line_id
        self.child_line_ids = child_line_ids
        self.default_parent = default_parent or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"Line '{self.line_id}' has {len(self.child_line_ids)} child line(s): "
                f"{', '.join(self.child_line_ids)}"
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


# --------------------------------------------------------------------------
# Main app
# --------------------------------------------------------------------------


class PBSApp(App[None]):
    """Browse and edit the PBS cost tree as one editable table."""

    TITLE = "PBS Cost Model"

    CSS = """
    #table { height: 1fr; }
    .row { height: auto; width: 1fr; }
    .row-header { height: 1; width: 1fr; }
    .toggle-btn { width: 3; min-width: 3; height: 1; border: none; background: transparent; }
    .wbs-cell { width: 10; color: $text-muted; }
    .name-cell { width: 1fr; }
    .method-select { width: 22; }
    .cost-type-select { width: 16; }
    .cost-label { width: 16; text-align: right; content-align: right middle; padding-right: 1; }
    .cost-label.-unresolved { color: $text-muted; text-style: italic; }
    .flag { width: 2; color: $warning; text-style: bold; }
    .fields { padding-left: 4; height: auto; }
    .field-row { height: 1; }
    .field-label { width: 34; color: $text-muted; padding-left: 1; }
    .field-input { width: 1fr; }
    Input, Select { background: $panel-lighten-1; }
    Input:focus, Select:focus { background: $boost; }
    .component-row .name-cell { color: $text-muted; }
    .row:focus, .row:focus-within { border-left: thick $accent; }
    #total-bar { height: 1; background: $primary; color: $text; text-style: bold; padding: 0 2; }
    #status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #command_bar { height: 1; display: none; background: $panel; }
    .command-prefix { width: 2; padding-left: 1; color: $text; }
    #command_input { width: 1fr; background: $panel; }
    """

    # Vim-style modal keys, not function keys (not every terminal passes those
    # through) and not plain letters alone: a row is focused as a whole in
    # "normal mode" (j/k move between rows, letters are commands), and moves
    # into "insert mode" - editing a field, where letters type normally - via
    # i/Enter; Escape moves focus back to the row ("normal mode" again). This
    # only works because Input/Select swallow every printable key while they
    # themselves hold focus, so these bindings never fire mid-edit.
    BINDINGS = [
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
        Binding("i,enter", "enter_edit", "Edit field"),
        Binding("escape", "escape_to_normal", "Back to row"),
        Binding("o", "add_line", "Add line"),
        Binding("ctrl+o", "add_root_line", "Add root line"),
        Binding("shift+o", "add_component", "Add component"),
        # "dd" (remove) has no single-key Binding - it's a genuine two-key
        # chord, handled in on_key below - Textual bindings map one key each.
        Binding("shift+k", "move_up", "Move up"),
        Binding("shift+j", "move_down", "Move down"),
        Binding("colon", "open_command_bar", "Command (:w, :e)"),
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
        self.wbs_numbers: dict = compute_wbs_numbers(self.lines)
        self.collapsed: set = set()
        self._pending_dd = False
        self._pre_command_row = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="table")
        yield TotalBar("", id="total-bar")
        yield Static("", id="status", markup=False)
        yield Horizontal(
            Static(":", classes="command-prefix"),
            Input(compact=True, id="command_input"),
            id="command_bar",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_table()
        self.set_status(
            "vim-style: j/k move - i/Enter edit - Esc back to row - o add line - "
            "O add component - dd remove - shift+j/k reorder - :w/:e save-as/load - "
            "v validate - x export - r reload - q quit"
        )

    def on_key(self, event: events.Key) -> None:
        if event.key == "d" and isinstance(self.focused, (LineRow, ComponentRow)):
            if self._pending_dd:
                self._pending_dd = False
                event.stop()
                self.action_remove_selected()
            else:
                self._pending_dd = True
        else:
            self._pending_dd = False

    # -- persistence -------------------------------------------------

    def save(self) -> None:
        self.repo.save(self.lines)

    def set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.update(message)
        status.set_class(error, "-error")

    def on_data_changed(self) -> None:
        """Called after any inline field edit: persist and refresh cost labels."""
        self.save()
        self.refresh_costs()

    # -- table rendering ------------------------------------------------

    def _flatten_rows(self):
        rows = []

        def walk(line_id: str, depth: int) -> None:
            rows.append(("line", line_id, None, depth))
            if line_id in self.collapsed:
                return
            line = self.lines[line_id]
            for comp in line.cost_components:
                rows.append(("component", line_id, comp.component_id, depth + 1))
            for child_id in children_of(self.lines, line_id):
                walk(child_id, depth + 1)

        for root_id in root_lines(self.lines):
            walk(root_id, 0)
        return rows

    async def refresh_table(
        self,
        focus_line_id: Optional[str] = None,
        focus_component_id: Optional[str] = None,
        enter_edit: bool = False,
    ) -> None:
        table = self.query_one("#table", VerticalScroll)
        await table.remove_children()
        row_widgets = []
        for kind, line_id, component_id, depth in self._flatten_rows():
            if kind == "line":
                row_widgets.append(LineRow(self, line_id, depth))
            else:
                row_widgets.append(ComponentRow(self, line_id, component_id, depth))
        if row_widgets:
            await table.mount_all(row_widgets)
        self.refresh_costs()

        target = None
        if focus_component_id is not None:
            target = next(
                (
                    r
                    for r in self.query(ComponentRow)
                    if r.line_id == focus_line_id and r.component_id == focus_component_id
                ),
                None,
            )
        elif focus_line_id is not None:
            target = next((r for r in self.query(LineRow) if r.line_id == focus_line_id), None)
        if target is None and row_widgets:
            target = row_widgets[0]
        if target is not None:
            target.first_edit_target().focus() if enter_edit else target.focus()
        else:
            table.focus()

    def refresh_costs(self) -> None:
        self.calculator = CostCalculator(self.lines)
        self.wbs_numbers = compute_wbs_numbers(self.lines)
        for row in self.query(LineRow):
            row.refresh_cost(self.calculator)
        for row in self.query(ComponentRow):
            row.refresh_cost(self.calculator)

        total = 0.0
        any_unresolved = False
        for root_id in root_lines(self.lines):
            result = self.calculator.calculate_line(root_id)
            if result.resolved:
                total += result.cost or 0.0
            else:
                any_unresolved = True
        bar = self.query_one("#total-bar", TotalBar)
        suffix = "  (partial - some lines unresolved)" if any_unresolved else ""
        text = f"TOTAL: ${total:,.2f}{suffix}"
        bar.update(text)
        bar.total_text = text

    async def toggle_collapse(self, line_id: str) -> None:
        if line_id in self.collapsed:
            self.collapsed.discard(line_id)
        else:
            self.collapsed.add(line_id)
        await self.refresh_table(focus_line_id=line_id)

    # -- actions ------------------------------------------------

    def _current_row(self):
        """The row a command should act on: the focused row itself (normal
        mode), or the row owning the focused field (mid-edit)."""
        focused = self.focused
        if isinstance(focused, (LineRow, ComponentRow)):
            return focused
        owner = getattr(focused, "field_owner", None)
        if isinstance(owner, (LineRow, ComponentRow)):
            return owner
        return None

    def _row_widgets(self):
        return list(self.query(".row"))

    def action_cursor_down(self) -> None:
        rows = self._row_widgets()
        if not rows:
            return
        current = self._current_row()
        index = rows.index(current) + 1 if current in rows else 0
        rows[min(index, len(rows) - 1)].focus()

    def action_cursor_up(self) -> None:
        rows = self._row_widgets()
        if not rows:
            return
        current = self._current_row()
        index = rows.index(current) - 1 if current in rows else 0
        rows[max(index, 0)].focus()

    def action_enter_edit(self) -> None:
        row = self._current_row()
        if row is not None and isinstance(self.focused, (LineRow, ComponentRow)):
            row.first_edit_target().focus()

    def action_escape_to_normal(self) -> None:
        if self.focused is self.query_one("#command_input", Input):
            self._close_command_bar()
            return
        row = self._current_row()
        if row is not None:
            row.focus()

    @work
    async def action_move_up(self) -> None:
        await self._move_current_row("up")

    @work
    async def action_move_down(self) -> None:
        await self._move_current_row("down")

    async def _move_current_row(self, direction: str) -> None:
        row = self._current_row()
        if not isinstance(row, LineRow):
            return
        line_id = row.line_id
        if move_line(self.lines, line_id, direction):
            self.save()
            await self.refresh_table(focus_line_id=line_id)
            self.set_status(f"Moved {line_id} {direction}")

    def action_open_command_bar(self) -> None:
        if not isinstance(self.focused, (LineRow, ComponentRow)):
            return  # only from normal mode - never steals ":" while typing
        self._pre_command_row = self._current_row()
        bar = self.query_one("#command_bar")
        input_ = self.query_one("#command_input", Input)
        self.query_one("#status", Static).display = False
        input_.value = ""
        input_.placeholder = "w [path] / e <path> / wq [path] / q"
        bar.display = True
        input_.focus()

    def _close_command_bar(self) -> None:
        bar = self.query_one("#command_bar")
        input_ = self.query_one("#command_input", Input)
        bar.display = False
        input_.value = ""
        self.query_one("#status", Static).display = True
        if self._pre_command_row is not None:
            self._pre_command_row.focus()
        self._pre_command_row = None

    @on(Input.Submitted, "#command_input")
    async def _command_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value
        self._close_command_bar()
        await self._run_command(text)

    async def _run_command(self, text: str) -> None:
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return
        cmd, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if cmd in ("w", "write"):
            if arg:
                self._save_as(arg)
            else:
                self.save()
                self.set_status(f"Saved to {self.repo.path}")
        elif cmd in ("e", "edit"):
            if not arg:
                self.set_status("Usage: :e <path>", error=True)
                return
            await self._load_file(arg)
        elif cmd == "wq":
            if arg:
                self._save_as(arg)
            else:
                self.save()
            self.exit()
        elif cmd == "q":
            self.exit()
        else:
            self.set_status(f"Unknown command ':{cmd}' (try :w, :e, :wq, :q)", error=True)

    def _save_as(self, path: str) -> None:
        self.repo = JSONRepository(path)
        self.save()
        self.set_status(f"Saved to {path}")

    async def _load_file(self, path: str) -> None:
        self.repo = JSONRepository(path)
        self.lines = self.repo.load()
        self.collapsed = set()
        await self.refresh_table()
        self.set_status(f"Loaded {path}")

    @work
    async def action_add_line(self) -> None:
        row = self._current_row()
        parent = row.line_id if row is not None else None
        await self._add_line(parent)

    @work
    async def action_add_root_line(self) -> None:
        await self._add_line(None)

    async def _add_line(self, parent: Optional[str]) -> None:
        line_id = next_line_id(self.lines)
        sort_index = next_sort_index(self.lines, parent)
        self.lines[line_id] = PBSLine(
            line_id=line_id, line_name="", parent_line_id=parent, sort_index=sort_index
        )
        self.save()
        await self.refresh_table(focus_line_id=line_id, enter_edit=True)
        self.set_status(f"Added line {line_id}")

    @work
    async def action_add_component(self) -> None:
        row = self._current_row()
        if not isinstance(row, LineRow):
            self.set_status("Select a first_principles line first", error=True)
            return
        line = self.lines[row.line_id]
        if line.cost_method != CostMethod.FIRST_PRINCIPLES.value:
            self.set_status(f"Line '{line.line_id}' is not cost_method=first_principles", error=True)
            return
        component_id = next_component_id(line)
        line.cost_components.append(
            CostComponent(component_id=component_id, cost_type=CostType.LABOR.value, cost_method=CostMethod.LUMP_SUM.value)
        )
        self.save()
        self.collapsed.discard(line.line_id)  # otherwise the new component would be hidden
        await self.refresh_table(focus_line_id=line.line_id, focus_component_id=component_id, enter_edit=True)
        self.set_status(f"Added component {component_id} to line {line.line_id}")

    @work
    async def action_remove_selected(self) -> None:
        row = self._current_row()
        if row is None:
            self.set_status("Select a line or component first", error=True)
            return
        if isinstance(row, ComponentRow):
            await self._remove_component(row.line_id, row.component_id)
        else:
            await self._remove_line(row.line_id)

    async def _remove_component(self, line_id: str, component_id: str) -> None:
        confirmed = await self.push_screen_wait(
            ConfirmScreen(f"Remove component '{component_id}' from line '{line_id}'?", danger=True)
        )
        if not confirmed:
            return
        line = self.lines[line_id]
        line.cost_components = [c for c in line.cost_components if c.component_id != component_id]
        self.save()
        await self.refresh_table()
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
        await self.refresh_table()
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

    async def action_reload(self) -> None:
        self.lines = self.repo.load()
        await self.refresh_table()
        self.set_status("Reloaded from disk")


def run_tui(file_path: Union[str, Path]) -> None:
    PBSApp(file_path).run()
