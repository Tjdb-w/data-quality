"""Lineage path explanation and impact scope queries.

The public entry point is :func:`explain_lineage_paths`. The graph declares
table-level nodes (``tables``), field-level nodes (``fields``) and processing
nodes (``processes``); edges point from ``source`` to ``target`` and carry a
caller-chosen ``type``. Upstream queries follow edges backwards from the
target towards the source nodes, downstream queries follow them forwards
over the directly and indirectly affected nodes.

Every enumerated path keeps the ordered nodes, the direct edge that caused
each step and each node's stable id, type and displayable table/field
names. A node may appear on several paths, but a single path never extends
through a cycle: a path that would revisit one of its own nodes is closed
with ``cycle`` set to ``true`` while the determined path segment, the nodes
on the cycle and every other independent path are preserved.

A target that is not declared in the graph is not an exception: the result
is ``{"success": False, "code": "LINEAGE_NODE_NOT_FOUND", ...}``. A declared
target without relations in the queried direction returns
``{"success": True, "paths": []}``.

Malformed graphs raise :class:`InvalidLineagePathInputError` and bad query
arguments raise :class:`InvalidLineagePathQueryError`; both subclass
:class:`ValueError`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

__all__ = [
    "explain_lineage_paths",
    "InvalidLineagePathInputError",
    "InvalidLineagePathQueryError",
    "LINEAGE_NODE_NOT_FOUND",
    "VALID_DIRECTIONS",
    "NODE_TYPES",
]

VALID_DIRECTIONS = ("upstream", "downstream", "both")
NODE_TYPES = ("table", "field", "process")
LINEAGE_NODE_NOT_FOUND = "LINEAGE_NODE_NOT_FOUND"

_GRAPH_KEYS = frozenset({"tables", "fields", "processes", "edges"})
_EDGE_KEYS = frozenset({"source", "target", "type"})

# A node is identified by (kind, primary, secondary): table nodes are
# ("table", table, None), field nodes are ("field", table, column) and
# process nodes are ("process", process, None).
NodeId = Tuple[str, str, Optional[str]]

# A canonical edge: endpoints are node ids, plus the declared edge type.
Edge = Dict[str, Any]


class InvalidLineagePathInputError(ValueError):
    """The lineage graph (``tables``/``fields``/``processes``/``edges``)
    is missing or malformed."""


class InvalidLineagePathQueryError(ValueError):
    """The query arguments (``target``/``direction``) are bad."""


def _node_id(node: NodeId) -> str:
    kind, primary, secondary = node
    if kind == "field":
        return f"field:{primary}.{secondary}"
    return f"{kind}:{primary}"


def _edge_id(edge: Edge) -> str:
    return f"{_node_id(edge['source'])}->{_node_id(edge['target'])}"


def _check_keys(
    obj: Dict[str, Any], expected: frozenset, where: str, error: type
) -> None:
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise error(f"{where} has unsupported keys: {unknown}")


def _validate_tables(tables: Any, declared: Set[NodeId]) -> None:
    if not isinstance(tables, list):
        raise InvalidLineagePathInputError(
            "tables must be a list of table ids"
        )
    seen: Set[str] = set()
    for index, table in enumerate(tables):
        if not isinstance(table, str) or not table:
            raise InvalidLineagePathInputError(
                f"tables[{index}] must be a non-empty string"
            )
        if table in seen:
            raise InvalidLineagePathInputError(
                f"duplicate table id: {table!r}"
            )
        seen.add(table)
        declared.add(("table", table, None))


def _validate_processes(processes: Any, declared: Set[NodeId]) -> None:
    if not isinstance(processes, list):
        raise InvalidLineagePathInputError(
            "processes must be a list of process ids"
        )
    seen: Set[str] = set()
    for index, process in enumerate(processes):
        if not isinstance(process, str) or not process:
            raise InvalidLineagePathInputError(
                f"processes[{index}] must be a non-empty string"
            )
        if process in seen:
            raise InvalidLineagePathInputError(
                f"duplicate process id: {process!r}"
            )
        seen.add(process)
        declared.add(("process", process, None))


def _validate_fields(fields: Any, declared: Set[NodeId]) -> None:
    if not isinstance(fields, dict):
        raise InvalidLineagePathInputError(
            "fields must be an object mapping table ids to field id lists"
        )
    for table, columns in fields.items():
        if not isinstance(table, str) or not table:
            raise InvalidLineagePathInputError(
                f"fields key must be a non-empty table id string: {table!r}"
            )
        if ("table", table, None) not in declared:
            raise InvalidLineagePathInputError(
                f"fields declares fields for an undeclared table: {table!r}"
            )
        if not isinstance(columns, list):
            raise InvalidLineagePathInputError(
                f"fields[{table!r}] must be a list of field ids"
            )
        seen_columns: Set[str] = set()
        for index, column in enumerate(columns):
            if not isinstance(column, str) or not column:
                raise InvalidLineagePathInputError(
                    f"fields[{table!r}][{index}] must be a non-empty "
                    "field id string"
                )
            if column in seen_columns:
                raise InvalidLineagePathInputError(
                    f"duplicate field id in table {table!r}: {column!r}"
                )
            seen_columns.add(column)
            declared.add(("field", table, column))


def _parse_endpoint(
    endpoint: Any, where: str, error: type
) -> NodeId:
    """Parse a node reference into a node id (structure only).

    A node reference is exactly one of ``{"table": ...}`` (table-level
    node), ``{"table": ..., "field": ...}`` (field-level node) or
    ``{"process": ...}`` (processing node).
    """
    if not isinstance(endpoint, dict):
        raise error(f"{where} must be an object")
    keys = set(endpoint)
    if keys == {"table"}:
        table = endpoint["table"]
        if not isinstance(table, str) or not table:
            raise error(f"{where}.table must be a non-empty string")
        return ("table", table, None)
    if keys == {"table", "field"}:
        table = endpoint["table"]
        column = endpoint["field"]
        if not isinstance(table, str) or not table:
            raise error(f"{where}.table must be a non-empty string")
        if not isinstance(column, str) or not column:
            raise error(f"{where}.field must be a non-empty string")
        return ("field", table, column)
    if keys == {"process"}:
        process = endpoint["process"]
        if not isinstance(process, str) or not process:
            raise error(f"{where}.process must be a non-empty string")
        return ("process", process, None)
    raise error(
        f"{where} must have exactly the keys ['table'], ['table', 'field'] "
        f"or ['process'], got {sorted(keys)}"
    )


def _validate_edges(edges: Any, declared: Set[NodeId]) -> List[Edge]:
    if not isinstance(edges, list):
        raise InvalidLineagePathInputError("edges must be a list")

    canonical: List[Edge] = []
    seen_edges: Set[Tuple[NodeId, NodeId]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise InvalidLineagePathInputError(
                f"edges[{index}] must be an object"
            )
        _check_keys(edge, _EDGE_KEYS, f"edges[{index}]",
                    InvalidLineagePathInputError)

        edge_type = edge["type"]
        if not isinstance(edge_type, str) or not edge_type:
            raise InvalidLineagePathInputError(
                f"edges[{index}].type must be a non-empty string"
            )

        source = _parse_endpoint(
            edge["source"], f"edges[{index}].source",
            InvalidLineagePathInputError,
        )
        target = _parse_endpoint(
            edge["target"], f"edges[{index}].target",
            InvalidLineagePathInputError,
        )
        if source not in declared:
            raise InvalidLineagePathInputError(
                f"edges[{index}].source is not a declared node: "
                f"{edge['source']!r}"
            )
        if target not in declared:
            raise InvalidLineagePathInputError(
                f"edges[{index}].target is not a declared node: "
                f"{edge['target']!r}"
            )

        pair = (source, target)
        if pair in seen_edges:
            raise InvalidLineagePathInputError(
                f"duplicate edge: {_node_id(source)!r} -> "
                f"{_node_id(target)!r}"
            )
        seen_edges.add(pair)
        canonical.append(
            {"source": source, "target": target, "type": edge_type}
        )
    return canonical


def _validate_graph(graph: Any) -> Tuple[Set[NodeId], List[Edge]]:
    if not isinstance(graph, dict):
        raise InvalidLineagePathInputError("graph must be an object")
    _check_keys(graph, _GRAPH_KEYS, "graph", InvalidLineagePathInputError)

    declared: Set[NodeId] = set()
    _validate_tables(graph["tables"], declared)
    _validate_processes(graph["processes"], declared)
    _validate_fields(graph["fields"], declared)
    edges = _validate_edges(graph["edges"], declared)
    return declared, edges


def _validate_query(target: Any, direction: Any) -> NodeId:
    node = _parse_endpoint(target, "target", InvalidLineagePathQueryError)
    if direction not in VALID_DIRECTIONS:
        raise InvalidLineagePathQueryError(
            f"direction must be one of {list(VALID_DIRECTIONS)}, "
            f"got {direction!r}"
        )
    return node


def _build_adjacency(
    edges: List[Edge],
) -> Tuple[
    Dict[NodeId, List[Tuple[Edge, NodeId]]],
    Dict[NodeId, List[Tuple[Edge, NodeId]]],
]:
    """Neighbour lists as (edge, neighbour) pairs, sorted for determinism.

    ``outgoing`` maps a node to its downstream neighbours, ``incoming`` to
    its upstream (direct parent) neighbours.
    """
    outgoing: Dict[NodeId, List[Tuple[Edge, NodeId]]] = {}
    incoming: Dict[NodeId, List[Tuple[Edge, NodeId]]] = {}
    for edge in edges:
        outgoing.setdefault(edge["source"], []).append((edge, edge["target"]))
        incoming.setdefault(edge["target"], []).append((edge, edge["source"]))
    for neighbours in list(outgoing.values()) + list(incoming.values()):
        neighbours.sort(
            key=lambda item: (_node_id(item[1]), _edge_id(item[0]))
        )
    return outgoing, incoming


def _enumerate_paths(
    start: NodeId,
    neighbors: Dict[NodeId, List[Tuple[Edge, NodeId]]],
) -> List[Dict[str, Any]]:
    """Enumerate all simple paths from ``start``.

    Returns raw paths as ``{"nodes", "edges", "cycle_nodes", "cycle_edge"}``.
    A path closes when it reaches a node without further neighbours or when
    the next hop would revisit a node already on the path (a cycle); an
    isolated ``start`` yields no path at all.
    """
    paths: List[Dict[str, Any]] = []

    def visit(
        node: NodeId,
        nodes_so_far: List[NodeId],
        edges_so_far: List[Edge],
        in_path: Set[NodeId],
    ) -> None:
        branches = neighbors.get(node, ())
        if not branches:
            # A terminal node (a source upstream, a sink downstream). An
            # isolated start node has no relations and yields no path.
            if edges_so_far:
                paths.append(
                    {
                        "nodes": list(nodes_so_far),
                        "edges": list(edges_so_far),
                        "cycle_nodes": [],
                        "cycle_edge": None,
                    }
                )
            return
        for edge, nxt in branches:
            if nxt in in_path:
                # Closing the loop here would extend this path through a
                # cycle: keep the determined segment and the cycle nodes.
                first = nodes_so_far.index(nxt)
                paths.append(
                    {
                        "nodes": list(nodes_so_far),
                        "edges": list(edges_so_far),
                        "cycle_nodes": list(nodes_so_far[first:]),
                        "cycle_edge": edge,
                    }
                )
                continue
            visit(
                nxt,
                nodes_so_far + [nxt],
                edges_so_far + [edge],
                in_path | {nxt},
            )

    visit(start, [start], [], {start})
    return paths


def _node_result(node: NodeId) -> Dict[str, Any]:
    kind, primary, secondary = node
    return {
        "id": _node_id(node),
        "type": kind,
        "table": primary if kind in ("table", "field") else None,
        "field": secondary if kind == "field" else None,
    }


def _edge_result(edge: Edge) -> Dict[str, Any]:
    return {
        "id": _edge_id(edge),
        "type": edge["type"],
        "source": _node_id(edge["source"]),
        "target": _node_id(edge["target"]),
    }


def _path_result(path: Dict[str, Any], direction: str) -> Dict[str, Any]:
    nodes = path["nodes"]
    edges = path["edges"]
    steps = [
        {"position": 0, "node": _node_result(nodes[0]), "edge": None}
    ]
    for position, (edge, node) in enumerate(zip(edges, nodes[1:]), start=1):
        steps.append(
            {
                "position": position,
                "node": _node_result(node),
                "edge": _edge_result(edge),
            }
        )
    cycle_edge = path["cycle_edge"]
    return {
        "direction": direction,
        "depth": len(edges),
        "cycle": bool(path["cycle_nodes"]),
        "steps": steps,
        "cycle_nodes": [_node_result(node) for node in path["cycle_nodes"]],
        "cycle_edge": _edge_result(cycle_edge) if cycle_edge is not None else None,
    }


def _path_sort_key(path: Dict[str, Any]) -> Tuple[Any, ...]:
    direction_order = 0 if path["direction"] == "upstream" else 1
    node_ids = tuple(step["node"]["id"] for step in path["steps"])
    edge_ids = tuple(
        step["edge"]["id"] for step in path["steps"] if step["edge"] is not None
    )
    if path["cycle_edge"] is not None:
        edge_ids = edge_ids + (path["cycle_edge"]["id"],)
    return (direction_order, path["depth"], node_ids, edge_ids)


def _target_echo(node: NodeId) -> Dict[str, str]:
    kind, primary, secondary = node
    if kind == "table":
        return {"table": primary}
    if kind == "field":
        return {"table": primary, "field": secondary}
    return {"process": primary}


def explain_lineage_paths(
    graph: Any,
    target: Any,
    direction: Any = "both",
) -> Dict[str, Any]:
    """Explain upstream source paths and the downstream impact scope.

    :param graph: object with exactly the keys ``tables`` (list of distinct
        non-empty table ids), ``processes`` (list of distinct non-empty
        process ids), ``fields`` (object mapping declared table ids to
        lists of distinct non-empty field ids) and ``edges`` (list of
        ``{"source": ..., "target": ..., "type": ...}`` objects whose
        endpoints are node references declared in the graph and whose type
        is a non-empty string; duplicate ``(source, target)`` pairs are
        rejected, self-loops and cycles are legal). A node reference is
        ``{"table": ...}``, ``{"table": ..., "field": ...}`` or
        ``{"process": ...}``.
    :param target: node reference to start from; position 0 of every path.
    :param direction: ``"upstream"`` (back to the source nodes),
        ``"downstream"`` (over the affected nodes) or ``"both"``
        (default ``"both"``).
    :returns: ``{"success": True, "target", "direction", "paths"}`` where
        each path is ``{"direction", "depth", "cycle", "steps",
        "cycle_nodes", "cycle_edge"}``. Steps are ordered
        ``{"position", "node", "edge"}`` entries; the node carries the
        stable ``id``, the node ``type`` and the displayable ``table`` /
        ``field`` names, the edge is the direct edge that caused the step
        (``None`` at position 0). Paths are sorted by direction (upstream
        first), depth, node ids and edge ids, so identical inputs always
        produce identical results. A declared target without relations in
        the queried direction yields ``"paths": []``. An undeclared target
        yields ``{"success": False, "code": "LINEAGE_NODE_NOT_FOUND",
        "message", "target"}`` instead of raising.
    :raises ValueError: :class:`InvalidLineagePathInputError` or
        :class:`InvalidLineagePathQueryError`.
    """
    declared, edges = _validate_graph(graph)
    start = _validate_query(target, direction)

    if start not in declared:
        return {
            "success": False,
            "code": LINEAGE_NODE_NOT_FOUND,
            "message": f"target is not a declared lineage node: {target!r}",
            "target": _target_echo(start),
        }

    outgoing, incoming = _build_adjacency(edges)

    paths: List[Dict[str, Any]] = []
    if direction in ("upstream", "both"):
        for raw in _enumerate_paths(start, incoming):
            paths.append(_path_result(raw, "upstream"))
    if direction in ("downstream", "both"):
        for raw in _enumerate_paths(start, outgoing):
            paths.append(_path_result(raw, "downstream"))
    paths.sort(key=_path_sort_key)

    return {
        "success": True,
        "target": _target_echo(start),
        "direction": direction,
        "paths": paths,
    }
