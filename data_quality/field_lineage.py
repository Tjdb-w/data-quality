"""Upstream/downstream lineage tracing over declared table/column fields.

The public entry point is :func:`trace_field_lineage`. ``fields`` declares
the known columns per table; edges point from a ``source`` field to a
``target`` field: upstream traversal follows edges backwards, downstream
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

# A field is identified by its (table, column) pair.
FieldId = Tuple[str, str]


class InvalidFieldLineageInputError(ValueError):
    """The field lineage graph (``fields``/``edges``) is missing or malformed."""


class InvalidFieldLineageQueryError(ValueError):
    """The query arguments (``target``/``direction``/``max_depth``) are bad."""


class UnknownFieldLineageTargetError(ValueError):
    """The queried ``target`` is not declared among ``fields``."""


def _field_id(endpoint: Dict[str, Any]) -> FieldId:
    return (endpoint["table"], endpoint["column"])


def _validate_fields(fields: Any) -> Set[FieldId]:
    if not isinstance(fields, dict):
        raise InvalidFieldLineageInputError(
            "fields must be an object mapping table ids to field id lists"
        )

    declared: Set[FieldId] = set()
    for table, columns in fields.items():
        if not isinstance(table, str) or not table:
            raise InvalidFieldLineageInputError(
                f"fields key must be a non-empty table id string: {table!r}"
            )
        if not isinstance(columns, list):
            raise InvalidFieldLineageInputError(
                f"fields[{table!r}] must be a list of field ids"
            )
        seen_columns: Set[str] = set()
        for index, column in enumerate(columns):
            if not isinstance(column, str) or not column:
                raise InvalidFieldLineageInputError(
                    f"fields[{table!r}][{index}] must be a non-empty "
                    "field id string"
                )
            if column in seen_columns:
                raise InvalidFieldLineageInputError(
                    f"duplicate field id in table {table!r}: {column!r}"
                )
            seen_columns.add(column)
            declared.add((table, column))
    return declared


def _check_endpoint(endpoint: Any, declared: Set[FieldId], where: str) -> None:
    if not isinstance(endpoint, dict):
        raise InvalidFieldLineageInputError(f"{where} must be an object")
    keys = set(endpoint)
    if keys != _FIELD_KEYS:
        missing = sorted(_FIELD_KEYS - keys)
        if missing:
            raise InvalidFieldLineageInputError(
                f"{where} is missing keys: {missing}"
            )
        unknown = sorted(keys - _FIELD_KEYS)
        raise InvalidFieldLineageInputError(
            f"{where} has unsupported keys: {unknown}"
        )
    table = endpoint["table"]
    column = endpoint["column"]
    if not isinstance(table, str) or not table:
        raise InvalidFieldLineageInputError(
            f"{where}.table must be a non-empty string"
        )
    if not isinstance(column, str) or not column:
        raise InvalidFieldLineageInputError(
            f"{where}.column must be a non-empty string"
        )
    if (table, column) not in declared:
        raise InvalidFieldLineageInputError(
            f"{where} is not a declared field: {endpoint!r}"
        )


def _validate_edges(edges: Any, declared: Set[FieldId]) -> None:
    if not isinstance(edges, list):
        raise InvalidFieldLineageInputError("edges must be a list")

    seen_edges: Set[Tuple[FieldId, FieldId]] = set()
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

        _check_endpoint(edge["source"], declared, f"edges[{index}].source")
        _check_endpoint(edge["target"], declared, f"edges[{index}].target")

        pair = (_field_id(edge["source"]), _field_id(edge["target"]))
        if pair in seen_edges:
            raise InvalidFieldLineageInputError(
                f"duplicate edge: {edge['source']!r} -> {edge['target']!r}"
            )
        seen_edges.add(pair)


def _validate_query(target: Any, direction: Any, max_depth: Any) -> None:
    if not isinstance(target, dict):
        raise InvalidFieldLineageQueryError(
            "target must be an object with 'table' and 'column'"
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
    if not isinstance(target["table"], str) or not target["table"]:
        raise InvalidFieldLineageQueryError(
            "target.table must be a non-empty string"
        )
    if not isinstance(target["column"], str) or not target["column"]:
        raise InvalidFieldLineageQueryError(
            "target.column must be a non-empty string"
        )
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


def _build_adjacency(
    edges: List[Dict[str, Any]]
) -> Tuple[Dict[FieldId, List[FieldId]], Dict[FieldId, List[FieldId]]]:
    outgoing: Dict[FieldId, List[FieldId]] = {}
    incoming: Dict[FieldId, List[FieldId]] = {}
    for edge in edges:
        source = _field_id(edge["source"])
        target = _field_id(edge["target"])
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)
    return outgoing, incoming


def _walk(
    start: FieldId,
    neighbors: Dict[FieldId, List[FieldId]],
    max_depth: Optional[int],
) -> Dict[FieldId, int]:
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


def _side(
    depths: Optional[Dict[FieldId, int]],
    edges: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if depths is None:
        return {"fields": [], "edges": []}

    fields = [
        {"table": table, "column": column, "depth": depth}
        for (table, column), depth in sorted(
            depths.items(), key=lambda item: (item[1], item[0][0], item[0][1])
        )
    ]
    side_edges = [
        edge
        for edge in edges
        if _field_id(edge["source"]) in depths
        and _field_id(edge["target"]) in depths
    ]
    side_edges.sort(
        key=lambda edge: (
            edge["source"]["table"],
            edge["source"]["column"],
            edge["target"]["table"],
            edge["target"]["column"],
        )
    )
    return {"fields": fields, "edges": side_edges}


def trace_field_lineage(
    fields: Any,
    edges: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Trace upstream/downstream field-level lineage of ``target``.

    :param fields: object mapping non-empty table id strings to lists of
        distinct non-empty field id strings.
    :param edges: list of ``{"source": ..., "target": ...}`` objects whose
        endpoints are ``{"table": ..., "column": ...}`` objects declared in
        ``fields``; duplicate edges are rejected, self-loops and cycles are
        legal.
    :param target: ``{"table": ..., "column": ...}`` field to trace from;
        depth 0 in every returned side.
    :param direction: ``"upstream"``, ``"downstream"`` or ``"both"``
        (default ``"both"``).
    :param max_depth: ``None`` for unlimited (default) or an integer ``>= 0``;
        ``0`` returns only ``target``. Booleans are not integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"fields", "edges"}, "downstream": {"fields", "edges"}}``.
        Each populated side lists fields as ``{"table", "column", "depth"}``
        sorted by ``(depth, table, column)`` and includes every original edge
        whose endpoints are in the side, sorted by ``(source.table,
        source.column, target.table, target.column)``. The side not queried
        is returned empty.
    :raises ValueError: :class:`InvalidFieldLineageInputError`,
        :class:`InvalidFieldLineageQueryError` or
        :class:`UnknownFieldLineageTargetError`.
    """
    declared = _validate_fields(fields)
    _validate_edges(edges, declared)
    _validate_query(target, direction, max_depth)

    start = _field_id(target)
    if start not in declared:
        raise UnknownFieldLineageTargetError(
            f"target is not declared in fields: {target!r}"
        )

    outgoing, incoming = _build_adjacency(edges)

    upstream_depths: Optional[Dict[FieldId, int]] = None
    downstream_depths: Optional[Dict[FieldId, int]] = None
    if direction in ("upstream", "both"):
        upstream_depths = _walk(start, incoming, max_depth)
    if direction in ("downstream", "both"):
        downstream_depths = _walk(start, outgoing, max_depth)

    return {
        "target": target,
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _side(upstream_depths, edges),
        "downstream": _side(downstream_depths, edges),
    }
