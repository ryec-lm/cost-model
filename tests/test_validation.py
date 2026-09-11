from pbs_cost_model.models import CostComponent, PBSLine
from pbs_cost_model.validation import validate_tree


def line(**kwargs) -> PBSLine:
    return PBSLine(**kwargs)


def test_percentage_ref_to_leaf_is_error():
    lines = {
        "L001": line(line_id="L001", line_name="Leaf", cost_method="lump_sum", lump_sum_basis="quote", amount=10.0),
        "L002": line(line_id="L002", line_name="Fee", cost_method="percentage", basis_line_ref="L001", percentage_rate=0.1),
    }
    report = validate_tree(lines)
    assert any("leaf line" in e for e in report.errors)


def test_percentage_ref_to_ancestor_is_error():
    lines = {
        "L001": line(line_id="L001", line_name="Root", cost_method="percentage", basis_line_ref="L002", percentage_rate=0.1),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=10.0),
    }
    # L001 is L002's ancestor; L002 -> parent is L001, but here L001 references L002
    # which is a descendant of L001. Should be flagged as descendant reference.
    report = validate_tree(lines)
    assert any("descendant" in e for e in report.errors)


def test_percentage_ref_to_valid_rollup_is_ok():
    lines = {
        "L001": line(line_id="L001", line_name="Rollup"),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=10.0),
        "L003": line(line_id="L003", line_name="Fee", cost_method="percentage", basis_line_ref="L001", percentage_rate=0.1),
    }
    report = validate_tree(lines)
    assert report.ok


def test_component_first_principles_blocked():
    comp = CostComponent(component_id="C1", cost_type="labor", cost_method="first_principles")
    lines = {
        "L001": line(line_id="L001", line_name="FP", cost_method="first_principles", cost_components=[comp]),
    }
    report = validate_tree(lines)
    assert any("not allowed on a cost_component" in e for e in report.errors)


def test_duplicate_component_id():
    comp1 = CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum", lump_sum_basis="quote", amount=10.0)
    comp2 = CostComponent(component_id="C1", cost_type="material", cost_method="lump_sum", lump_sum_basis="quote", amount=20.0)
    lines = {
        "L001": line(line_id="L001", line_name="FP", cost_method="first_principles", cost_components=[comp1, comp2]),
    }
    report = validate_tree(lines)
    assert any("duplicate component_id" in e for e in report.errors)


def test_leaf_with_children_is_warning_not_error():
    lines = {
        "L001": line(line_id="L001", line_name="Leaf+kids", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=10.0),
    }
    report = validate_tree(lines)
    assert report.ok
    assert any("leaf-equivalent" in w for w in report.warnings)


def test_incomplete_line_reported_not_error():
    lines = {"L001": line(line_id="L001", line_name="Incomplete", cost_method="lump_sum")}
    report = validate_tree(lines)
    assert report.ok
    assert any("L001" in i for i in report.incomplete)
