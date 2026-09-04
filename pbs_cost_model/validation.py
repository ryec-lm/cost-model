"""Full validation sweep (the `validate` CLI command) plus the small set of
checks the CLI calls at input time so bad data is rejected immediately
rather than only caught later by `validate`.

Rules implemented (see BUILD_INSTRUCTIONS):
  1. percentage basis_line_ref must reference a rollup line (has children).
     Applied to both PBS-line percentage and component-level percentage with
     ref_type="line" -- same rationale (percentage-of-an-aggregate) applies
     to both, so we hold components to the same standard even though the
     spec only states it explicitly for PBS lines.
  2. No circular references in basis_line_ref chains (lines or components).
  3. cost_method="first_principles" is invalid on a cost_component.
  4. Every line/component must have all required attributes for its
     declared method to be included in `calc` -- missing attributes are
     reported here as validation errors, and separately cause `calc` to
     mark just that line as unresolved rather than failing the whole run.
  5. line_id and component_id (within its parent line) must be unique.

Plus one thing the build explicitly asked to have flagged rather than
silently assumed: a line that carries both a cost_method and children. That
is *allowed* (rolled-up cost = own cost + sum of children), but is reported
here as a warning so it's never accidentally ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .models import COST_METHODS, COMPONENT_COST_METHODS
from .repository import Repository
from .calc import compute

# Cascading issue messages produced by calc.py when a *dependency* is
# unresolved (as opposed to the line/component itself being the root
# cause). We skip these in `validate` output to avoid reporting the same
# root cause once per ancestor -- the root-cause line is reported on its
# own account instead.
_CASCADE_PREFIXES = (
    "child ",
    "basis line ",
    "component ",
    "sibling component ",
)


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    line_id: Optional[str] = None
    component_id: Optional[str] = None

    def __str__(self) -> str:
        loc = self.line_id or ""
        if self.component_id:
            loc = f"{loc}/{self.component_id}"
        loc = f"[{loc}] " if loc else ""
        return f"{self.level.upper():7} {loc}{self.message}"


def _is_root_cause(message: str) -> bool:
    # A circular reference is itself the interesting fact even when it's
    # embedded inside a cascaded "basis line X is unresolved (...)" message,
    # so always surface it regardless of prefix.
    if "circular reference" in message:
        return True
    return not message.startswith(_CASCADE_PREFIXES)


def validate(repo: Repository) -> List[Issue]:
    issues: List[Issue] = []

    # -- structural integrity: dict key vs line_id, uniqueness -----------
    for key, line in repo.tree.lines.items():
        if key != line.line_id:
            issues.append(
                Issue(
                    "error",
                    "id-mismatch",
                    f"stored under key {key!r} but line_id is {line.line_id!r}",
                    line_id=line.line_id,
                )
            )
        if line.cost_method is not None and line.cost_method not in COST_METHODS:
            issues.append(
                Issue(
                    "error",
                    "bad-cost-method",
                    f"unknown cost_method {line.cost_method!r}",
                    line_id=line.line_id,
                )
            )
        if line.parent_line_id is not None and line.parent_line_id not in repo.tree.lines:
            issues.append(
                Issue(
                    "error",
                    "missing-parent",
                    f"parent_line_id {line.parent_line_id!r} does not exist",
                    line_id=line.line_id,
                )
            )

        # rule 5 (components): component_id unique within the line
        seen_component_ids = set()
        for comp in line.cost_components:
            if comp.component_id in seen_component_ids:
                issues.append(
                    Issue(
                        "error",
                        "duplicate-component-id",
                        f"duplicate component_id {comp.component_id!r} on line {line.line_id!r}",
                        line_id=line.line_id,
                        component_id=comp.component_id,
                    )
                )
            seen_component_ids.add(comp.component_id)

            # rule 3: no first_principles inside a component
            if comp.cost_method == "first_principles":
                issues.append(
                    Issue(
                        "error",
                        "nested-first-principles",
                        "cost_method=first_principles is not valid on a cost_component",
                        line_id=line.line_id,
                        component_id=comp.component_id,
                    )
                )
            elif comp.cost_method not in COMPONENT_COST_METHODS:
                issues.append(
                    Issue(
                        "error",
                        "bad-cost-method",
                        f"unknown cost_method {comp.cost_method!r}",
                        line_id=line.line_id,
                        component_id=comp.component_id,
                    )
                )

        # ambiguity flag: cost_method set AND has children
        if line.cost_method is not None and repo.has_children(line.line_id):
            issues.append(
                Issue(
                    "warning",
                    "cost-method-and-children",
                    "line has both a cost_method and children -- rolled-up cost will be "
                    "its own cost plus the sum of its children (allowed, but flagged so "
                    "it's never assumed by accident)",
                    line_id=line.line_id,
                )
            )

    # -- rule 1: percentage basis must be a rollup line -------------------
    for line in repo.list_lines():
        if line.cost_method == "percentage" and line.basis_line_ref is not None:
            _check_percentage_basis_is_rollup(
                repo, issues, line.basis_line_ref, line_id=line.line_id
            )
        for comp in line.cost_components:
            if comp.cost_method != "percentage" or comp.basis_line_ref is None:
                continue
            if comp.ref_type == "line":
                _check_percentage_basis_is_rollup(
                    repo,
                    issues,
                    comp.basis_line_ref,
                    line_id=line.line_id,
                    component_id=comp.component_id,
                )
            elif comp.ref_type == "sibling_component":
                if comp.basis_line_ref == comp.component_id:
                    issues.append(
                        Issue(
                            "error",
                            "self-reference",
                            "component references itself as basis_line_ref",
                            line_id=line.line_id,
                            component_id=comp.component_id,
                        )
                    )
                elif not any(
                    c.component_id == comp.basis_line_ref for c in line.cost_components
                ):
                    issues.append(
                        Issue(
                            "error",
                            "missing-basis",
                            f"sibling_component {comp.basis_line_ref!r} does not exist "
                            f"on this line",
                            line_id=line.line_id,
                            component_id=comp.component_id,
                        )
                    )
            elif comp.ref_type not in (None,):
                issues.append(
                    Issue(
                        "error",
                        "bad-ref-type",
                        f"invalid ref_type {comp.ref_type!r}",
                        line_id=line.line_id,
                        component_id=comp.component_id,
                    )
                )

    # -- rules 2 & 4: circular refs + completeness, via the calculator ----
    # We reuse calc.py's resolution pass rather than re-implementing cycle
    # detection: any issue calc surfaces at the *origin* node (not a
    # cascade from a dependency) is a validation error.
    results = compute(repo)
    for line_id, lr in results.items():
        for msg in lr.issues:
            if _is_root_cause(msg):
                issues.append(
                    Issue("error", _classify(msg), msg, line_id=line_id)
                )
        for comp_id, cr in lr.component_results.items():
            for msg in cr.issues:
                if _is_root_cause(msg):
                    issues.append(
                        Issue(
                            "error", _classify(msg), msg, line_id=line_id, component_id=comp_id
                        )
                    )

    return issues


def _classify(message: str) -> str:
    if "circular reference" in message:
        return "circular-reference"
    if "missing:" in message:
        return "incomplete"
    if "no cost_components" in message:
        return "incomplete"
    return "unresolved"


def _check_percentage_basis_is_rollup(
    repo: Repository,
    issues: List[Issue],
    basis_line_ref: str,
    line_id: str,
    component_id: Optional[str] = None,
) -> None:
    basis = repo.get_line(basis_line_ref)
    if basis is None:
        issues.append(
            Issue(
                "error",
                "missing-basis",
                f"basis_line_ref {basis_line_ref!r} does not exist",
                line_id=line_id,
                component_id=component_id,
            )
        )
        return
    if not repo.has_children(basis_line_ref):
        issues.append(
            Issue(
                "error",
                "basis-not-rollup",
                f"basis_line_ref {basis_line_ref!r} must be a rollup line (have at "
                f"least one child), but it is a leaf",
                line_id=line_id,
                component_id=component_id,
            )
        )


# -- input-time checks (used by cli.py before writing a change) -----------


def check_percentage_basis_at_input(repo: Repository, basis_line_ref: str) -> Optional[str]:
    """Returns an error message if basis_line_ref is unusable as a percentage
    basis, else None. Used to reject bad input immediately in the CLI."""
    basis = repo.get_line(basis_line_ref)
    if basis is None:
        return f"basis_line_ref {basis_line_ref!r} does not exist"
    if not repo.has_children(basis_line_ref):
        return (
            f"basis_line_ref {basis_line_ref!r} is a leaf line (no children) -- "
            f"percentage basis must be a rollup line"
        )
    return None


def check_no_cycle_at_input(
    repo: Repository, line_id: str, proposed_basis_line_ref: str
) -> Optional[str]:
    """Would setting line_id's basis_line_ref to proposed_basis_line_ref create
    a cycle (self, ancestor, or descendant)? Returns an error message if so."""
    if proposed_basis_line_ref == line_id:
        return "a line cannot reference itself as basis_line_ref"
    descendant_ids = {d.line_id for d in repo.descendants_of(line_id)}
    if proposed_basis_line_ref in descendant_ids:
        return f"basis_line_ref {proposed_basis_line_ref!r} is a descendant of {line_id!r} (would create a cycle)"
    # Ancestors are always safe to reference for a *value* dependency in the
    # opposite direction of the tree edge, but if that ancestor (or anything
    # upstream of it) ever depends back on this line the calculator will
    # catch it at calc-time; the common accidental cases (self/descendant)
    # are caught here at input time.
    return None
