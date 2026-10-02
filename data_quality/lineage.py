"""Upstream/downstream lineage tracing over a declared node/edge graph.

The public entry point is :func:`trace_lineage`. Edges point from
``source`` to ``target``: upstream traversal follows edges backwards,
downstream traversal follows them forwards.

Graph structure problems raise :class:`InvalidLineageInputError`, bad query
arguments raise :class:`InvalidLineageQueryError` and a target that is not
declared in ``nodes`` raises :class:`UnknownLineageTargetError`; all three
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

VALID_DIRECTIONS = ("upstream", "downstream", "both")
_EDGE_KEYS = frozenset({"source", "target"})


class InvalidLineageInputError(ValueError):
    """The lineage graph (``nodes``/``edges``) is missing or malformed."""


class InvalidLineageQueryError(ValueError):
    """The query arguments (``target``/``direction``/``max_depth``) are bad."""


class UnknownLineageTargetError(ValueError):
    """The queried ``target`` is not declared among ``nodes``."""


def _validate_graph(nodes: Any, edges: Any) -> Set[str]:
    if not isinstance(nodes, list):
        raise InvalidLineageInputError("nodes must be a list of node ids")
    if not nodes:
        raise InvalidLineageInputError("nodes must not be empty")

    declared: Set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, str):
            raise InvalidLineageInputError(
                f"nodes[{index}] must be a string"
            )
        if not node:
            raise InvalidLineageInputError(
                f"nodes[{index}] must be a non-empty string"
            )
        if node in declared:
            raise InvalidLineageInputError(f"duplicate node id: {node!r}")
        declared.add(node)

    if not isinstance(edges, list):
        raise InvalidLineageInputError("edges must be a list")

    seen_edges: Set[Tuple[str, str]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise InvalidLineageInputError(f"edges[{index}] must be an object")

        keys = set(edge)
        if keys != _EDGE_KEYS:
            missing = sorted(_EDGE_KEYS - keys)
            if missing:
                raise InvalidLineageInputError(
                    f"edges[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _EDGE_KEYS)
            raise InvalidLineageInputError(
                f"edges[{index}] has unsupported keys: {unknown}"
            )

        source = edge["source"]
        target = edge["target"]
        if not isinstance(source, str) or source not in declared:
            raise InvalidLineageInputError(
                f"edges[{index}].source is not a declared node: {source!r}"
            )
        if not isinstance(target, str) or target not in declared:
            raise InvalidLineageInputError(
                f"edges[{index}].target is not a declared node: {target!r}"
            )

        pair = (source, target)
        if pair in seen_edges:
            raise InvalidLineageInputError(
                f"duplicate edge: {source!r} -> {target!r}"
            )
        seen_edges.add(pair)

    return declared


def _validate_query(
    target: Any, direction: Any, max_depth: Any
) -> None:
    if not isinstance(target, str):
        raise InvalidLineageQueryError("target must be a node id string")
    if direction not in VALID_DIRECTIONS:
        raise InvalidLineageQueryError(
            f"direction must be one of {list(VALID_DIRECTIONS)}, "
            f"got {direction!r}"
        )
    if max_depth is not None:
        # bool is a subclass of int, but JSON true/false are not depths.
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise InvalidLineageQueryError(
                "max_depth must be null or an integer >= 0"
            )
        if max_depth < 0:
            raise InvalidLineageQueryError(
                f"max_depth must be >= 0, got {max_depth}"
            )


def _build_adjacency(
    edges: List[Dict[str, str]]
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    outgoing: Dict[str, List[str]] = {}
    incoming: Dict[str, List[str]] = {}
    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)
    return outgoing, incoming


def _walk(
    start: str,
    neighbors: Dict[str, List[str]],
    max_depth: Optional[int],
) -> Dict[str, int]:
    """BFS from ``start``; first visit wins, so depths are shortest."""
    depths = {start: 0}
    if max_depth == 0:
        return depths

    queue = deque([(start, 0)])
    while queue:
        node, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        next_depth = depth + 1
        for nxt in neighbors.get(node, ()):
            if nxt not in depths:
                depths[nxt] = next_depth
                queue.append((nxt, next_depth))
    return depths


def _side(
    depths: Optional[Dict[str, int]],
    edges: List[Dict[str, str]],
) -> Dict[str, Any]:
    if depths is None:
        return {"nodes": [], "edges": []}

    nodes = [
        {"id": node_id, "depth": depth}
        for node_id, depth in sorted(depths.items(), key=lambda item: (item[1], item[0]))
    ]
    side_edges = [
        edge
        for edge in edges
        if edge["source"] in depths and edge["target"] in depths
    ]
    side_edges.sort(key=lambda edge: (edge["source"], edge["target"]))
    return {"nodes": nodes, "edges": side_edges}


def trace_lineage(
    nodes: Any,
    edges: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Trace upstream/downstream lineage of ``target``.

    :param nodes: non-empty list of distinct non-empty node id strings.
    :param edges: list of ``{"source": ..., "target": ...}`` objects whose
        endpoints are declared in ``nodes``; duplicate edges are rejected.
    :param target: node id to trace from; depth 0 in every returned side.
    :param direction: ``"upstream"``, ``"downstream"`` or ``"both"``
        (default ``"both"``).
    :param max_depth: ``None`` for unlimited (default) or an integer ``>= 0``;
        ``0`` returns only ``target``. Booleans are not integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"nodes", "edges"}, "downstream": {"nodes", "edges"}}``.
        Each populated side lists nodes as ``{"id", "depth"}`` sorted by
        ``(depth, id)`` and includes every original edge whose endpoints are
        in the side, sorted by ``(source, target)``. The side not queried is
        returned empty.
    :raises ValueError: :class:`InvalidLineageInputError`,
        :class:`InvalidLineageQueryError` or
        :class:`UnknownLineageTargetError`.
    """
    declared = _validate_graph(nodes, edges)
    _validate_query(target, direction, max_depth)

    if target not in declared:
        raise UnknownLineageTargetError(
            f"target is not declared in nodes: {target!r}"
        )

    outgoing, incoming = _build_adjacency(edges)

    upstream_depths: Optional[Dict[str, int]] = None
    downstream_depths: Optional[Dict[str, int]] = None
    if direction in ("upstream", "both"):
        upstream_depths = _walk(target, incoming, max_depth)
    if direction in ("downstream", "both"):
        downstream_depths = _walk(target, outgoing, max_depth)

    return {
        "target": target,
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _side(upstream_depths, edges),
        "downstream": _side(downstream_depths, edges),
    }
