from pbs_cost_model.calc import compute, CostCalculator


def test_lump_sum_cost(repo):
    line = repo.add_line(
        line_name="L", cost_method="lump_sum", lump_sum_basis="quote", amount=1000
    )
    results = compute(repo)
    r = results[line.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 1000


def test_parametric_cost(repo):
    line = repo.add_line(
        line_name="L", cost_method="parametric", quantity=10, unit_of_measure="LF", unit_rate=25
    )
    r = compute(repo)[line.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 250


def test_lump_sum_missing_attr_is_unresolved(repo):
    line = repo.add_line(line_name="L", cost_method="lump_sum", lump_sum_basis="quote")
    r = compute(repo)[line.line_id]
    assert not r.resolved
    assert any("missing" in i for i in r.issues)


def test_rollup_sums_children(repo):
    parent = repo.add_line(line_name="P")
    repo.add_line(line_name="C1", parent_line_id=parent.line_id, cost_method="lump_sum", lump_sum_basis="quote", amount=100)
    repo.add_line(line_name="C2", parent_line_id=parent.line_id, cost_method="lump_sum", lump_sum_basis="quote", amount=200)
    r = compute(repo)[parent.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 300


def test_node_with_cost_method_and_children_sums_both(repo):
    parent = repo.add_line(
        line_name="P", cost_method="lump_sum", lump_sum_basis="allowance", amount=50
    )
    repo.add_line(line_name="C", parent_line_id=parent.line_id, cost_method="lump_sum", lump_sum_basis="quote", amount=200)
    r = compute(repo)[parent.line_id]
    assert r.resolved
    assert r.own_cost == 50
    assert r.children_cost == 200
    assert r.rolled_up_cost == 250


def test_percentage_of_rollup(repo):
    parent = repo.add_line(line_name="P")
    repo.add_line(line_name="C", parent_line_id=parent.line_id, cost_method="lump_sum", lump_sum_basis="quote", amount=1000)
    pct = repo.add_line(
        line_name="Pct", cost_method="percentage", basis_line_ref=parent.line_id, percentage_rate=0.1
    )
    r = compute(repo)[pct.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 100


def test_percentage_basis_must_be_rollup(repo):
    leaf = repo.add_line(line_name="Leaf", cost_method="lump_sum", lump_sum_basis="quote", amount=100)
    pct = repo.add_line(
        line_name="Pct", cost_method="percentage", basis_line_ref=leaf.line_id, percentage_rate=0.1
    )
    r = compute(repo)[pct.line_id]
    assert not r.resolved
    assert any("not a rollup" in i for i in r.issues)


def test_circular_percentage_reference_is_unresolved_not_infinite_loop(repo):
    parent = repo.add_line(line_name="P")
    child = repo.add_line(line_name="C", parent_line_id=parent.line_id)
    # child references parent, but parent's rollup includes child -> real cycle
    repo.update_line(child.line_id, cost_method="percentage", basis_line_ref=parent.line_id, percentage_rate=0.1)
    results = compute(repo)
    assert not results[parent.line_id].resolved
    assert not results[child.line_id].resolved


def test_first_principles_sums_components(repo):
    line = repo.add_line(line_name="FP", cost_method="first_principles")
    repo.add_component(line.line_id, cost_type="labor", cost_method="lump_sum", lump_sum_basis="quote", amount=100)
    repo.add_component(line.line_id, cost_type="material", cost_method="parametric", quantity=4, unit_of_measure="EA", unit_rate=25)
    r = compute(repo)[line.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 200


def test_first_principles_empty_components_is_unresolved(repo):
    line = repo.add_line(line_name="FP", cost_method="first_principles")
    r = compute(repo)[line.line_id]
    assert not r.resolved


def test_component_percentage_ref_type_line(repo):
    basis = repo.add_line(line_name="Basis")
    repo.add_line(line_name="BasisChild", parent_line_id=basis.line_id, cost_method="lump_sum", lump_sum_basis="quote", amount=1000)
    fp = repo.add_line(line_name="FP", cost_method="first_principles")
    repo.add_component(
        fp.line_id, cost_type="subcontract", cost_method="percentage",
        ref_type="line", basis_line_ref=basis.line_id, percentage_rate=0.2,
    )
    r = compute(repo)[fp.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 200


def test_component_percentage_ref_type_sibling_component(repo):
    fp = repo.add_line(line_name="FP", cost_method="first_principles")
    labor = repo.add_component(fp.line_id, cost_type="labor", cost_method="lump_sum", lump_sum_basis="quote", amount=1000)
    repo.add_component(
        fp.line_id, cost_type="equipment", cost_method="percentage",
        ref_type="sibling_component", basis_line_ref=labor.component_id, percentage_rate=0.1,
    )
    r = compute(repo)[fp.line_id]
    assert r.resolved
    assert r.rolled_up_cost == 1000 + 100


def test_component_circular_sibling_reference_unresolved(repo):
    fp = repo.add_line(line_name="FP", cost_method="first_principles")
    c1 = repo.add_component(
        fp.line_id, cost_type="labor", cost_method="percentage",
        ref_type="sibling_component", basis_line_ref="C2", percentage_rate=0.1,
    )
    repo.add_component(
        fp.line_id, cost_type="equipment", cost_method="percentage",
        ref_type="sibling_component", basis_line_ref=c1.component_id, percentage_rate=0.1,
    )
    r = compute(repo)[fp.line_id]
    assert not r.resolved


def test_first_principles_component_cannot_be_first_principles(repo):
    fp = repo.add_line(line_name="FP", cost_method="first_principles")
    # Bypass CLI-level guard to exercise calc-time defense in depth.
    repo.add_component(fp.line_id, cost_type="labor", cost_method="first_principles")
    r = compute(repo)[fp.line_id]
    assert not r.resolved
