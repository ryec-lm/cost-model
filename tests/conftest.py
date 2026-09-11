import pytest

from pbs_cost_model.storage import JSONRepository


@pytest.fixture(autouse=True)
def _start_with_empty_tree(tmp_path):
    """Every existing test builds its own tree from scratch and expects a
    real empty starting point - not the FTA SCC seed a brand-new file now
    gets (see scc.py). Pre-create tree.json as empty so load_or_seed() sees
    an existing (if empty) file and skips seeding. Tests for the seeding
    behavior itself use a path that's deliberately left nonexistent.
    """
    JSONRepository(tmp_path / "tree.json").save({})
