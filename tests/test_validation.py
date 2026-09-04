from pbs_cost_model.validation import (
    validate,
    check_percentage_basis_at_input,
    check_no_cycle_at_input,
)


def test_clean_tree_has_no_issues(repo):
    line = repo.add_line(line_name="L", cost_method="lump_sum", lump_sum_basis="quote", amount=100)
    issues = validate(repo)
    assert issues == []


def test_percentage_basis_must_be_rollup_reported(repo):
    leaf = repo.add_line(line_name="Leaf", cost_method="lump_sum", lump_sum_basis="quote", amount=100)
    repo.add_line(line_name="Pct", cost_method="percentage", basis_line_ref=leaf.line_id, percentage_rate=0.1)
    issues = validate(repo)
    codes = {i.code for i in issues}
    assert "basis-not-rollup" in codes


def test_nested_first_principles_component_rejected(repo):
    fp = repo.add_line(line_name="FP", cost_method="first_principles")
    repo.add_component(fp.line_id, cost_type="labor", cost_method="first_principles")
    issues = validate(repo)
    assert any(i.code == "nested-first-principles" for i in issues)


def test_duplicate_component_id_reported(repo):
    fp = repo.add_line(line_name="FP", cost_method="first_principles")
    repo.add_component(fp.line_id, cost_type="labor", cost_method="lump_sum", component_id="X")
    # force a duplicate by editing the tree directly (bypassing repo's own guard)
    from pbs_cost_model.models import CostComponent

    line = repo.get_line(fp.line_id)
    line.cost_components.append(
        CostComponent(component_id="X", cost_type="material", cost_method="lump_sum")
    )
    issues = validate(repo)
    assert any(i.code == "duplicate-component-id" for i in issues)


def test_ambiguous_cost_method_and_children_is_warning_not_error(repo):
    parent = repo.add_line(line_name="P", cost_method="lump_sum", lump_sum_basis="quote", amount=100)
    repo.add_line(line_name="C", parent_line_id=parent.line_id, cost_method="lump_sum", lump_sum_basis="quote", amount=50)
    issues = validate(repo)
    ambiguous = [i for i in issues if i.code == "cost-method-and-children"]
    assert len(ambiguous) == 1
    assert ambiguous[0].level == "warning"
    assert all(i.level != "error" for i in ambiguous)


def test_incomplete_line_reported_as_error(repo):
    repo.add_line(line_name="L", cost_method="parametric", quantity=5)
    issues = validate(repo)
    assert any(i.code == "incomplete" for i in issues)


def test_circular_reference_reported(repo):
    parent = repo.add_line(line_name="P")
    child = repo.add_line(line_name="C", parent_line_id=parent.line_id)
    repo.update_line(child.line_id, cost_method="percentage", basis_line_ref=parent.line_id, percentage_rate=0.1)
    issues = validate(repo)
    assert any(i.code == "circular-reference" for i in issues)


def test_check_percentage_basis_at_input_rejects_leaf(repo):
    leaf = repo.add_line(line_name="Leaf", cost_method="lump_sum", lump_sum_basis="quote", amount=1)
    msg = check_percentage_basis_at_input(repo, leaf.line_id)
    assert msg is not None


def test_check_percentage_basis_at_input_accepts_rollup(repo):
    parent = repo.add_line(line_name="P")
    repo.add_line(line_name="C", parent_line_id=parent.line_id)
    msg = check_percentage_basis_at_input(repo, parent.line_id)
    assert msg is None


def test_check_no_cycle_at_input_rejects_self(repo):
    a = repo.add_line(line_name="A")
    msg = check_no_cycle_at_input(repo, a.line_id, a.line_id)
    assert msg is not None


def test_check_no_cycle_at_input_rejects_descendant(repo):
    a = repo.add_line(line_name="A")
    b = repo.add_line(line_name="B", parent_line_id=a.line_id)
    msg = check_no_cycle_at_input(repo, a.line_id, b.line_id)
    assert msg is not None
