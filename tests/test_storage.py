from pbs_cost_model.models import CostComponent, PBSLine
from pbs_cost_model.storage import JSONRepository, next_component_id, next_line_id


def test_roundtrip(tmp_path):
    path = tmp_path / "tree.json"
    repo = JSONRepository(path)

    comp = CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum", lump_sum_basis="quote", amount=100.0)
    lines = {
        "L001": PBSLine(line_id="L001", line_name="Root"),
        "L002": PBSLine(
            line_id="L002",
            line_name="FP child",
            parent_line_id="L001",
            cost_method="first_principles",
            cost_components=[comp],
        ),
    }
    repo.save(lines)

    reloaded = repo.load()
    assert set(reloaded) == {"L001", "L002"}
    assert reloaded["L002"].cost_components[0].amount == 100.0
    assert reloaded["L002"].parent_line_id == "L001"


def test_load_missing_file_returns_empty(tmp_path):
    repo = JSONRepository(tmp_path / "does_not_exist.json")
    assert repo.load() == {}


def test_next_line_id_sequences():
    lines = {"L001": PBSLine(line_id="L001", line_name="A")}
    assert next_line_id(lines) == "L002"
    assert next_line_id({}) == "L001"


def test_next_component_id_sequences():
    line = PBSLine(line_id="L001", line_name="A", cost_components=[
        CostComponent(component_id="C1", cost_type="labor", cost_method="lump_sum", amount=1.0, lump_sum_basis="quote"),
    ])
    assert next_component_id(line) == "C2"
