import csv

from click.testing import CliRunner

from pbs_cost_model.cli import main


def run(runner, args, tmp_path):
    return runner.invoke(main, ["--file", str(tmp_path / "tree.json")] + args)


def test_full_workflow(tmp_path):
    runner = CliRunner()

    r = run(runner, ["add-line", "--name", "Guideway & Track"], tmp_path)
    assert r.exit_code == 0, r.output
    assert "Added line L001" in r.output

    r = run(runner, ["add-line", "--name", "Track structure", "--parent", "L001",
                     "--cost-method", "lump_sum", "--lump-sum-basis", "quote", "--amount", "100000"], tmp_path)
    assert r.exit_code == 0, r.output
    assert "Added line L002" in r.output

    r = run(runner, ["add-line", "--name", "Design fee", "--cost-method", "percentage",
                     "--basis-line", "L001", "--rate", "0.05"], tmp_path)
    assert r.exit_code == 0, r.output

    r = run(runner, ["show-tree"], tmp_path)
    assert r.exit_code == 0, r.output
    assert "L001" in r.output and "L002" in r.output and "$100,000.00" in r.output

    r = run(runner, ["calc"], tmp_path)
    assert r.exit_code == 0, r.output
    assert "$105,000.00" in r.output

    r = run(runner, ["validate"], tmp_path)
    assert r.exit_code == 0, r.output
    assert "OK" in r.output

    csv_path = tmp_path / "out.csv"
    r = run(runner, ["export", str(csv_path)], tmp_path)
    assert r.exit_code == 0, r.output
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 3


def test_add_line_missing_required_attr_errors_noninteractive(tmp_path):
    runner = CliRunner()
    r = run(runner, ["add-line", "--name", "Bad", "--cost-method", "lump_sum",
                     "--lump-sum-basis", "quote"], tmp_path)
    assert r.exit_code != 0
    assert "--amount" in r.output


def test_percentage_ref_to_leaf_rejected_by_validate(tmp_path):
    runner = CliRunner()
    run(runner, ["add-line", "--name", "Leaf", "--cost-method", "lump_sum",
                 "--lump-sum-basis", "quote", "--amount", "10"], tmp_path)
    run(runner, ["add-line", "--name", "Fee", "--cost-method", "percentage",
                 "--basis-line", "L001", "--rate", "0.1"], tmp_path)
    r = run(runner, ["validate"], tmp_path)
    assert r.exit_code != 0
    assert "leaf line" in r.output


def test_remove_line_with_children_requires_flag(tmp_path):
    runner = CliRunner()
    run(runner, ["add-line", "--name", "Parent"], tmp_path)
    run(runner, ["add-line", "--name", "Child", "--parent", "L001"], tmp_path)
    r = run(runner, ["remove-line", "L001", "--yes"], tmp_path)
    assert r.exit_code != 0
    assert "has children" in r.output


def test_remove_line_cascade(tmp_path):
    runner = CliRunner()
    run(runner, ["add-line", "--name", "Parent"], tmp_path)
    run(runner, ["add-line", "--name", "Child", "--parent", "L001"], tmp_path)
    r = run(runner, ["remove-line", "L001", "--yes", "--cascade"], tmp_path)
    assert r.exit_code == 0, r.output
    r = run(runner, ["show-tree"], tmp_path)
    assert "(empty tree)" in r.output


def test_add_component_and_first_principles_calc(tmp_path):
    runner = CliRunner()
    run(runner, ["add-line", "--name", "FP line", "--cost-method", "first_principles"], tmp_path)
    r = run(runner, ["add-component", "L001", "--cost-type", "labor", "--cost-method", "lump_sum",
                     "--lump-sum-basis", "quote", "--amount", "50"], tmp_path)
    assert r.exit_code == 0, r.output
    r = run(runner, ["add-component", "L001", "--cost-type", "material", "--cost-method", "parametric",
                     "--quantity", "10", "--unit", "EA", "--unit-rate", "2"], tmp_path)
    assert r.exit_code == 0, r.output
    r = run(runner, ["calc", "L001"], tmp_path)
    assert r.exit_code == 0, r.output
    assert "$70.00" in r.output


def test_edit_line_updates_field(tmp_path):
    runner = CliRunner()
    run(runner, ["add-line", "--name", "Original", "--cost-method", "lump_sum",
                 "--lump-sum-basis", "quote", "--amount", "10"], tmp_path)
    r = run(runner, ["edit-line", "L001", "--amount", "20"], tmp_path)
    assert r.exit_code == 0, r.output
    r = run(runner, ["calc", "L001"], tmp_path)
    assert "$20.00" in r.output


def test_edit_line_parent_cycle_rejected(tmp_path):
    runner = CliRunner()
    run(runner, ["add-line", "--name", "Parent"], tmp_path)
    run(runner, ["add-line", "--name", "Child", "--parent", "L001"], tmp_path)
    r = run(runner, ["edit-line", "L001", "--parent", "L002"], tmp_path)
    assert r.exit_code != 0
    assert "cycle" in r.output
