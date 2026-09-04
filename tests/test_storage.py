import json
import os

from pbs_cost_model.storage import JSONFileStore
from pbs_cost_model.repository import Repository


def test_json_file_store_round_trip(tmp_path):
    path = tmp_path / "tree.json"
    repo = Repository(JSONFileStore(str(path)))
    repo.add_line(line_name="Root", cost_method=None)
    repo.save()

    assert path.exists()
    with open(path) as f:
        data = json.load(f)
    assert "L1" in data["lines"]
    assert data["lines"]["L1"]["line_name"] == "Root"

    repo2 = Repository(JSONFileStore(str(path)))
    assert repo2.get_line("L1").line_name == "Root"


def test_missing_file_yields_empty_tree(tmp_path):
    path = tmp_path / "does_not_exist.json"
    repo = Repository(JSONFileStore(str(path)))
    assert repo.list_lines() == []


def test_save_is_atomic_no_leftover_tmp_files(tmp_path):
    path = tmp_path / "tree.json"
    repo = Repository(JSONFileStore(str(path)))
    repo.add_line(line_name="Root")
    repo.save()
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".pbs_tmp_")]
    assert leftovers == []
