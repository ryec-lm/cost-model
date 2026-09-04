# PBS Cost Model CLI

A CLI for building a first-principles cost estimate as a Product Breakdown
Structure (PBS) tree. The hierarchy is entirely user-defined - nothing here
is hardcoded to any particular FTA SCC or discipline.

## Install

```
pip install -e ".[dev]"
```

This installs the `pbs` command.

## Data model

Every node in the tree is a **line** (`line_id`, `line_name`,
`parent_line_id`, `cost_method`). `cost_method` is one of:

- `lump_sum` - `lump_sum_basis` (quote/historical/analogous/allowance) + `amount`.
  Confidence (High/Medium-High/Medium/Low) is derived from the basis, not stored.
- `parametric` - `quantity` x `unit_rate` (with a `unit_of_measure`).
- `percentage` - `percentage_rate` x the rolled-up cost of `basis_line_ref`.
  The basis must be a rollup line (has children); it can't be a leaf, an
  ancestor, or a descendant of the referencing line.
- `first_principles` - cost is the sum of its `cost_components` (no direct
  attributes of its own).
- `null` (unset) - a pure rollup/organizational node; its cost is the sum
  of its children.

A line can have children regardless of `cost_method`. **If a line has both
a `cost_method` and children, it is treated as a leaf-equivalent cost
line: its own cost_method total is used, and children are NOT rolled into
it.** `show-tree`, `show-line`, and `validate` all flag this combination
so it's never silent.

**Cost components** live only under `first_principles` lines. Each has a
`cost_type` (labor/material/equipment/shipping/subcontract) and its own
`cost_method` (`lump_sum`, `parametric`, or `percentage` - `first_principles`
is not allowed here, no recursive nesting). A percentage component's
`basis_ref` can point to another PBS line (`ref_type=line`) or to a sibling
component in the same first_principles line (`ref_type=sibling_component`).

## Storage

The tree is stored as JSON (default `pbs_tree.json` in the current
directory; override with `-f/--file`). `pbs_cost_model/storage.py` defines
a `PBSRepository` interface with `load()`/`save()` - the CLI and
calculation code never touch the file format directly, so swapping in a
SQLite-backed repository later doesn't require touching `cli.py`,
`calc.py`, or `validation.py`.

## Commands

```
pbs [-f FILE] add-line       [--name] [--parent] [--cost-method] [method-specific flags...]
pbs [-f FILE] edit-line      LINE_ID  [same flags as add-line, plus --clear-cost-method]
pbs [-f FILE] add-component  LINE_ID  [--cost-type] [--cost-method] [method-specific flags...]
pbs [-f FILE] edit-component LINE_ID COMPONENT_ID [same flags as add-component]
pbs [-f FILE] remove-line    LINE_ID  [--yes] [--cascade | --reassign-to PARENT_ID]
pbs [-f FILE] remove-component LINE_ID COMPONENT_ID [--yes]
pbs [-f FILE] show-tree      [LINE_ID]
pbs [-f FILE] show-line      LINE_ID
pbs [-f FILE] calc           [LINE_ID]
pbs [-f FILE] validate
pbs [-f FILE] export         OUTPUT.csv
```

Run any command with `--help` for its full flag list.

### Interactive vs. scripted

`add-line`/`edit-line`/`add-component`/`edit-component` accept every
attribute as a flag for scripting. Any required attribute left off is
prompted for when stdin/stdout is a TTY, and raises a clear usage error
otherwise (so batch/CI use fails fast instead of hanging on a prompt).
Running `edit-line`/`edit-component` with no flags at all, interactively,
walks you through every field with the current value as the default.

### Removing a line with children

`remove-line` on a line with children requires an explicit decision:
`--cascade` deletes the whole subtree, or `--reassign-to PARENT_ID`
(`--reassign-to ""` for root) reparents the children first. Run
interactively with neither flag and you'll be prompted per-case.

## Example

```
pbs add-line -n "Guideway & Track Elements"                       # -> L001 (rollup)
pbs add-line -n "Track Structure" -p L001 -m parametric \
    --quantity 5000 --unit LF --unit-rate 450                     # -> L002
pbs add-line -n "Ballast" -p L001 -m lump_sum \
    --lump-sum-basis historical --amount 250000                   # -> L003
pbs add-line -n "Design & Engineering" -m percentage \
    --basis-line L001 --rate 0.08                                 # -> L004
pbs add-line -n "Signal Interface" -m first_principles             # -> L005
pbs add-component L005 --cost-type labor -m parametric \
    --quantity 200 --unit HR --unit-rate 85                       # -> C1
pbs add-component L005 --cost-type shipping -m percentage \
    --ref-type sibling_component --basis-ref C1 --rate 0.05       # -> C2

pbs show-tree
pbs calc
pbs validate
pbs export estimate.csv
```

## Tests

```
pytest
```
