"""Upstream/downstream lineage tracing over a directed graph.

The public entry point is :func:`trace_lineage`. The graph is given as a
list of node ids (``nodes``) and a list of ``{"source", "target"}`` edge
objects (``edges``), where an edge points from ``source`` to ``target``.
Self-loops and cycles are legal; traversal is always finite.

Graph structure problems raise :class:`InvalidLineageInputError`, query
parameter problems raise :class:`InvalidLineageQueryError` and an
undeclared target raises :class:`UnknownLineageTargetError`; all three
subclass :class:`ValueError`.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

__all__ = [
    "trace_lineage",
    "InvalidLineageInputError",
    "InvalidLineageQueryError",
    "UnknownLineageTargetError",
]

DIRECTIONS = ("upstream", "downstream", "both")


class InvalidLineageInputError(ValueError):
    """The nodes/edges graph definition is malformed."""

    code = "INVALID_LINEAGE_INPUT"


class InvalidLineageQueryError(ValueError):
    """The query (target/direction/max_depth) is malformed."""

    code = "INVALID_LINEAGE_QUERY"


class UnknownLineageTargetError(ValueError):
    """The query target is not one of the declared nodes."""

    code = "UNKNOWN_LINEAGE_TARGET"


def _validate_graph(nodes: Any, edges: Any) -> Tuple[Set[str], List[Tuple[str, str]]]:
    if not isinstance(nodes, list) or not nodes:
        raise InvalidLineageInputError("nodes must be a non-empty list")
    node_set: Set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, str) or not node:
            raise InvalidLineageInputError(
                f"nodes[{index}] must be a non-empty string"
            )
        if node in node_set:
            raise InvalidLineageInputError(f"duplicate node: {node!r}")
        node_set.add(node)

    if not isinstance(edges, list):
        raise InvalidLineageInputError("edges must be a list")
    normalized: List[Tuple[str, str]] = []
    seen_edges: Set[Tuple[str, str]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise InvalidLineageInputError(f"edges[{index}] must be an object")
        if set(edge) != {"source", "target"}:
            raise InvalidLineageInputError(
                f"edges[{index}] must contain exactly 'source' and 'target'"
            )
        source = edge["source"]
        target = edge["target"]
        if not isinstance(source, str) or not isinstance(target, str):
            raise InvalidLineageInputError(
                f"edges[{index}] source and target must be strings"
            )
        for key, endpoint in (("source", source), ("target", target)):
            if endpoint not in node_set:
                raise InvalidLineageInputError(
                    f"edges[{index}].{key} references undeclared node "
                    f"{endpoint!r}"
                )
        pair = (source, target)
        if pair in seen_edges:
            raise InvalidLineageInputError(
                f"duplicate edge: {source!r} -> {target!r}"
            )
        seen_edges.add(pair)
        normalized.append(pair)
    return node_set, normalized


def _validate_query(
    target: Any,
    direction: Any,
    max_depth: Any,
    node_set: Set[str],
) -> None:
    if not isinstance(target, str):
        raise InvalidLineageQueryError("target must be a string")
    if direction not in DIRECTIONS:
        raise InvalidLineageQueryError(
            f"direction must be one of {list(DIRECTIONS)}, got {direction!r}"
        )
    if max_depth is not None:
        # bool is a subclass of int, but JSON true/false are not depths.
        if (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 0
        ):
            raise InvalidLineageQueryError(
                "max_depth must be null or an integer >= 0"
            )
    if target not in node_set:
        raise UnknownLineageTargetError(f"unknown target node: {target!r}")


def _traverse(
    target: str,
    edges: List[Tuple[str, str]],
    forward: bool,
    max_depth: Optional[int],
) -> Dict[str, int]:
    """Breadth-first walk from ``target``; returns shortest depth per node."""
    adjacency: Dict[str, List[str]] = {}
    for source, dest in edges:
        key, nxt = (source, dest) if forward else (dest, source)
        adjacency.setdefault(key, []).append(nxt)

    depths = {target: 0}
    frontier = deque([target])
    while frontier:
        current = frontier.popleft()
        depth = depths[current]
        if max_depth is not None and depth >= max_depth:
            continue
        for nxt in adjacency.get(current, ()):
            if nxt not in depths:
                depths[nxt] = depth + 1
                frontier.append(nxt)
    return depths


def _build_side(
    depths: Dict[str, int], edges: List[Tuple[str, str]]
) -> Dict[str, Any]:
    node_set = set(depths)
    ordered_nodes = sorted(depths.items(), key=lambda item: (item[1], item[0]))
    ordered_edges = sorted(
        (source, dest)
        for source, dest in edges
        if source in node_set and dest in node_set
    )
    return {
        "nodes": [{"id": node, "depth": depth} for node, depth in ordered_nodes],
        "edges": [
            {"source": source, "target": dest}
            for source, dest in ordered_edges
        ],
    }


def trace_lineage(
    nodes: Any,
    edges: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Trace upstream and/or downstream lineage from ``target``.

    :param nodes: non-empty list of unique node id strings.
    :param edges: list of ``{"source", "target"}`` objects whose endpoints
        are declared in ``nodes``; duplicate edges are rejected.
    :param target: node id to start from (depth 0).
    :param direction: ``"upstream"`` (walk edges in reverse),
        ``"downstream"`` (walk edges forward) or ``"both"`` (default).
    :param max_depth: ``None`` (default, unlimited) or an integer >= 0;
        ``0`` returns only the target itself.
    :returns: ``{"target", "direction", "max_depth", "upstream",
        "downstream"}`` where each side has ``nodes`` (``id``/``depth``
        entries sorted by depth then id) and ``edges`` (all original edges
        between included nodes, sorted by source then target). The side
        that was not queried is empty.
    :raises InvalidLineageInputError: malformed nodes/edges.
    :raises InvalidLineageQueryError: malformed target/direction/max_depth.
    :raises UnknownLineageTargetError: target is not a declared node.
    """
    node_set, normalized = _validate_graph(nodes, edges)
    _validate_query(target, direction, max_depth, node_set)

    empty_side: Dict[str, Any] = {"nodes": [], "edges": []}
    if direction in ("upstream", "both"):
        upstream = _build_side(
            _traverse(target, normalized, forward=False, max_depth=max_depth),
            normalized,
        )
    else:
        upstream = empty_side
    if direction in ("downstream", "both"):
        downstream = _build_side(
            _traverse(target, normalized, forward=True, max_depth=max_depth),
            normalized,
        )
    else:
        downstream = empty_side

    return {
        "target": target,
        "direction": direction,
        "max_depth": max_depth,
        "upstream": upstream,
        "downstream": downstream,
    }
