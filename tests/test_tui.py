from textual.widgets import Button, Input, Select

from pbs_cost_model.storage import JSONRepository
from pbs_cost_model.tui import ComponentRow, LineRow, PBSApp, TotalBar


def _line_row(app, line_id):
    return next(r for r in app.query(LineRow) if r.line_id == line_id)


def _component_row(app, line_id, component_id):
    return next(
        r for r in app.query(ComponentRow) if r.line_id == line_id and r.component_id == component_id
    )


async def test_add_line_and_edit_lump_sum(tmp_path):
    file_path = tmp_path / "tree.json"
    app = PBSApp(str(file_path))
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()

        row = _line_row(app, "L001")
        row.query_one(".name-cell", Input).value = "Ballast"
        await pilot.pause()
        row.query_one(".method-select", Select).value = "lump_sum"
        await pilot.pause()

        row.query_one(".fields Select").value = "quote"
        row.query_one(".fields Input").value = "100000"
        await pilot.pause()

        line = app.lines["L001"]
        assert line.line_name == "Ballast"
        assert line.cost_method == "lump_sum"
        assert line.lump_sum_basis == "quote"
        assert line.amount == 100000.0

        result = app.calculator.calculate_line("L001")
        assert result.cost == 100000.0

        bar = app.query_one(TotalBar)
        assert "$100,000.00" in bar.total_text

    reloaded = JSONRepository(file_path).load()
    assert reloaded["L001"].amount == 100000.0


async def test_switching_method_rebuilds_fields(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f2")
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


async def test_add_line_as_child_of_selected(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        row = _line_row(app, "L001")
        row.query_one(".name-cell", Input).focus()
        await pilot.pause()

        await pilot.press("f2")
        await pilot.pause()

        assert app.lines["L002"].parent_line_id == "L001"


async def test_add_component_to_first_principles_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        row = _line_row(app, "L001")
        row.query_one(".method-select", Select).value = "first_principles"
        row.query_one(".name-cell", Input).focus()
        await pilot.pause()

        await pilot.press("f3")
        await pilot.pause()

        line = app.lines["L001"]
        assert len(line.cost_components) == 1
        comp_row = _component_row(app, "L001", line.cost_components[0].component_id)
        comp_row.query_one(".method-select", Select).value = "lump_sum"
        await pilot.pause()
        comp_row.query_one(".fields Select").value = "quote"
        comp_row.query_one(".fields Input").value = "42"
        await pilot.pause()

        assert app.calculator.calculate_line("L001").cost == 42.0


async def test_toggle_collapse_hides_children(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        parent = _line_row(app, "L001")
        parent.query_one(".name-cell", Input).focus()
        await pilot.pause()
        await pilot.press("f2")
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


async def test_remove_leaf_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        _line_row(app, "L001").query_one(".name-cell", Input).focus()
        await pilot.pause()

        await pilot.press("f4")
        await pilot.pause()
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

        assert app.lines == {}


async def test_remove_line_with_children_cascade(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        _line_row(app, "L001").query_one(".name-cell", Input).focus()
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()

        _line_row(app, "L001").query_one(".name-cell", Input).focus()
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()
        app.screen.query_one("#cascade", Button).press()
        await pilot.pause()
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

        assert app.lines == {}


async def test_validate_screen_opens_and_closes(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("f5")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        app.screen.query_one("#close", Button).press()
        await pilot.pause()
        assert len(app.screen_stack) == 1
