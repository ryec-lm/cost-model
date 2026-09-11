"""Default starting point for a brand-new tree: the 10 FTA Standard Cost
Categories (SCC), used as the top level (WBS 10, 20, ... 100) of a capital
cost estimate.

This is purely a convenience seed applied once, when a tree file doesn't
exist yet - nothing else in the codebase assumes these categories exist,
are named this way, or keep this numbering. Rename, delete, reorder, or
add to them freely; the PBS hierarchy stays entirely user-defined below
(and, if you want, above) this starting point.
"""

from __future__ import annotations

from .models import PBSLine, PBSTree
from .storage import PBSRepository

FTA_SCC_CATEGORIES = [
    ("10", "Guideway & Track Elements"),
    ("20", "Stations, Stops, Terminals, Intermodal"),
    ("30", "Support Facilities: Yards, Shops, Administrative Buildings"),
    ("40", "Sitework & Special Conditions"),
    ("50", "Systems"),
    ("60", "ROW, Land, Existing Improvements"),
    ("70", "Vehicles"),
    ("80", "Professional Services"),
    ("90", "Unallocated Contingency"),
    ("100", "Finance Charges"),
]


def seed_fta_scc_tree() -> PBSTree:
    lines = {}
    for i, (number, name) in enumerate(FTA_SCC_CATEGORIES):
        line_id = f"L{i + 1:03d}"
        lines[line_id] = PBSLine(
            line_id=line_id,
            line_name=name,
            sort_index=i,
            wbs_override=number,
        )
    return lines


def load_or_seed(repo: PBSRepository) -> PBSTree:
    """Load a tree, seeding it with the FTA SCC categories first if the
    backing file doesn't exist yet (a brand-new tree)."""
    path = getattr(repo, "path", None)
    if path is not None and not path.exists():
        lines = seed_fta_scc_tree()
        repo.save(lines)
        return lines
    return repo.load()
