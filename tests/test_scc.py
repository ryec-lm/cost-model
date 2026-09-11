from pbs_cost_model.scc import FTA_SCC_CATEGORIES, load_or_seed, seed_fta_scc_tree
from pbs_cost_model.storage import JSONRepository
from pbs_cost_model.wbs import compute_wbs_numbers, display_wbs


def test_seed_creates_ten_categories_with_pinned_wbs():
    lines = seed_fta_scc_tree()
    assert len(lines) == 10
    numbers = compute_wbs_numbers(lines)
    for line in lines.values():
        assert line.parent_line_id is None
        assert display_wbs(line, numbers) == line.wbs_override  # pinned, not auto

    by_wbs = {line.wbs_override: line.line_name for line in lines.values()}
    assert by_wbs["10"] == "Guideway & Track Elements"
    assert by_wbs["100"] == "Finance Charges"
    assert [n for n, _ in FTA_SCC_CATEGORIES] == sorted(by_wbs, key=int)


def test_load_or_seed_seeds_a_nonexistent_file(tmp_path):
    path = tmp_path / "brand_new.json"
    repo = JSONRepository(path)

    lines = load_or_seed(repo)

    assert len(lines) == 10
    assert path.exists()  # seeding also persists it
    reloaded = repo.load()
    assert len(reloaded) == 10


def test_load_or_seed_does_not_reseed_an_existing_empty_file(tmp_path):
    path = tmp_path / "tree.json"
    repo = JSONRepository(path)
    repo.save({})  # a deliberately empty tree already exists

    lines = load_or_seed(repo)

    assert lines == {}


def test_seeded_categories_are_freely_editable(tmp_path):
    # autouse fixture pre-creates tree.json as empty - use a fresh name here
    path = tmp_path / "brand_new.json"
    repo = JSONRepository(path)
    lines = load_or_seed(repo)

    line_id = next(lid for lid, l in lines.items() if l.wbs_override == "10")
    lines[line_id].line_name = "Renamed Category"
    del lines[next(lid for lid, l in lines.items() if l.wbs_override == "90")]
    repo.save(lines)

    reloaded = repo.load()
    assert len(reloaded) == 9
    assert reloaded[line_id].line_name == "Renamed Category"
