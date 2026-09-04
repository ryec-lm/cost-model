import pytest

from pbs_cost_model.storage import Store, EMPTY_DOCUMENT
from pbs_cost_model.repository import Repository


class InMemoryStore(Store):
    """Store backed by a plain dict, for fast in-process tests."""

    def __init__(self):
        import copy

        self._data = copy.deepcopy(EMPTY_DOCUMENT)

    def load(self):
        import copy

        return copy.deepcopy(self._data)

    def save(self, data):
        import copy

        self._data = copy.deepcopy(data)


@pytest.fixture
def repo():
    return Repository(InMemoryStore())
