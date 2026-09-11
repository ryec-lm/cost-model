from pbs_cost_model.calc import CostCalculator, calculate_tree
from pbs_cost_model.models import CostComponent, CostMethod, PBSLine


def line(**kwargs) -> PBSLine:
    return PBSLine(**kwargs)


def test_lump_sum_resolved():
    lines = {"L001": line(line_id="L001", line_name="A", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0)}
    results = calculate_tree(lines)
    assert results["L001"].resolved
    assert results["L001"].cost == 100.0


def test_lump_sum_incomplete():
    lines = {"L001": line(line_id="L001", line_name="A", cost_method="lump_sum")}
    results = calculate_tree(lines)
    assert not results["L001"].resolved
    assert "amount" in results["L001"].reason


def test_parametric():
    lines = {
        "L001": line(
            line_id="L001", line_name="A", cost_method="parametric",
            quantity=10, unit_of_measure="LF", unit_rate=5.0,
        )
    }
    results = calculate_tree(lines)
    assert results["L001"].cost == 50.0


def test_rollup_sums_children():
    lines = {
        "L001": line(line_id="L001", line_name="Parent"),
        "L002": line(line_id="L002", line_name="Child1", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=10.0),
        "L003": line(line_id="L003", line_name="Child2", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=20.0),
    }
    results = calculate_tree(lines)
    assert results["L001"].resolved
    assert results["L001"].cost == 30.0


def test_empty_node_unresolved():
    lines = {"L001": line(line_id="L001", line_name="Empty")}
    results = calculate_tree(lines)
    assert not results["L001"].resolved
    assert "no cost_method" in results["L001"].reason


def test_percentage_depends_on_basis():
    lines = {
        "L001": line(line_id="L001", line_name="Rollup"),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0),
        "L003": line(line_id="L003", line_name="Design fee", cost_method="percentage", basis_line_ref="L001", percentage_rate=0.1),
    }
    results = calculate_tree(lines)
    assert results["L001"].cost == 100.0
    assert results["L003"].cost == 10.0


def test_percentage_circular_reference():
    lines = {
        "L001": line(line_id="L001", line_name="A", cost_method="percentage", basis_line_ref="L002", percentage_rate=0.1),
        "L002": line(line_id="L002", line_name="B", cost_method="percentage", basis_line_ref="L001", percentage_rate=0.1),
    }
    results = calculate_tree(lines)
    assert not results["L001"].resolved
    assert "circular" in results["L001"].reason


def test_leaf_equivalent_with_children_ignores_children():
    lines = {
        "L001": line(line_id="L001", line_name="Leaf-with-kids", cost_method="lump_sum", lump_sum_basis="quote", amount=500.0),
        "L002": line(line_id="L002", line_name="Child", parent_line_id="L001", cost_method="lump_sum", lump_sum_basis="quote", amount=999.0),
    }
    results = calculate_tree(lines)
    assert results["L001"].cost == 500.0
    assert results["L001"].leaf_equivalent_with_children


def test_first_principles_sums_components():
    comp1 = CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0)
    comp2 = CostComponent(component_id="C2", cost_type="material", cost_method="parametric", quantity=2, unit_of_measure="EA", unit_rate=25.0)
    lines = {
        "L001": line(line_id="L001", line_name="FP line", cost_method="first_principles", cost_components=[comp1, comp2]),
    }
    results = calculate_tree(lines)
    assert results["L001"].cost == 150.0


def test_component_percentage_sibling_ref():
    comp1 = CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0)
    comp2 = CostComponent(component_id="C2", cost_type="shipping", cost_method="percentage", ref_type="sibling_component", basis_ref="C1", percentage_rate=0.1)
    lines = {
        "L001": line(line_id="L001", line_name="FP line", cost_method="first_principles", cost_components=[comp1, comp2]),
    }
    results = calculate_tree(lines)
    assert results["L001"].cost == 110.0


def test_component_percentage_sibling_cycle():
    comp1 = CostComponent(component_id="C1", cost_type="labor", cost_method="percentage", ref_type="sibling_component", basis_ref="C2", percentage_rate=0.1)
    comp2 = CostComponent(component_id="C2", cost_type="shipping", cost_method="percentage", ref_type="sibling_component", basis_ref="C1", percentage_rate=0.1)
    lines = {
        "L001": line(line_id="L001", line_name="FP line", cost_method="first_principles", cost_components=[comp1, comp2]),
    }
    calculator = CostCalculator(lines)
    result = calculator.calculate_line("L001")
    assert not result.resolved


def test_component_percentage_line_ref():
    comp1 = CostComponent(component_id="C1", cost_type="subcontract", cost_method="percentage", ref_type="line", basis_ref="L010", percentage_rate=0.2)
    lines = {
        "L010": line(line_id="L010", line_name="Rollup"),
        "L011": line(line_id="L011", line_name="Child of rollup", parent_line_id="L010", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0),
        "L001": line(line_id="L001", line_name="FP line", cost_method="first_principles", cost_components=[comp1]),
    }
    results = calculate_tree(lines)
    assert results["L001"].cost == 20.0
