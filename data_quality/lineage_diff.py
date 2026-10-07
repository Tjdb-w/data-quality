"""Field lineage snapshot comparison.

The public entry point is :func:`compare_lineage_snapshots`. It consumes
two field lineage snapshots (``baseline`` and ``current``, each carrying
``fields`` and ``edges`` in the same shape as
:func:`data_quality.field_lineage.trace_field_lineage`) plus a non-empty
``targets`` list of field objects. Only the in-memory graphs are
interpreted: no record is modified, nothing is written to disk and no
service is contacted.

Each snapshot is validated independently with the field lineage
contract: ``fields`` maps non-empty table ids to distinct non-empty
column ids and ``edges`` are ``{"source", "target"}`` objects whose
``{"table", "column"}`` endpoints are declared; duplicate edges are
rejected while self-loops and cycles are legal.

For every target, the fields reachable in each direction and the edges
induced by those reachable fields are compared between the two sides:

* a target present on only one side is reported ``"added"`` (current
  only) or ``"removed"`` (baseline only);
* a target present on both sides is ``"changed"`` when either the
  reachable field set or the induced edge set differs in either
  direction, otherwise ``"unchanged"``.

Malformed snapshots raise :class:`InvalidLineageSnapshotError`, a
malformed ``targets`` list raises :class:`InvalidLineageDiffQueryError`
and a target missing from both snapshots raises
:class:`UnknownLineageDiffTargetError`; all three subclass
:class:`ValueError`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from .field_lineage import (
    InvalidFieldLineageInputError,
    FieldId,
    _field_id,
    _validate_edges,
    _validate_fields,
)

__all__ = [
    "compare_lineage_snapshots",
    "InvalidLineageSnapshotError",
    "InvalidLineageDiffQueryError",
    "UnknownLineageDiffTargetError",
]

_PAYLOAD_KEYS = frozenset({"baseline", "current", "targets"})
_SNAPSHOT_KEYS = frozenset({"fields", "edges"})
_FIELD_KEYS = frozenset({"table", "column"})

# An edge is identified by its (source, target) field pair.
EdgeId = Tuple[FieldId, FieldId]


class InvalidLineageSnapshotError(ValueError):
    """A lineage snapshot (``fields``/``edges``) is missing or malformed."""


class InvalidLineageDiffQueryError(ValueError):
    """The ``targets`` argument is missing or malformed."""


class UnknownLineageDiffTargetError(ValueError):
    """A queried target is declared in neither snapshot."""


def _snapshot_error(message: str) -> InvalidLineageSnapshotError:
    return InvalidLineageSnapshotError(message)


def _check_keys(
    obj: Any, expected: frozenset, where: str, error: type
) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise error(f"{where} must be an object")
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise error(f"{where} has unsupported keys: {unknown}")
    return obj


def _validate_snapshot(snapshot: Any, where: str) -> Set[FieldId]:
    _check_keys(
        snapshot, _SNAPSHOT_KEYS, where, InvalidLineageSnapshotError
    )
    try:
        declared = _validate_fields(snapshot["fields"])
        _validate_edges(snapshot["edges"], declared)
    except InvalidFieldLineageInputError as exc:
        raise _snapshot_error(str(exc)) from exc
    return declared


def _validate_targets(targets: Any) -> List[FieldId]:
    if not isinstance(targets, list):
        raise InvalidLineageDiffQueryError("targets must be a list")
    if not targets:
        raise InvalidLineageDiffQueryError("targets must be non-empty")

    ordered: List[FieldId] = []
    seen: Set[FieldId] = set()
    for index, target in enumerate(targets):
        where = f"targets[{index}]"
        if not isinstance(target, dict):
            raise InvalidLineageDiffQueryError(f"{where} must be an object")
        keys = set(target)
        if keys != _FIELD_KEYS:
            missing = sorted(_FIELD_KEYS - keys)
            if missing:
                raise InvalidLineageDiffQueryError(
                    f"{where} is missing keys: {missing}"
                )
            unknown = sorted(keys - _FIELD_KEYS)
            raise InvalidLineageDiffQueryError(
                f"{where} has unsupported keys: {unknown}"
            )
        table = target["table"]
        column = target["column"]
        if not isinstance(table, str) or not table:
            raise InvalidLineageDiffQueryError(
                f"{where}.table must be a non-empty string"
            )
        if not isinstance(column, str) or not column:
            raise InvalidLineageDiffQueryError(
                f"{where}.column must be a non-empty string"
            )
        field = (table, column)
        if field in seen:
            raise InvalidLineageDiffQueryError(
                f"duplicate target: {target!r}"
            )
        seen.add(field)
        ordered.append(field)
    return ordered


def _reachable(start: FieldId, adjacency: Dict[FieldId, Set[FieldId]]) -> Set[FieldId]:
    """All fields reachable from ``start`` along ``adjacency`` (start included)."""
    seen = {start}
    stack = [start]
    while stack:
        field = stack.pop()
        for nxt in adjacency.get(field, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _field_refs(fields: Set[FieldId]) -> List[Dict[str, str]]:
    return [
        {"table": table, "column": column}
        for table, column in sorted(fields)
    ]


def _edge_refs(edges: Set[EdgeId]) -> List[Dict[str, Dict[str, str]]]:
    return [
        {
            "source": {"table": source[0], "column": source[1]},
            "target": {"table": target[0], "column": target[1]},
        }
        for source, target in sorted(edges, key=lambda edge: (edge[0], edge[1]))
    ]


def _delta(fields_b: Set[FieldId], fields_c: Set[FieldId],
           edges_b: Set[EdgeId], edges_c: Set[EdgeId]) -> Dict[str, Any]:
    return {
        "added_fields": _field_refs(fields_c - fields_b),
        "removed_fields": _field_refs(fields_b - fields_c),
        "added_edges": _edge_refs(edges_c - edges_b),
        "removed_edges": _edge_refs(edges_b - edges_c),
    }


def _side_state(
    baseline: Tuple[Set[FieldId], Set[EdgeId]],
    current: Tuple[Set[FieldId], Set[EdgeId]],
) -> str:
    return "changed" if baseline != current else "unchanged"


def compare_lineage_snapshots(payload: Any) -> Dict[str, Any]:
    """Compare two field lineage snapshots around queried target fields.

    :param payload: object with exactly the keys ``baseline``,
        ``current`` and ``targets``. ``baseline`` and ``current`` are
        objects with exactly the keys ``fields`` and ``edges``, each
        following the :func:`~data_quality.field_lineage.trace_field_lineage`
        contract (validated independently). ``targets`` is a non-empty
        list of distinct ``{"table", "column"}`` objects with non-empty
        string values.
    :returns: ``{"status": "ok", "summary": {...}, "changes": [...],
        "targets": [...]}``. The summary carries six paired counts:
        ``added_field_count``/``removed_field_count``,
        ``added_edge_count``/``removed_edge_count`` (snapshot-level
        changes) and ``changed_target_count``/``unchanged_target_count``.
        ``changes`` lists the snapshot-level field and edge differences
        as ``{"type": "field_added"|"field_removed", "field": ...}`` and
        ``{"type": "edge_added"|"edge_removed", "edge": ...}`` entries,
        fields sorted by ``(table, column)`` and edges by
        ``(source, target)``. ``targets`` preserves the query order; each
        entry carries ``field``, ``status`` (``"added"``, ``"removed"``,
        ``"changed"`` or ``"unchanged"``), ``upstream_delta`` and
        ``downstream_delta``, each delta holding the four sorted sets
        ``added_fields``, ``removed_fields``, ``added_edges`` and
        ``removed_edges``. A target on one side only gets that side's
        reachable fields and induced edges as the delta; a target on both
        sides compares the reachable fields and induced edges of each
        direction.
    :raises ValueError: :class:`InvalidLineageSnapshotError` on malformed
        top-level payload or snapshots,
        :class:`InvalidLineageDiffQueryError` on a malformed ``targets``
        list, or :class:`UnknownLineageDiffTargetError` when a target is
        declared in neither snapshot.
    """
    _check_keys(payload, _PAYLOAD_KEYS, "payload", InvalidLineageSnapshotError)
    _validate_snapshot(payload["baseline"], "baseline")
    _validate_snapshot(payload["current"], "current")
    target_ids = _validate_targets(payload["targets"])

    baseline_graph = _Graph(payload["baseline"])
    current_graph = _Graph(payload["current"])

    added_fields = current_graph.fields - baseline_graph.fields
    removed_fields = baseline_graph.fields - current_graph.fields
    added_edges = current_graph.edges - baseline_graph.edges
    removed_edges = baseline_graph.edges - current_graph.edges

    changes: List[Dict[str, Any]] = []
    for field in sorted(added_fields):
        changes.append(
            {"type": "field_added", "field": {"table": field[0], "column": field[1]}}
        )
    for edge in sorted(added_edges, key=lambda edge: (edge[0], edge[1])):
        changes.append({"type": "edge_added", "edge": _edge_object(edge)})
    for field in sorted(removed_fields):
        changes.append(
            {"type": "field_removed", "field": {"table": field[0], "column": field[1]}}
        )
    for edge in sorted(removed_edges, key=lambda edge: (edge[0], edge[1])):
        changes.append({"type": "edge_removed", "edge": _edge_object(edge)})

    target_reports: List[Dict[str, Any]] = []
    changed_count = 0
    unchanged_count = 0
    for field in target_ids:
        in_baseline = field in baseline_graph.fields
        in_current = field in current_graph.fields
        field_ref = {"table": field[0], "column": field[1]}

        if not in_baseline and not in_current:
            raise UnknownLineageDiffTargetError(
                f"target is declared in neither snapshot: {field_ref!r}"
            )

        if in_baseline and not in_current:
            up_b, up_edges_b = baseline_graph.reach(field, reverse=True)
            down_b, down_edges_b = baseline_graph.reach(field, reverse=False)
            status = "removed"
            upstream_delta = _delta(up_b, set(), up_edges_b, set())
            downstream_delta = _delta(down_b, set(), down_edges_b, set())
            changed_count += 1
        elif in_current and not in_baseline:
            up_c, up_edges_c = current_graph.reach(field, reverse=True)
            down_c, down_edges_c = current_graph.reach(field, reverse=False)
            status = "added"
            upstream_delta = _delta(set(), up_c, set(), up_edges_c)
            downstream_delta = _delta(set(), down_c, set(), down_edges_c)
            changed_count += 1
        else:
            up_b, up_edges_b = baseline_graph.reach(field, reverse=True)
            up_c, up_edges_c = current_graph.reach(field, reverse=True)
            down_b, down_edges_b = baseline_graph.reach(field, reverse=False)
            down_c, down_edges_c = current_graph.reach(field, reverse=False)
            up_status = _side_state((up_b, up_edges_b), (up_c, up_edges_c))
            down_status = _side_state(
                (down_b, down_edges_b), (down_c, down_edges_c)
            )
            status = (
                "changed"
                if up_status == "changed" or down_status == "changed"
                else "unchanged"
            )
            upstream_delta = _delta(up_b, up_c, up_edges_b, up_edges_c)
            downstream_delta = _delta(
                down_b, down_c, down_edges_b, down_edges_c
            )
            if status == "changed":
                changed_count += 1
            else:
                unchanged_count += 1

        target_reports.append(
            {
                "field": field_ref,
                "status": status,
                "upstream_delta": upstream_delta,
                "downstream_delta": downstream_delta,
            }
        )

    return {
        "status": "ok",
        "summary": {
            "added_field_count": len(added_fields),
            "removed_field_count": len(removed_fields),
            "added_edge_count": len(added_edges),
            "removed_edge_count": len(removed_edges),
            "changed_target_count": changed_count,
            "unchanged_target_count": unchanged_count,
        },
        "changes": changes,
        "targets": target_reports,
    }


def _edge_object(edge: EdgeId) -> Dict[str, Dict[str, str]]:
    source, target = edge
    return {
        "source": {"table": source[0], "column": source[1]},
        "target": {"table": target[0], "column": target[1]},
    }


class _Graph:
    """A validated field lineage snapshot indexed for reachability."""

    def __init__(self, snapshot: Dict[str, Any]) -> None:
        self.fields: Set[FieldId] = {
            (table, column)
            for table, columns in snapshot["fields"].items()
            for column in columns
        }
        self.edges: Set[EdgeId] = {
            (_field_id(edge["source"]), _field_id(edge["target"]))
            for edge in snapshot["edges"]
        }
        self._outgoing: Dict[FieldId, Set[FieldId]] = {}
        self._incoming: Dict[FieldId, Set[FieldId]] = {}
        for source, target in self.edges:
            self._outgoing.setdefault(source, set()).add(target)
            self._incoming.setdefault(target, set()).add(source)

    def reach(
        self, start: FieldId, reverse: bool
    ) -> Tuple[Set[FieldId], Set[EdgeId]]:
        """Reachable fields (incl. ``start``) and the edges they induce."""
        adjacency = self._incoming if reverse else self._outgoing
        fields = _reachable(start, adjacency)
        edges = {
            edge
            for edge in self.edges
            if edge[0] in fields and edge[1] in fields
        }
        return fields, edges
