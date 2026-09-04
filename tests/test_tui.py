from textual.widgets import Button, Input, Select, Tree

from pbs_cost_model.storage import JSONRepository
from pbs_cost_model.tui import PBSApp


async def click(pilot, selector):
    # Button.press() exercises the same Button.Pressed handling a real mouse
    # click does, without depending on the compositor's hit-test coordinates
    # being fresh (which pilot.click(selector) can flake on right after a
    # Static.update() in the same screen).
    pilot.app.screen.query_one(selector, Button).press()
    await pilot.pause()


async def test_add_root_rollup_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Guideway & Track"
        await click(pilot, "#save")

        tree = app.query_one("#tree", Tree)
        assert len(tree.root.children) == 1
        assert "Guideway & Track" in str(tree.root.children[0].label)
        assert "L001" in app.lines


async def test_add_lump_sum_child_line_and_calc(tmp_path):
    file_path = tmp_path / "tree.json"
    app = PBSApp(str(file_path))
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Parent"
        await click(pilot, "#save")

        tree = app.query_one("#tree", Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Ballast"
        assert app.screen.query_one("#parent", Input).value == "L001"
        app.screen.query_one("#cost_method", Select).value = "lump_sum"
        await pilot.pause()
        app.screen.query_one("#lump_sum_basis", Select).value = "quote"
        app.screen.query_one("#amount", Input).value = "1000"
        await click(pilot, "#save")

        assert app.lines["L002"].parent_line_id == "L001"
        result = app.calculator.calculate_line("L001")
        assert result.cost == 1000.0

    reloaded = JSONRepository(file_path).load()
    assert reloaded["L002"].amount == 1000.0


async def test_add_component_to_first_principles_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Signals"
        app.screen.query_one("#cost_method", Select).value = "first_principles"
        await click(pilot, "#save")

        tree = app.query_one("#tree", Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.pause()

        await pilot.press("shift+a")
        await pilot.pause()
        app.screen.query_one("#cost_method", Select).value = "lump_sum"
        await pilot.pause()
        app.screen.query_one("#lump_sum_basis", Select).value = "quote"
        app.screen.query_one("#amount", Input).value = "500"
        await click(pilot, "#save")

        line = app.lines["L001"]
        assert len(line.cost_components) == 1
        assert app.calculator.calculate_line("L001").cost == 500.0


async def test_remove_leaf_line(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Solo line"
        await click(pilot, "#save")

        tree = app.query_one("#tree", Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await click(pilot, "#yes")

        assert app.lines == {}


async def test_validate_screen_opens_and_closes(tmp_path):
    app = PBSApp(str(tmp_path / "tree.json"))
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Incomplete"
        app.screen.query_one("#cost_method", Select).value = "lump_sum"
        await pilot.pause()
        # leave amount blank -> save should reject with inline error
        await click(pilot, "#save")
        assert len(app.screen_stack) == 2  # rejected, dialog still open

        await click(pilot, "#cancel")

        await pilot.press("v")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await click(pilot, "#close")
        assert len(app.screen_stack) == 1
