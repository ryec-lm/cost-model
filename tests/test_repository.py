import pytest

from pbs_cost_model.repository import RepositoryError


def test_add_line_generates_sequential_ids(repo):
    a = repo.add_line(line_name="A")
    b = repo.add_line(line_name="B", parent_line_id=a.line_id)
    assert a.line_id == "L1"
    assert b.line_id == "L2"
    assert b.parent_line_id == "L1"


def test_add_line_rejects_unknown_parent(repo):
    with pytest.raises(RepositoryError):
        repo.add_line(line_name="Orphan", parent_line_id="L999")


def test_update_line_rejects_self_parent(repo):
    a = repo.add_line(line_name="A")
    with pytest.raises(RepositoryError):
        repo.update_line(a.line_id, parent_line_id=a.line_id)


def test_update_line_rejects_moving_under_own_descendant(repo):
    a = repo.add_line(line_name="A")
    b = repo.add_line(line_name="B", parent_line_id=a.line_id)
    with pytest.raises(RepositoryError):
        repo.update_line(a.line_id, parent_line_id=b.line_id)


def test_children_of_and_has_children(repo):
    a = repo.add_line(line_name="A")
    repo.add_line(line_name="B", parent_line_id=a.line_id)
    assert repo.has_children(a.line_id)
    assert len(repo.children_of(a.line_id)) == 1


def test_descendants_of_multi_level(repo):
    a = repo.add_line(line_name="A")
    b = repo.add_line(line_name="B", parent_line_id=a.line_id)
    c = repo.add_line(line_name="C", parent_line_id=b.line_id)
    ids = {d.line_id for d in repo.descendants_of(a.line_id)}
    assert ids == {b.line_id, c.line_id}


def test_cascade_delete_removes_whole_subtree(repo):
    a = repo.add_line(line_name="A")
    b = repo.add_line(line_name="B", parent_line_id=a.line_id)
    repo.add_line(line_name="C", parent_line_id=b.line_id)
    deleted = repo.cascade_delete(a.line_id)
    assert set(deleted) == {"L1", "L2", "L3"}
    assert repo.list_lines() == []


def test_reassign_children_reparents(repo):
    a = repo.add_line(line_name="A")
    b = repo.add_line(line_name="B", parent_line_id=a.line_id)
    repo.reassign_children(a.line_id, None)
    assert repo.get_line(b.line_id).parent_line_id is None


def test_component_id_generation_and_reuse_after_delete(repo):
    line = repo.add_line(line_name="FP", cost_method="first_principles")
    c1 = repo.add_component(line.line_id, cost_type="labor", cost_method="lump_sum")
    c2 = repo.add_component(line.line_id, cost_type="material", cost_method="lump_sum")
    assert c1.component_id == "C1"
    assert c2.component_id == "C2"
    repo.delete_component(line.line_id, "C1")
    c3 = repo.add_component(line.line_id, cost_type="equipment", cost_method="lump_sum")
    # max existing suffix is 2 (C2), so next is C3 -- ids are never reused
    assert c3.component_id == "C3"


def test_duplicate_component_id_rejected(repo):
    line = repo.add_line(line_name="FP", cost_method="first_principles")
    repo.add_component(line.line_id, cost_type="labor", cost_method="lump_sum", component_id="X")
    with pytest.raises(RepositoryError):
        repo.add_component(line.line_id, cost_type="material", cost_method="lump_sum", component_id="X")
