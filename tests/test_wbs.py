from pbs_cost_model.models import CostComponent, PBSLine
from pbs_cost_model.operations import indent_line, move_line, outdent_line
from pbs_cost_model.wbs import compute_wbs_numbers, display_component_wbs, display_wbs


def line(**kwargs) -> PBSLine:
    return PBSLine(**kwargs)


def test_flat_roots_numbered_in_sort_order():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0),
        "L002": line(line_id="L002", line_name="B", sort_index=1),
    }
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "2"


def test_nested_numbering():
    lines = {
        "L001": line(line_id="L001", line_name="Root", sort_index=0),
        "L002": line(line_id="L002", line_name="Child1", parent_line_id="L001", sort_index=0),
        "L003": line(line_id="L003", line_name="Child2", parent_line_id="L001", sort_index=1),
        "L004": line(line_id="L004", line_name="Grandchild", parent_line_id="L003", sort_index=0),
    }
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "1.1"
    assert numbers["L003"] == "1.2"
    assert numbers["L004"] == "1.2.1"


def test_wbs_override_takes_precedence():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0, wbs_override="10.0"),
    }
    numbers = compute_wbs_numbers(lines)
    assert display_wbs(lines["L001"], numbers) == "10.0"


def test_no_override_uses_computed():
    lines = {"L001": line(line_id="L001", line_name="A", sort_index=0)}
    numbers = compute_wbs_numbers(lines)
    assert display_wbs(lines["L001"], numbers) == "1"


def test_component_wbs_extends_parent_line_number():
    parent = line(
        line_id="L001",
        line_name="Signal Interface",
        sort_index=0,
        parent_line_id=None,
        cost_method="first_principles",
    )
    c1 = CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum")
    c2 = CostComponent(component_id="C2", cost_type="material", cost_method="parametric")
    parent.cost_components = [c1, c2]
    lines = {"L001": parent}
    numbers = compute_wbs_numbers(lines)
    assert display_component_wbs(parent, c1, numbers) == "1.1"
    assert display_component_wbs(parent, c2, numbers) == "1.2"


def test_component_wbs_follows_parent_override():
    parent = line(
        line_id="L001",
        line_name="Signal Interface",
        sort_index=0,
        wbs_override="50.0",
        cost_method="first_principles",
    )
    comp = CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum")
    parent.cost_components = [comp]
    lines = {"L001": parent}
    numbers = compute_wbs_numbers(lines)
    assert display_component_wbs(parent, comp, numbers) == "50.0.1"


def test_move_line_swaps_sort_index_with_neighbor():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0),
        "L002": line(line_id="L002", line_name="B", sort_index=1),
        "L003": line(line_id="L003", line_name="C", sort_index=2),
    }
    assert move_line(lines, "L002", "up") is True
    numbers = compute_wbs_numbers(lines)
    assert numbers["L002"] == "1"
    assert numbers["L001"] == "2"
    assert numbers["L003"] == "3"


def test_move_line_at_edge_is_noop():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0),
        "L002": line(line_id="L002", line_name="B", sort_index=1),
    }
    assert move_line(lines, "L001", "up") is False
    assert move_line(lines, "L002", "down") is False
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "2"


def test_move_line_only_affects_siblings():
    lines = {
        "L001": line(line_id="L001", line_name="Root", sort_index=0),
        "L002": line(line_id="L002", line_name="Child1", parent_line_id="L001", sort_index=0),
        "L003": line(line_id="L003", line_name="Child2", parent_line_id="L001", sort_index=1),
        "L004": line(line_id="L004", line_name="Other root", sort_index=1),
    }
    assert move_line(lines, "L003", "up") is True
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "1.2"
    assert numbers["L003"] == "1.1"
    assert numbers["L004"] == "2"


def test_indent_makes_line_child_of_preceding_sibling():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0),
        "L002": line(line_id="L002", line_name="B", sort_index=1),
    }
    assert indent_line(lines, "L002") is True
    assert lines["L002"].parent_line_id == "L001"
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "1.1"


def test_indent_first_sibling_is_noop():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0),
        "L002": line(line_id="L002", line_name="B", sort_index=1),
    }
    assert indent_line(lines, "L001") is False
    assert lines["L001"].parent_line_id is None


def test_outdent_moves_line_to_grandparent_level():
    lines = {
        "L001": line(line_id="L001", line_name="Root", sort_index=0),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", sort_index=0),
        "L003": line(line_id="L003", line_name="Grandchild", parent_line_id="L002", sort_index=0),
    }
    assert outdent_line(lines, "L003") is True
    assert lines["L003"].parent_line_id == "L001"
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "1.1"
    assert numbers["L003"] == "1.2"


def test_outdent_root_is_noop():
    lines = {"L001": line(line_id="L001", line_name="Root", sort_index=0)}
    assert outdent_line(lines, "L001") is False
    assert lines["L001"].parent_line_id is None


def test_outdent_to_root_level():
    lines = {
        "L001": line(line_id="L001", line_name="Root", sort_index=0),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", sort_index=0),
    }
    assert outdent_line(lines, "L002") is True
    assert lines["L002"].parent_line_id is None
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "2"


def test_indent_then_outdent_round_trip():
    lines = {
        "L001": line(line_id="L001", line_name="A", sort_index=0),
        "L002": line(line_id="L002", line_name="B", sort_index=1),
    }
    indent_line(lines, "L002")
    outdent_line(lines, "L002")
    assert lines["L002"].parent_line_id is None
    numbers = compute_wbs_numbers(lines)
    assert numbers["L001"] == "1"
    assert numbers["L002"] == "2"
