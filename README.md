# cost-model

A CLI for building a first-principles cost model organized as a Product
Breakdown Structure (PBS). Build the tree incrementally -- add lines,
add sub-lines, assign cost attributes -- across as many sessions as you
need, and get rolled-up costs, validation, and a flat CSV export at any
point.

Initially scoped to FTA SCC 10 (Guideway & Track Elements), but the tool
has no SCC-specific logic baked in -- the PBS hierarchy is entirely
user-defined.

## Install

```bash
pip install -e ".[dev]"
```

This installs the `pbs-cost-model` console script (equivalently, run
`python -m pbs_cost_model`).

## Data model

- **PBS line**: a node in the tree. Has a `cost_method` of `lump_sum`,
  `parametric`, `percentage`, `first_principles`, or `None` (a pure
  rollup/organizational node with no direct cost of its own).
- **Cost component**: nested only under `first_principles` lines. Has a
  `cost_type` (`labor`, `material`, `equipment`, `shipping`,
  `subcontract`) and its own `cost_method` (any method except
  `first_principles` -- no recursive nesting).

A line can have both a `cost_method` and children. That's allowed --
rolled-up cost = its own cost + the sum of its children -- but `validate`
flags it as a warning so it's never assumed silently.

See method-specific attributes and full validation rules in the build
instructions (below in this repo's history / issue tracker) -- summarized
here:

| cost_method | attributes | calculated cost |
|---|---|---|
| `lump_sum` | `lump_sum_basis` (quote/historical/analogous/allowance), `amount` | `amount` |
| `parametric` | `quantity`, `unit_of_measure`, `unit_rate` | `quantity * unit_rate` |
| `percentage` | `basis_line_ref` (must be a rollup line), `percentage_rate` | `basis_line.rolled_up_cost * percentage_rate` |
| `first_principles` | `cost_components[]` | sum of component costs |

`confidence` is never stored -- it's derived from `lump_sum_basis` at
display time: quote→High, historical→Medium-High, analogous→Medium,
allowance→Low.

## Storage

The tree persists to a single JSON file (default `pbs_tree.json` in the
current directory; override with `-f/--file`). It's designed to read
cleanly in diffs and version control. The data-access layer
(`repository.py`) is separated from persistence (`storage.py`) behind a
small `Store` interface, so a SQLite-backed store could be dropped in
later without touching the CLI commands.

## Commands

All commands take a global `-f/--file PATH` before the subcommand
(default `pbs_tree.json`):

```bash
pbs-cost-model -f myproject.json add-line -n "Guideway & Track Elements"
```

- `add-line` -- add a new PBS line (`-n/--name`, `-p/--parent`,
  `-m/--cost-method`, plus method-specific flags). Prompts interactively
  for anything not passed as a flag; pass `--non-interactive` to skip
  prompting entirely (leaves missing attributes unset, flagged as
  incomplete -- handy for scripted/batch entry).
- `edit-line LINE_ID` -- modify an existing line. Changing
  `--cost-method` clears the old method's attributes first.
- `add-component LINE_ID` -- add a cost_component to a `first_principles`
  line (`--cost-type`, `--cost-method` limited to
  lump_sum/parametric/percentage, plus method-specific flags; for
  percentage use `--ref-type line|sibling_component` and `--basis-line`).
- `edit-component LINE_ID COMPONENT_ID` -- modify a cost_component.
- `remove-line LINE_ID` -- delete, with confirmation. If the line has
  children you must choose `--cascade` (delete the whole subtree) or
  `--reassign-to <line_id|"">` (re-parent the children first, `""` for
  root); interactively you'll be prompted to choose.
- `remove-component LINE_ID COMPONENT_ID` -- delete, with confirmation.
- `show-tree [--show-components]` -- indented tree view with rolled-up
  costs and a tag per line (`lump_sum`/`parametric`/`percentage`/
  `first_principles`/`rollup`) showing how each number was derived.
- `show-line LINE_ID` -- full detail for one line (and its components,
  if any).
- `calc [LINE_ID]` -- rolled-up total for the whole tree or a subtree.
  Incomplete lines are flagged individually rather than failing the
  whole calculation.
- `validate` -- full validation sweep; prints errors/warnings with
  `line_id`/`component_id` references and exits non-zero if there are
  errors.
- `export [-o PATH]` -- flatten the tree to CSV (one row per line, one
  row per cost_component), default `pbs_export.csv`.

## Validation rules

1. `percentage.basis_line_ref` must reference a rollup line (has at
   least one child) -- rejected at input time and re-checked by
   `validate`. The same rule is applied to a component's
   `ref_type=line` reference for consistency, even though the build spec
   only states it explicitly for PBS lines.
2. No circular references in `basis_line_ref` chains, at either the
   line or the cost_component level (including sibling_component refs).
   Detected via cycle-aware memoized resolution in `calc.py`; a cycle
   degrades just the involved lines/components to "unresolved" rather
   than crashing the whole calculation.
3. `cost_method=first_principles` is invalid on a cost_component --
   blocked at input (the CLI's `--cost-method` choices for
   `add-component`/`edit-component` don't even offer it) and re-checked
   by `validate`.
4. Every line/component must have all required attributes for its
   declared method before it's included in `calc` -- `calc` flags
   incomplete lines individually; `validate` reports them as errors.
5. `line_id` is globally unique; `component_id` is unique within its
   parent line.

## Example

```bash
pbs-cost-model -f scc10.json add-line -n "Guideway & Track Elements"
pbs-cost-model -f scc10.json add-line -n "Trackform" -p L1 -m first_principles
pbs-cost-model -f scc10.json add-component L2 --cost-type labor \
    --cost-method lump_sum --lump-sum-basis quote --amount 500000
pbs-cost-model -f scc10.json add-line -n "Special Trackwork" -p L1 \
    -m parametric --quantity 10 --unit EA --unit-rate 12000
pbs-cost-model -f scc10.json show-tree
pbs-cost-model -f scc10.json validate
pbs-cost-model -f scc10.json export -o scc10.csv
```

## Tests

```bash
pytest
```
