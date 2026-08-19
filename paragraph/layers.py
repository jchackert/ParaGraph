# Swift architectural layer classification, shared by the layered viz and
# the standards advisor. Non-Swift nodes (Python tooling, docs, observations)
# are classified out of the Swift layers entirely so advice and layer-violation
# analysis never apply to tooling code.
from __future__ import annotations

from pathlib import PurePosixPath

# Dependency direction flows down this list: a node may depend on nodes in the
# same layer or any layer BELOW it. view -> viewmodel -> service -> model -> core.
# "tooling" (scripts, build files, non-Swift code) and "other" sit outside the
# Swift stack and are exempt from violation analysis.
SWIFT_LAYERS = ["view", "viewmodel", "service", "model", "core"]
NON_SWIFT_LAYERS = ["tooling", "other"]
ALL_LAYERS = SWIFT_LAYERS + NON_SWIFT_LAYERS
LAYER_ORDER = {name: i for i, name in enumerate(ALL_LAYERS)}

_SERVICE_SUFFIXES = (
    "service", "manager", "client", "repository", "provider", "coordinator",
    "controller", "scheduler", "gateway", "api",
)
_VIEW_SUFFIXES = ("view", "screen", "page", "cell", "row", "sheet", "overlay")

_TOOLING_EXTENSIONS = (
    ".py", ".sh", ".rb", ".js", ".ts", ".mjs", ".yml", ".yaml", ".toml",
    ".json", ".lua", ".pl",
)


def is_swift_node(node: dict) -> bool:
    """True only for code nodes whose source is a .swift file.

    This is the gate every Swift-specific feature must pass through —
    Python tooling scripts in the same repo must never receive Swift advice
    or participate in Swift layer analysis.
    """
    if node.get("file_type") not in (None, "code"):
        return False
    sf = str(node.get("source_file") or "")
    return sf.endswith(".swift")


def classify_layer(node: dict) -> str:
    """Classify one graph node into an architectural layer.

    Heuristics use directory names first (most reliable), then label
    suffixes. Anything that is not Swift code lands in "tooling"
    (scripts/build files) or "other" (docs, observations, concepts).
    """
    sf = str(node.get("source_file") or "")
    label = str(node.get("label") or "").strip().rstrip("()").lstrip(".")

    if not is_swift_node(node):
        if sf.endswith(_TOOLING_EXTENSIONS) or "/scripts/" in f"/{sf}":
            return "tooling"
        return "other"

    parts = [p.lower() for p in PurePosixPath(sf).parts]
    lower = label.lower()

    # Directory signals (checked before name suffixes)
    if any(p in ("views", "view", "screens", "ui") for p in parts):
        # A ViewModel that lives in a Views/ folder is still a ViewModel
        if lower.endswith("viewmodel"):
            return "viewmodel"
        return "view"
    if any(p in ("viewmodels", "viewmodel") for p in parts):
        return "viewmodel"
    if any(p in ("services", "service", "networking", "network") for p in parts):
        return "service"
    if any(p in ("models", "model", "entities") for p in parts):
        if lower.endswith("viewmodel"):
            return "viewmodel"
        return "model"
    # Shared frameworks/packages (FooKit/Sources/..., Core/, Shared/)
    if any(p.endswith("kit") or p in ("core", "shared", "common", "foundation")
           for p in parts[:-1]):
        # Kit-internal structure still wins if explicit
        return "core"

    # Name-suffix signals
    if lower.endswith("viewmodel"):
        return "viewmodel"
    if lower.endswith(_VIEW_SUFFIXES):
        return "view"
    if lower.endswith(_SERVICE_SUFFIXES):
        return "service"
    if lower.endswith(("model", "entity", "record", "dto")):
        return "model"

    return "core"


def is_layer_violation(src_layer: str, tgt_layer: str) -> bool:
    """True when a dependency points UP the Swift stack.

    src depends on tgt. Allowed: same layer or any layer below (a View may
    use a ViewModel, a Service may use a Model). A Model reaching into a
    View, or a Service importing SwiftUI views, points upward — a layering
    violation. Edges touching tooling/other are never violations.
    """
    if src_layer not in SWIFT_LAYERS or tgt_layer not in SWIFT_LAYERS:
        return False
    return LAYER_ORDER[tgt_layer] < LAYER_ORDER[src_layer]


def layer_map(nodes: list[dict]) -> dict[str, str]:
    """node_id -> layer for a graph.json node list."""
    return {n["id"]: classify_layer(n) for n in nodes}
