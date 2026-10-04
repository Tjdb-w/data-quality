"""Lineage path explanation and impact-scope queries.

The public entry points are :func:`explain_lineage_paths` (node-level graph,
same shape as :func:`data_quality.trace_lineage`) and
:func:`explain_field_lineage_paths` (table/column graph, same shape as
:func:`data_quality.trace_field_lineage`). Edges point from ``source`` to
``target``: upstream paths follow edges backwards from the target to its
sources, downstream paths follow them forwards to every directly and
indirectly dependent object.

Every simple path is enumerated, so the same node may occur in several
paths while a single path never repeats a node; a back-edge to a node
already on the current path closes a cycle and is reported instead of
being followed forever.

Graph structure problems and bad query arguments reuse the existing
exceptions (and therefore their CLI error codes). A target that is not
declared in the graph raises :class:`LineageNodeNotFoundError`, whose
:attr:`LineageNodeNotFoundError.code` is ``"LINEAGE_NODE_NOT_FOUND"``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from . import field_lineage as _field_lineage
from . import lineage as _lineage

__all__ = [
    "explain_lineage_paths",
    "explain_field_lineage_paths",
    "LineageNodeNotFoundError",
]

# A node id is either a plain string (node-level graph) or a
# ``(table, column)`` pair (field-level graph).
NodeId = Any
# A path is carried as (nodes in traversal order, original directed edge
# ids ``(source, target)`` in traversal order).
_Path = Tuple[Tuple[NodeId, ...], Tuple[Tuple[NodeId, NodeId], ...]]


class LineageNodeNotFoundError(ValueError):
    """The queried target node is not declared in the graph."""

    code = "LINEAGE_NODE_NOT_FOUND"


def _direct_neighbors(
    adjacency: Dict[NodeId, List[NodeId]],
    forward: bool,
) -> Dict[NodeId, List[Tuple[Tuple[NodeId, NodeId], NodeId]]]:
    """Tag each adjacency entry with the original directed edge id.

    ``forward`` selects the edge orientation: downstream traversal follows
    ``node -> other``, upstream traversal follows ``other -> node``; either
    way the edge id keeps its original ``(source, target)`` direction.
    """
    directed: Dict[NodeId, List[Tuple[Tuple[NodeId, NodeId], NodeId]]] = {}
    for node_id, linked in adjacency.items():
        steps: List[Tuple[Tuple[NodeId, NodeId], NodeId]] = []
        for other in linked:
            edge_id = (node_id, other) if forward else (other, node_id)
            steps.append((edge_id, other))
        directed[node_id] = steps
    return directed


def _enumerate_paths(
    start: NodeId,
    neighbors: Dict[NodeId, List[Tuple[Tuple[NodeId, NodeId], NodeId]]],
    max_depth: Optional[int],
) -> Tuple[List[_Path], List[NodeId], List[Tuple[NodeId, NodeId]]]:
    """Depth-first enumeration of every simple path starting at ``start``.

    Returns the paths (unsorted), the nodes lying on detected cycles and
    the directed edges that make those cycles up. A back-edge to a node on
    the current route is never followed: the route segment found so far is
    kept as a path, and the closed cycle is recorded.
    """
    paths: List[_Path] = []
    cycle_nodes: Set[NodeId] = set()
    cycle_edges: Set[Tuple[NodeId, NodeId]] = set()

    def visit(route_nodes: List[NodeId], route_edges: List[Tuple[NodeId, NodeId]]) -> None:
        current = route_nodes[-1]
        depth = len(route_edges)
        if max_depth is not None and depth >= max_depth:
            paths.append((tuple(route_nodes), tuple(route_edges)))
            return

        extended = False
        for edge_id, nxt in neighbors.get(current, ()):  # deterministic input order
            if nxt in route_nodes:
                loop_start = route_nodes.index(nxt)
                # Nodes/edges from the repeated node up to the current node,
                # plus the closing back-edge, describe the whole cycle.
                cycle_nodes.update(route_nodes[loop_start:])
                cycle_edges.update(route_edges[loop_start:])
                cycle_edges.add(edge_id)
                continue
            extended = True
            route_nodes.append(nxt)
            route_edges.append(edge_id)
            visit(route_nodes, route_edges)
            route_nodes.pop()
            route_edges.pop()

        if not extended and (depth > 0 or max_depth == 0):
            # A terminal segment with no forward continuation. A zero-edge
            # target-only segment is reported only when the query itself is
            # bounded to depth 0; an isolated target with no relations
            # yields no paths.
            paths.append((tuple(route_nodes), tuple(route_edges)))

    visit([start], [])
    return paths, list(cycle_nodes), list(cycle_edges)


def _sort_paths(paths: List[_Path]) -> List[_Path]:
    """Sort by path depth, then node ids, then edge ids."""
    return sorted(paths, key=lambda path: (len(path[1]), path[0], path[1]))


def _edge_lookup(
    edges: List[Dict[str, Any]]
) -> Dict[Tuple[NodeId, NodeId], Dict[str, Any]]:
    lookup: Dict[Tuple[NodeId, NodeId], Dict[str, Any]] = {}
    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        if isinstance(source, str):
            key = (source, target)
        else:
            key = (
                (source["table"], source["column"]),
                (target["table"], target["column"]),
            )
        lookup[key] = edge
    return lookup


def _build_node_side(
    start: str,
    edge_by_id: Dict[Tuple[str, str], Dict[str, str]],
    neighbors: Optional[Dict[str, List[Tuple[Tuple[str, str], str]]]],
    max_depth: Optional[int],
) -> Dict[str, Any]:
    if neighbors is None:
        return {"paths": [], "cycle": False, "cycle_nodes": [], "cycle_edges": []}

    raw_paths, cycle_node_set, cycle_edge_set = _enumerate_paths(
        start, neighbors, max_depth
    )

    paths: List[Dict[str, Any]] = []
    for route_nodes, route_edges in _sort_paths(raw_paths):
        steps: List[Dict[str, Any]] = []
        for position, node_id in enumerate(route_nodes):
            steps.append(
                {
                    "node": {"id": node_id, "depth": position},
                    "edge": dict(edge_by_id[route_edges[position - 1]])
                    if position > 0
                    else None,
                }
            )
        paths.append({"depth": len(route_edges), "nodes": steps})

    return {
        "paths": paths,
        "cycle": bool(cycle_edge_set),
        "cycle_nodes": sorted(cycle_node_set),
        "cycle_edges": [dict(edge_by_id[eid]) for eid in sorted(cycle_edge_set)],
    }


def _field_ref(field_id: Tuple[str, str]) -> Dict[str, str]:
    return {"table": field_id[0], "column": field_id[1]}


def _build_field_side(
    start: Tuple[str, str],
    edge_by_id: Dict[Tuple[Any, Any], Dict[str, Any]],
    neighbors: Optional[
        Dict[Tuple[str, str], List[Tuple[Tuple[Any, Any], Tuple[str, str]]]]
    ],
    max_depth: Optional[int],
) -> Dict[str, Any]:
    if neighbors is None:
        return {"paths": [], "cycle": False, "cycle_nodes": [], "cycle_edges": []}

    raw_paths, cycle_node_set, cycle_edge_set = _enumerate_paths(
        start, neighbors, max_depth
    )

    paths: List[Dict[str, Any]] = []
    for route_nodes, route_edges in _sort_paths(raw_paths):
        steps: List[Dict[str, Any]] = []
        for position, field_id in enumerate(route_nodes):
            edge = None
            if position > 0:
                original = edge_by_id[route_edges[position - 1]]
                edge = {
                    "source": dict(original["source"]),
                    "target": dict(original["target"]),
                }
            steps.append(
                {
                    "node": {
                        "table": field_id[0],
                        "column": field_id[1],
                        "depth": position,
                    },
                    "edge": edge,
                }
            )
        paths.append({"depth": len(route_edges), "nodes": steps})

    return {
        "paths": paths,
        "cycle": bool(cycle_edge_set),
        "cycle_nodes": [_field_ref(fid) for fid in sorted(cycle_node_set)],
        "cycle_edges": [
            {
                "source": dict(edge_by_id[eid]["source"]),
                "target": dict(edge_by_id[eid]["target"]),
            }
            for eid in sorted(cycle_edge_set)
        ],
    }


def explain_lineage_paths(
    nodes: Any,
    edges: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Explain the concrete upstream source paths and downstream impact of
    ``target`` over a node-level lineage graph.

    :param nodes: non-empty list of distinct non-empty node id strings,
        exactly as accepted by :func:`data_quality.trace_lineage`.
    :param edges: list of ``{"source": ..., "target": ...}`` objects whose
        endpoints are declared in ``nodes``; duplicate edges are rejected.
    :param target: node id to explain; the first node of every returned
        path.
    :param direction: ``"upstream"``, ``"downstream"`` or ``"both"``
        (default ``"both"``).
    :param max_depth: ``None`` for unlimited (default) or an integer ``>= 0``
        bounding the number of edges in each path; ``0`` yields one
        target-only path. Booleans are not integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"paths", "cycle", "cycle_nodes", "cycle_edges"},
        "downstream": {...}}``. Each path is ``{"depth", "nodes"}`` where
        every step is ``{"node": {"id", "depth"}, "edge": <original edge or
        null>}``; ``edge`` is the edge that leads to the node and is null
        on the target. Paths are sorted by ``(depth, node ids, edge ids)``.
        When a cycle exists, ``cycle`` is true and ``cycle_nodes`` /
        ``cycle_edges`` describe it while every other independent simple
        path is still returned. A declared target without relations in a
        direction is a normal result with ``paths: []``.
    :raises ValueError: the existing ``InvalidLineageInputError`` /
        ``InvalidLineageQueryError`` for malformed graphs or queries, and
        :class:`LineageNodeNotFoundError`
        (``code == "LINEAGE_NODE_NOT_FOUND"``) for an undeclared target.
    """
    declared = _lineage._validate_graph(nodes, edges)
    _lineage._validate_query(target, direction, max_depth)

    if target not in declared:
        raise LineageNodeNotFoundError(
            f"target is not declared in nodes: {target!r}"
        )

    outgoing, incoming = _lineage._build_adjacency(edges)
    edge_by_id = _edge_lookup(edges)
    return {
        "target": target,
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _build_node_side(
            target,
            edge_by_id,
            _direct_neighbors(incoming, forward=False)
            if direction in ("upstream", "both")
            else None,
            max_depth,
        ),
        "downstream": _build_node_side(
            target,
            edge_by_id,
            _direct_neighbors(outgoing, forward=True)
            if direction in ("downstream", "both")
            else None,
            max_depth,
        ),
    }


def explain_field_lineage_paths(
    fields: Any,
    edges: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Explain the concrete upstream source paths and downstream impact of
    ``target`` over a table/column field-level lineage graph.

    :param fields: object mapping non-empty table ids to lists of distinct
        non-empty field ids, exactly as accepted by
        :func:`data_quality.trace_field_lineage`.
    :param edges: list of ``{"source": ..., "target": ...}`` objects whose
        endpoints are ``{"table", "column"}`` objects declared in
        ``fields``; duplicate edges are rejected, self-loops and cycles are
        legal.
    :param target: ``{"table", "column"}`` field to explain.
    :param direction: ``"upstream"``, ``"downstream"`` or ``"both"``
        (default ``"both"``).
    :param max_depth: ``None`` for unlimited (default) or an integer ``>= 0``
        bounding the number of edges in each path; ``0`` yields one
        target-only path. Booleans are not integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"paths", "cycle", "cycle_nodes", "cycle_edges"},
        "downstream": {...}}``. Each path is ``{"depth", "nodes"}`` where
        every step is ``{"node": {"table", "column", "depth"}, "edge":
        <original edge or null>}``; ``edge`` leads to the node and is null
        on the target. Paths sort by ``(depth, node ids, edge ids)``. A
        declared target without relations in a direction returns
        ``paths: []``; cycles set ``cycle`` true, list the cycle nodes and
        edges and never suppress the other independent paths.
    :raises ValueError: the existing ``InvalidFieldLineageInputError`` /
        ``InvalidFieldLineageQueryError`` for malformed graphs or queries,
        and :class:`LineageNodeNotFoundError`
        (``code == "LINEAGE_NODE_NOT_FOUND"``) for an undeclared target.
    """
    declared = _field_lineage._validate_fields(fields)
    _field_lineage._validate_edges(edges, declared)
    _field_lineage._validate_query(target, direction, max_depth)

    start = _field_lineage._field_id(target)
    if start not in declared:
        raise LineageNodeNotFoundError(
            f"target is not declared in fields: {target!r}"
        )

    outgoing, incoming = _field_lineage._build_adjacency(edges)
    edge_by_id = _edge_lookup(edges)
    return {
        "target": _field_ref(start),
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _build_field_side(
            start,
            edge_by_id,
            _direct_neighbors(incoming, forward=False)
            if direction in ("upstream", "both")
            else None,
            max_depth,
        ),
        "downstream": _build_field_side(
            start,
            edge_by_id,
            _direct_neighbors(outgoing, forward=True)
            if direction in ("downstream", "both")
            else None,
            max_depth,
        ),
    }
