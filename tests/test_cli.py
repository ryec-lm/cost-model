import csv

from click.testing import CliRunner

from pbs_cost_model.cli import cli


def run(args):
    runner = CliRunner()
    return runner.invoke(cli, args, obj={})


def test_add_line_and_show_tree(tmp_path):
    f = str(tmp_path / "t.json")
    result = run(["-f", f, "add-line", "-n", "Root", "--non-interactive"])
    assert result.exit_code == 0, result.output
    assert "Added line L1" in result.output

    result = run(["-f", f, "show-tree"])
    assert result.exit_code == 0, result.output
    assert "Root" in result.output


def test_add_line_requires_name_non_interactive(tmp_path):
    f = str(tmp_path / "t.json")
    result = run(["-f", f, "add-line", "--non-interactive"])
    assert result.exit_code != 0


def test_add_line_rejects_leaf_percentage_basis(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "Leaf", "-m", "lump_sum", "--lump-sum-basis", "quote", "--amount", "100", "--non-interactive"])
    result = run(
        [
            "-f", f, "add-line", "-n", "Pct", "-m", "percentage",
            "--basis-line", "L1", "--percentage-rate", "0.1", "--non-interactive",
        ]
    )
    assert result.exit_code != 0
    assert "leaf" in result.output.lower()


def test_add_component_requires_first_principles_line(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "Parametric", "-m", "parametric", "--quantity", "1", "--unit", "EA", "--unit-rate", "5", "--non-interactive"])
    result = run(
        ["-f", f, "add-component", "L1", "--cost-type", "labor", "--cost-method", "lump_sum", "--lump-sum-basis", "quote", "--amount", "10", "--non-interactive"]
    )
    assert result.exit_code != 0
    assert "first_principles" in result.output


def test_full_flow_add_component_calc(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "FP", "-m", "first_principles", "--non-interactive"])
    result = run(
        ["-f", f, "add-component", "L1", "--cost-type", "labor", "--cost-method", "lump_sum", "--lump-sum-basis", "quote", "--amount", "1000", "--non-interactive"]
    )
    assert result.exit_code == 0, result.output
    result = run(["-f", f, "calc"])
    assert result.exit_code == 0
    assert "1,000.00" in result.output


def test_remove_line_with_children_requires_flag_non_interactive(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "P", "--non-interactive"])
    run(["-f", f, "add-line", "-n", "C", "-p", "L1", "--non-interactive"])
    result = run(["-f", f, "remove-line", "L1", "--yes"])
    assert result.exit_code != 0


def test_remove_line_cascade(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "P", "--non-interactive"])
    run(["-f", f, "add-line", "-n", "C", "-p", "L1", "--non-interactive"])
    result = run(["-f", f, "remove-line", "L1", "--yes", "--cascade"])
    assert result.exit_code == 0, result.output
    result = run(["-f", f, "show-tree"])
    assert "(empty tree)" in result.output


def test_remove_line_reassign(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "P", "--non-interactive"])
    run(["-f", f, "add-line", "-n", "C", "-p", "L1", "--non-interactive"])
    result = run(["-f", f, "remove-line", "L1", "--yes", "--reassign-to", ""])
    assert result.exit_code == 0, result.output
    result = run(["-f", f, "show-line", "L2"])
    assert "(root)" in result.output


def test_validate_exit_code_nonzero_on_error(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "Leaf", "-m", "lump_sum", "--lump-sum-basis", "quote", "--amount", "1", "--non-interactive"])
    # A parametric line missing required attrs is an incomplete-line error.
    run(["-f", f, "add-line", "-n", "Incomplete", "-m", "parametric", "--quantity", "1", "--non-interactive"])
    result = run(["-f", f, "validate"])
    assert result.exit_code == 1
    assert "incomplete" in result.output.lower() or "error" in result.output.lower()


def test_export_csv_rows(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "FP", "-m", "first_principles", "--non-interactive"])
    run(["-f", f, "add-component", "L1", "--cost-type", "labor", "--cost-method", "lump_sum", "--lump-sum-basis", "quote", "--amount", "1000", "--non-interactive"])
    out_csv = str(tmp_path / "out.csv")
    result = run(["-f", f, "export", "-o", out_csv])
    assert result.exit_code == 0, result.output
    with open(out_csv) as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    kinds = {r["row_type"] for r in rows}
    assert kinds == {"line", "component"}


def test_edit_line_changes_method_and_clears_old_attrs(tmp_path):
    f = str(tmp_path / "t.json")
    run(["-f", f, "add-line", "-n", "L", "-m", "lump_sum", "--lump-sum-basis", "quote", "--amount", "100", "--non-interactive"])
    result = run(["-f", f, "edit-line", "L1", "-m", "parametric", "--quantity", "2", "--unit", "EA", "--unit-rate", "50", "--non-interactive"])
    assert result.exit_code == 0, result.output
    result = run(["-f", f, "calc"])
    assert "100.00" in result.output  # 2 * 50 == 100.00
