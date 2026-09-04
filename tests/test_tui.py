from textual.widgets import Button, Input, Select

from pbs_cost_model.models import PBSLine
from pbs_cost_model.storage import JSONRepository
from pbs_cost_model.tui import ComponentRow, LineRow, PBSApp, TotalBar


def _line_row(app, line_id):
    return next(r for r in app.query(LineRow) if r.line_id == line_id)


def _component_row(app, line_id, component_id):
    return next(
        r for r in app.query(ComponentRow) if r.line_id == line_id and r.component_id == component_id
    )


async def test_o_adds_line_and_enters_edit_mode(tmp_path):
    file_path = tmp_path / "tree.json"
    app = PBSApp(str(file_path))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()

        row = _line_row(app, "L001")
        # "o" behaves like vim's open-line: it also drops straight into edit
        # mode on the new row's name field.
        assert app.focused is row.query_one(".name-cell", Input)

        row.query_one(".name-cell", Input).value = "Ballast"
        row.query_one(".method-select", Select).value = "lump_sum"
        await pilot.pause()
        row.query_one(".fields Select").value = "quote"
        row.query_one(".fields Input").value = "100000"
        await pilot.pause()

        line = app.lines["L001"]
        assert line.line_name == "Ballast"
        assert line.amount == 100000.0
        assert app.calculator.calculate_line("L001").cost == 100000.0

        bar = app.query_one(TotalBar)
        assert "$100,000.00" in bar.total_text

    reloaded = JSONRepository(file_path).load()
    assert reloaded["L001"].amount == 100000.0


async def test_escape_returns_to_normal_mode(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        row = _line_row(app, "L001")
        assert app.focused is row  # back in normal mode: the row itself has focus

        # a plain letter typed in normal mode must NOT land in the name field
        await pilot.press("v")
        await pilot.pause()
        assert len(app.screen_stack) == 2  # "v" fired Validate, not text entry
        app.screen.query_one("#close", Button).press()
        await pilot.pause()
        assert app.lines["L001"].line_name == ""


async def test_jk_navigation_between_rows(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")  # child of L001, since L001 row is focused (normal mode)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.lines["L002"].parent_line_id == "L001"
        assert app.focused.line_id == "L002"

        await pilot.press("k")
        await pilot.pause()
        assert app.focused.line_id == "L001"

        await pilot.press("j")
        await pilot.pause()
        assert app.focused.line_id == "L002"


async def test_i_and_enter_start_editing_focused_row(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        row = _line_row(app, "L001")
        assert app.focused is row

        await pilot.press("i")
        await pilot.pause()
        assert app.focused is row.query_one(".name-cell", Input)


async def test_switching_method_rebuilds_fields(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        row = _line_row(app, "L001")

        row.query_one(".method-select", Select).value = "parametric"
        await pilot.pause()
        assert len(list(row.query(".field-input"))) == 3

        row.query_one(".method-select", Select).value = "percentage"
        await pilot.pause()
        assert len(list(row.query(".field-input"))) == 2

        row.query_one(".method-select", Select).value = "none"
        await pilot.pause()
        assert len(list(row.query(".field-input"))) == 0
        assert app.lines["L001"].cost_method is None


async def test_shift_o_adds_component_to_first_principles_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        row = _line_row(app, "L001")
        row.query_one(".method-select", Select).value = "first_principles"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("shift+o")
        await pilot.pause()

        line = app.lines["L001"]
        assert len(line.cost_components) == 1
        comp_row = _component_row(app, "L001", line.cost_components[0].component_id)
        # "shift+o" should drop straight into editing the new component, like
        # "o" does for a new line - not leave focus on the parent line.
        assert app.focused is comp_row.query_one(".cost-type-select", Select)

        comp_row.query_one(".method-select", Select).value = "lump_sum"
        await pilot.pause()
        comp_row.query_one(".fields Select").value = "quote"
        comp_row.query_one(".fields Input").value = "42"
        await pilot.pause()

        assert app.calculator.calculate_line("L001").cost == 42.0


async def test_add_component_expands_a_collapsed_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        row = _line_row(app, "L001")
        row.query_one(".method-select", Select).value = "first_principles"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        # add and then remove a component just to make the toggle appear,
        # then add one more line as a child so the toggle has something to
        # collapse, and collapse it
        await pilot.press("o")  # child line under L001
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        _line_row(app, "L001").query_one(".toggle-btn", Button).press()
        await pilot.pause()
        assert "L001" in app.collapsed
        assert len(list(app.query(LineRow))) == 1

        _line_row(app, "L001").focus()
        await pilot.pause()
        await pilot.press("shift+o")
        await pilot.pause()

        line = app.lines["L001"]
        assert len(line.cost_components) == 1
        assert "L001" not in app.collapsed
        assert _component_row(app, "L001", line.cost_components[0].component_id) is not None


async def test_toggle_collapse_hides_children(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert len(list(app.query(LineRow))) == 2

        toggle = _line_row(app, "L001").query_one(".toggle-btn", Button)
        toggle.press()
        await pilot.pause()

        assert len(list(app.query(LineRow))) == 1
        assert "L001" in app.collapsed

        toggle2 = _line_row(app, "L001").query_one(".toggle-btn", Button)
        toggle2.press()
        await pilot.pause()
        assert len(list(app.query(LineRow))) == 2


async def test_dd_removes_leaf_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

        assert app.lines == {}


async def test_single_d_does_not_remove(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("j")  # anything else cancels the pending "d"
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert "L001" in app.lines


async def test_dd_removes_line_with_children_cascade(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        _line_row(app, "L001").focus()
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#cascade", Button).press()
        await pilot.pause()
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

        assert app.lines == {}


async def test_validate_screen_opens_and_closes(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("v")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        app.screen.query_one("#close", Button).press()
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_wbs_column_shows_computed_number(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")  # child of L001
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert _line_row(app, "L001").query_one(".wbs-cell", Input).value == "1"
        assert _line_row(app, "L002").query_one(".wbs-cell", Input).value == "1.1"


async def test_wbs_override_pins_number_until_cleared(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        row = _line_row(app, "L001")
        row.query_one(".wbs-cell", Input).value = "99.0"
        await pilot.pause()
        assert app.lines["L001"].wbs_override == "99.0"

        await pilot.press("o")  # add a second line - should not disturb the pin
        await pilot.pause()
        assert _line_row(app, "L001").query_one(".wbs-cell", Input).value == "99.0"

        _line_row(app, "L001").query_one(".wbs-cell", Input).value = ""
        await pilot.pause()
        assert app.lines["L001"].wbs_override is None


async def test_add_root_line_via_ctrl_o(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")  # first line, always root (nothing focused yet)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")  # would nest under L001 if not for ctrl+o below
        await pilot.pause()
        assert app.lines["L002"].parent_line_id == "L001"
        await pilot.press("escape")
        await pilot.pause()

        _line_row(app, "L001").focus()
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()

        assert app.lines["L003"].parent_line_id is None
        assert app.wbs_numbers["L003"] == "2"


async def test_shift_j_k_reorder_siblings(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.wbs_numbers["L001"] == "1"
        assert app.wbs_numbers["L002"] == "2"

        _line_row(app, "L002").focus()
        await pilot.pause()
        await pilot.press("shift+k")
        await pilot.pause()

        assert app.wbs_numbers["L002"] == "1"
        assert app.wbs_numbers["L001"] == "2"
        assert app.focused.line_id == "L002"

        await pilot.press("shift+j")
        await pilot.pause()
        assert app.wbs_numbers["L002"] == "2"
        assert app.wbs_numbers["L001"] == "1"


async def test_colon_w_saves_as_new_file(tmp_path):
    original = tmp_path / "tree.json"
    new_path = tmp_path / "snapshot.json"
    app = PBSApp(str(original))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        row = _line_row(app, "L001")
        row.query_one(".name-cell", Input).value = "Ballast"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press(":")
        await pilot.pause()
        bar = app.query_one("#command_input", Input)
        assert app.focused is bar
        bar.value = f"w {new_path}"
        await pilot.press("enter")
        await pilot.pause()

        assert str(app.repo.path) == str(new_path)
        assert not app.query_one("#command_bar").display

    reloaded = JSONRepository(new_path).load()
    assert reloaded["L001"].line_name == "Ballast"


async def test_colon_e_loads_a_different_file(tmp_path):
    other = tmp_path / "other.json"
    other_lines = {"L001": PBSLine(line_id="L001", line_name="From other file")}
    JSONRepository(other).save(other_lines)

    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press(":")
        await pilot.pause()
        bar = app.query_one("#command_input", Input)
        bar.value = f"e {other}"
        await pilot.press("enter")
        await pilot.pause()

        assert app.lines["L001"].line_name == "From other file"
        assert str(app.repo.path) == str(other)


async def test_escape_closes_command_bar_without_running_it(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press(":")
        await pilot.pause()
        bar = app.query_one("#command_input", Input)
        bar.value = "q"
        await pilot.press("escape")
        await pilot.pause()

        assert not app.query_one("#command_bar").display
        assert app.focused is _line_row(app, "L001")
