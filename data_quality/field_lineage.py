"""Upstream/downstream field-level lineage tracing over a declared graph.

The public entry point is :func:`trace_field_lineage`. Fields are
identified by ``(table, column)`` pairs and edges point from ``source`` to
``target``: upstream traversal follows edges backwards, downstream
traversal follows them forwards.

Graph structure problems raise :class:`InvalidFieldLineageInputError`, bad
query arguments raise :class:`InvalidFieldLineageQueryError` and a target
that is not declared in ``fields`` raises
:class:`UnknownFieldLineageTargetError`; all three subclass
:class:`ValueError`.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

__all__ = [
    "trace_field_lineage",
    "InvalidFieldLineageInputError",
    "InvalidFieldLineageQueryError",
    "UnknownFieldLineageTargetError",
]

VALID_DIRECTIONS = ("upstream", "downstream", "both")
_EDGE_KEYS = frozenset({"source", "target"})
_FIELD_KEYS = frozenset({"table", "column"})

FieldKey = Tuple[str, str]


class InvalidFieldLineageInputError(ValueError):
    """The field lineage graph (``fields``/``edges``) is malformed."""


class InvalidFieldLineageQueryError(ValueError):
    """The query arguments (``target``/``direction``/``max_depth``) are bad."""


class UnknownFieldLineageTargetError(ValueError):
    """The queried ``target`` is not declared among ``fields``."""


def _endpoint_key(
    endpoint: Any, location: str, declared: Dict[str, Set[str]]
) -> FieldKey:
    """Validate an edge endpoint against the declared fields.

    Endpoints are graph structure, so every problem raises
    :class:`InvalidFieldLineageInputError`.
    """
    if not isinstance(endpoint, dict):
        raise InvalidFieldLineageInputError(
            f"{location} must be an object with table and column"
        )

    keys = set(endpoint)
    if keys != _FIELD_KEYS:
        missing = sorted(_FIELD_KEYS - keys)
        if missing:
            raise InvalidFieldLineageInputError(
                f"{location} is missing keys: {missing}"
            )
        unknown = sorted(keys - _FIELD_KEYS)
        raise InvalidFieldLineageInputError(
            f"{location} has unsupported keys: {unknown}"
        )

    table = endpoint["table"]
    column = endpoint["column"]
    if (
        not isinstance(table, str)
        or not isinstance(column, str)
        or table not in declared
        or column not in declared[table]
    ):
        raise InvalidFieldLineageInputError(
            f"{location} is not a declared field: {table!r}.{column!r}"
        )
    return table, column


def _validate_graph(
    fields: Any, edges: Any
) -> Dict[str, Set[str]]:
    if not isinstance(fields, dict):
        raise InvalidFieldLineageInputError(
            "fields must be an object mapping table ids to column id lists"
        )
    if not fields:
        raise InvalidFieldLineageInputError("fields must not be empty")

    declared: Dict[str, Set[str]] = {}
    for table, columns in fields.items():
        if not isinstance(table, str) or not table:
            raise InvalidFieldLineageInputError(
                f"fields has an invalid table id: {table!r}"
            )
        if not isinstance(columns, list):
            raise InvalidFieldLineageInputError(
                f"fields[{table!r}] must be a list of column ids"
            )
        if not columns:
            raise InvalidFieldLineageInputError(
                f"fields[{table!r}] must not be empty"
            )

        seen_columns: Set[str] = set()
        for index, column in enumerate(columns):
            if not isinstance(column, str) or not column:
                raise InvalidFieldLineageInputError(
                    f"fields[{table!r}][{index}] must be a non-empty string"
                )
            if column in seen_columns:
                raise InvalidFieldLineageInputError(
                    f"duplicate column id {column!r} in table {table!r}"
                )
            seen_columns.add(column)
        declared[table] = seen_columns

    if not isinstance(edges, list):
        raise InvalidFieldLineageInputError("edges must be a list")

    seen_edges: Set[Tuple[FieldKey, FieldKey]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise InvalidFieldLineageInputError(
                f"edges[{index}] must be an object"
            )

        keys = set(edge)
        if keys != _EDGE_KEYS:
            missing = sorted(_EDGE_KEYS - keys)
            if missing:
                raise InvalidFieldLineageInputError(
                    f"edges[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _EDGE_KEYS)
            raise InvalidFieldLineageInputError(
                f"edges[{index}] has unsupported keys: {unknown}"
            )

        source = _endpoint_key(
            edge["source"], f"edges[{index}].source", declared
        )
        target = _endpoint_key(
            edge["target"], f"edges[{index}].target", declared
        )

        pair = (source, target)
        if pair in seen_edges:
            raise InvalidFieldLineageInputError(
                f"duplicate edge: "
                f"{source[0]!r}.{source[1]!r} -> "
                f"{target[0]!r}.{target[1]!r}"
            )
        seen_edges.add(pair)

    return declared


def _validate_target(target: Any) -> FieldKey:
    """Validate the target reference shape as a query parameter."""
    if not isinstance(target, dict):
        raise InvalidFieldLineageQueryError(
            "target must be an object with table and column"
        )

    keys = set(target)
    if keys != _FIELD_KEYS:
        missing = sorted(_FIELD_KEYS - keys)
        if missing:
            raise InvalidFieldLineageQueryError(
                f"target is missing keys: {missing}"
            )
        unknown = sorted(keys - _FIELD_KEYS)
        raise InvalidFieldLineageQueryError(
            f"target has unsupported keys: {unknown}"
        )

    table = target["table"]
    column = target["column"]
    if not isinstance(table, str) or not isinstance(column, str):
        raise InvalidFieldLineageQueryError(
            "target.table and target.column must be strings"
        )
    return table, column


def _validate_query(
    target: Any, direction: Any, max_depth: Any
) -> FieldKey:
    table, column = _validate_target(target)
    if direction not in VALID_DIRECTIONS:
        raise InvalidFieldLineageQueryError(
            f"direction must be one of {list(VALID_DIRECTIONS)}, "
            f"got {direction!r}"
        )
    if max_depth is not None:
        # bool is a subclass of int, but JSON true/false are not depths.
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise InvalidFieldLineageQueryError(
                "max_depth must be null or an integer >= 0"
            )
        if max_depth < 0:
            raise InvalidFieldLineageQueryError(
                f"max_depth must be >= 0, got {max_depth}"
            )
    return table, column


def _build_adjacency(
    edges: List[Dict[str, Any]]
) -> Tuple[Dict[FieldKey, List[FieldKey]], Dict[FieldKey, List[FieldKey]]]:
    outgoing: Dict[FieldKey, List[FieldKey]] = {}
    incoming: Dict[FieldKey, List[FieldKey]] = {}
    for edge in edges:
        source = (edge["source"]["table"], edge["source"]["column"])
        target = (edge["target"]["table"], edge["target"]["column"])
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)
    return outgoing, incoming


def _walk(
    start: FieldKey,
    neighbors: Dict[FieldKey, List[FieldKey]],
    max_depth: Optional[int],
) -> Dict[FieldKey, int]:
    """BFS from ``start``; first visit wins, so depths are shortest."""
    depths = {start: 0}
    if max_depth == 0:
        return depths

    queue = deque([(start, 0)])
    while queue:
        field, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        next_depth = depth + 1
        for nxt in neighbors.get(field, ()):
            if nxt not in depths:
                depths[nxt] = next_depth
                queue.append((nxt, next_depth))
    return depths


def _edge_key(edge: Dict[str, Any]) -> Tuple[FieldKey, FieldKey]:
    source = edge["source"]
    target = edge["target"]
    return (
        (source["table"], source["column"]),
        (target["table"], target["column"]),
    )


def _side(
    depths: Optional[Dict[FieldKey, int]],
    edges: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if depths is None:
        return {"fields": [], "edges": []}

    fields = [
        {"table": table, "column": column, "depth": depth}
        for (table, column), depth in sorted(
            depths.items(), key=lambda item: (item[1], item[0])
        )
    ]
    side_edges = [
        edge
        for edge in edges
        if _edge_key(edge)[0] in depths and _edge_key(edge)[1] in depths
    ]
    side_edges.sort(key=lambda edge: _edge_key(edge))
    return {"fields": fields, "edges": side_edges}


def trace_field_lineage(
    fields: Any,
    edges: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Trace upstream/downstream field-level lineage of ``target``.

    :param fields: object mapping non-empty table ids to non-empty lists of
        distinct non-empty column ids.
    :param edges: list of ``{"source": {"table", "column"},
        "target": {"table", "column"}}`` objects whose endpoints are
        declared in ``fields``; each endpoint carries exactly ``table`` and
        ``column``; duplicate edges are rejected, self-loops and cycles are
        legal.
    :param target: ``{"table", "column"}`` field to trace from; depth 0 in
        every returned side.
    :param direction: ``"upstream"``, ``"downstream"`` or ``"both"``
        (default ``"both"``).
    :param max_depth: ``None`` for unlimited (default) or an integer ``>= 0``;
        ``0`` returns only ``target``. Booleans are not integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"fields", "edges"}, "downstream": {"fields", "edges"}}``.
        Each populated side lists fields as ``{"table", "column", "depth"}``
        sorted by ``(depth, table, column)`` and includes every original
        edge whose endpoints are in the side, sorted by
        ``(source.table, source.column, target.table, target.column)``. The
        side not queried is returned empty.
    :raises ValueError: :class:`InvalidFieldLineageInputError`,
        :class:`InvalidFieldLineageQueryError` or
        :class:`UnknownFieldLineageTargetError`.
    """
    declared = _validate_graph(fields, edges)
    table, column = _validate_query(target, direction, max_depth)

    if table not in declared or column not in declared[table]:
        raise UnknownFieldLineageTargetError(
            f"target is not declared in fields: {table!r}.{column!r}"
        )

    target_key: FieldKey = (table, column)
    outgoing, incoming = _build_adjacency(edges)

    upstream_depths: Optional[Dict[FieldKey, int]] = None
    downstream_depths: Optional[Dict[FieldKey, int]] = None
    if direction in ("upstream", "both"):
        upstream_depths = _walk(target_key, incoming, max_depth)
    if direction in ("downstream", "both"):
        downstream_depths = _walk(target_key, outgoing, max_depth)

    return {
        "target": {"table": table, "column": column},
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _side(upstream_depths, edges),
        "downstream": _side(downstream_depths, edges),
    }
