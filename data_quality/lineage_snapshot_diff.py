"""Comparison of two field lineage snapshots.

The public entry point is :func:`compare_lineage_snapshots`. It consumes
two already captured field lineage snapshots (``baseline`` and
``current``, each carrying ``fields`` and ``edges`` in the same shape as
:func:`data_quality.trace_field_lineage`) plus a non-empty list of
``targets``; the snapshots are interpreted as-is without modifying them,
writing anything to disk or contacting any service.

Global changes classify every declared field and edge into four
categories, the four ``changes`` keys:

* ``field_added`` / ``field_removed`` list ``{"field": {"table",
  "column"}}`` entries;
* ``edge_added`` / ``edge_removed`` list ``{"edge": {"source",
  "target"}}`` entries.

For every target the function compares the fields reachable from it in
either direction and the edges induced by those reachable fields between
the two snapshots:

* a target declared on only one side is ``added`` (``current`` only) or
  ``removed`` (``baseline`` only);
* a target declared on both sides is ``changed`` whenever either
  direction differs in its reachable fields or induced edges, otherwise
  ``unchanged``.

Validation happens in a fixed order: JSON at the CLI boundary, top level
and the two snapshots, then the targets shape, then per-target
existence. Malformed snapshots raise :class:`InvalidLineageSnapshotError`,
a bad targets list raises :class:`InvalidLineageDiffQueryError` and a
target declared on neither side raises
:class:`UnknownLineageDiffTargetError`; all three subclass
:class:`ValueError`.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set, Tuple

from .field_lineage import (
    InvalidFieldLineageInputError,
    _build_adjacency,
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

# A field is identified by its (table, column) pair.
FieldId = Tuple[str, str]
# An edge is identified by ((source table, source column),
# (target table, target column)).
EdgeId = Tuple[FieldId, FieldId]


class InvalidLineageSnapshotError(ValueError):
    """A lineage snapshot (``fields``/``edges``) is missing or malformed."""

    code = "INVALID_LINEAGE_SNAPSHOT"


class InvalidLineageDiffQueryError(ValueError):
    """The ``targets`` list is missing, malformed, empty or duplicated."""

    code = "INVALID_LINEAGE_DIFF_QUERY"


class UnknownLineageDiffTargetError(ValueError):
    """A queried target is declared on neither the baseline nor current."""

    code = "UNKNOWN_LINEAGE_DIFF_TARGET"


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


def _validate_snapshot(
    snapshot: Any, where: str
) -> Tuple[Set[FieldId], Set[EdgeId]]:
    _check_keys(snapshot, _SNAPSHOT_KEYS, where, InvalidLineageSnapshotError)
    try:
        declared = _validate_fields(snapshot["fields"])
        _validate_edges(snapshot["edges"], declared)
    except InvalidFieldLineageInputError as exc:
        raise InvalidLineageSnapshotError(str(exc)) from exc

    edge_ids: Set[EdgeId] = set()
    for edge in snapshot["edges"]:
        edge_ids.add((_field_id(edge["source"]), _field_id(edge["target"])))
    return declared, edge_ids


def _validate_targets(targets: Any) -> List[Dict[str, str]]:
    if not isinstance(targets, list):
        raise InvalidLineageDiffQueryError("targets must be a list")
    if not targets:
        raise InvalidLineageDiffQueryError("targets must not be empty")

    target_ids: List[FieldId] = []
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
        field_id = (table, column)
        if field_id in seen:
            raise InvalidLineageDiffQueryError(
                f"duplicate target: {target!r}"
            )
        seen.add(field_id)
        target_ids.append(field_id)

    return [{"table": table, "column": column} for table, column in target_ids]


def _reachable(
    start: FieldId, adjacency: Dict[FieldId, List[FieldId]]
) -> Set[FieldId]:
    """All fields reachable from ``start`` through one or more edges."""
    reachable: Set[FieldId] = set()
    queue = deque([start])
    while queue:
        field = queue.popleft()
        for nxt in adjacency.get(field, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)
    return reachable


def _side_closure(
    start: FieldId,
    adjacency: Dict[FieldId, List[FieldId]],
    edge_ids: Set[EdgeId],
) -> Tuple[Set[FieldId], Set[EdgeId]]:
    """Side fields (the target plus reachable fields) and induced edges.

    Mirrors the side shape of :func:`data_quality.trace_field_lineage`:
    the target belongs to the side at depth 0 and every original edge
    whose endpoints both lie in the side is induced, including edges
    incident to the target.
    """
    side_fields = {start}
    side_fields.update(_reachable(start, adjacency))
    induced = {
        edge
        for edge in edge_ids
        if edge[0] in side_fields and edge[1] in side_fields
    }
    return side_fields, induced


_EMPTY_DELTA_KEYS = (
    "added_fields",
    "removed_fields",
    "added_edges",
    "removed_edges",
)


def _empty_delta() -> Dict[str, List[Any]]:
    return {key: [] for key in _EMPTY_DELTA_KEYS}


def _field_ref(field_id: FieldId) -> Dict[str, str]:
    return {"table": field_id[0], "column": field_id[1]}


def _edge_ref(edge_id: EdgeId) -> Dict[str, Dict[str, str]]:
    return {"source": _field_ref(edge_id[0]), "target": _field_ref(edge_id[1])}


def _field_delta(
    added: Set[FieldId], removed: Set[FieldId]
) -> Dict[str, List[Dict[str, str]]]:
    return {
        "added_fields": [_field_ref(fid) for fid in sorted(added)],
        "removed_fields": [_field_ref(fid) for fid in sorted(removed)],
    }


def _edge_delta(
    added: Set[EdgeId], removed: Set[EdgeId]
) -> Dict[str, List[Dict[str, Dict[str, str]]]]:
    return {
        "added_edges": [_edge_ref(eid) for eid in sorted(added)],
        "removed_edges": [_edge_ref(eid) for eid in sorted(removed)],
    }


def _delta(
    added_fields: Set[FieldId],
    removed_fields: Set[FieldId],
    added_edges: Set[EdgeId],
    removed_edges: Set[EdgeId],
) -> Dict[str, List[Any]]:
    delta = _empty_delta()
    delta.update(_field_delta(added_fields, removed_fields))
    delta.update(_edge_delta(added_edges, removed_edges))
    return delta


def _delta_nonempty(delta: Dict[str, List[Any]]) -> bool:
    return any(delta[key] for key in _EMPTY_DELTA_KEYS)


def compare_lineage_snapshots(payload: Any) -> Dict[str, Any]:
    """Compare two field lineage snapshots for the queried targets.

    :param payload: object with exactly the keys ``baseline``,
        ``current`` and ``targets``. ``baseline`` and ``current`` are
        objects with exactly the keys ``fields`` and ``edges`` following
        the :func:`~data_quality.trace_field_lineage` contract (object
        mapping non-empty table ids to distinct non-empty column ids;
        edges are ``{"source", "target"}`` objects whose
        ``{"table", "column"}`` endpoints are declared; duplicate edges
        are rejected). ``targets`` is a non-empty list of distinct
        ``{"table", "column"}`` objects with non-empty string values.
    :returns: ``{"status": "ok", "summary": {...}, "changes": {...},
        "targets": [...]}``. The summary carries, in order,
        ``added_field_count``, ``removed_field_count``,
        ``added_edge_count``, ``removed_edge_count``,
        ``changed_target_count`` and ``unchanged_target_count``.
        ``changes`` carries, in that same paired order, the four keys
        ``field_added``, ``field_removed``, ``edge_added`` and
        ``edge_removed``; the field lists hold
        ``{"field": {"table", "column"}}`` entries and the edge lists
        hold ``{"edge": {"source", "target"}}`` entries, sorted by
        ``(table, column)`` and
        ``(source.table, source.column, target.table, target.column)``
        respectively.
        Target results keep the query order; each has ``field``,
        ``status``, ``upstream_delta`` and ``downstream_delta``, where
        every delta carries the four sorted sets ``added_fields``,
        ``removed_fields``, ``added_edges`` and ``removed_edges``.
    :raises ValueError: :class:`InvalidLineageSnapshotError` on a
        malformed payload or snapshot,
        :class:`InvalidLineageDiffQueryError` on a bad targets list and
        :class:`UnknownLineageDiffTargetError` when a target is declared
        on neither side.
    """
    _check_keys(
        payload, _PAYLOAD_KEYS, "payload", InvalidLineageSnapshotError
    )
    baseline_fields, baseline_edges = _validate_snapshot(
        payload["baseline"], "baseline"
    )
    current_fields, current_edges = _validate_snapshot(
        payload["current"], "current"
    )
    target_refs = _validate_targets(payload["targets"])

    added_fields = current_fields - baseline_fields
    removed_fields = baseline_fields - current_fields
    added_edges = current_edges - baseline_edges
    removed_edges = baseline_edges - current_edges

    changes: Dict[str, List[Dict[str, Any]]] = {
        "field_added": [
            {"field": _field_ref(fid)} for fid in sorted(added_fields)
        ],
        "field_removed": [
            {"field": _field_ref(fid)} for fid in sorted(removed_fields)
        ],
        "edge_added": [
            {"edge": _edge_ref(eid)} for eid in sorted(added_edges)
        ],
        "edge_removed": [
            {"edge": _edge_ref(eid)} for eid in sorted(removed_edges)
        ],
    }

    baseline_out, baseline_in = _build_adjacency(
        payload["baseline"]["edges"]
    )
    current_out, current_in = _build_adjacency(
        payload["current"]["edges"]
    )

    target_results: List[Dict[str, Any]] = []
    changed_target_count = 0
    unchanged_target_count = 0
    for ref in target_refs:
        field_id = _field_id(ref)
        in_baseline = field_id in baseline_fields
        in_current = field_id in current_fields

        if not in_baseline and not in_current:
            raise UnknownLineageDiffTargetError(
                f"target is declared on neither snapshot: {ref!r}"
            )

        if in_baseline != in_current:
            status = "added" if in_current else "removed"
            upstream_delta = _empty_delta()
            downstream_delta = _empty_delta()
        else:
            base_up_fields, base_up_edges = _side_closure(
                field_id, baseline_in, baseline_edges
            )
            cur_up_fields, cur_up_edges = _side_closure(
                field_id, current_in, current_edges
            )
            base_down_fields, base_down_edges = _side_closure(
                field_id, baseline_out, baseline_edges
            )
            cur_down_fields, cur_down_edges = _side_closure(
                field_id, current_out, current_edges
            )

            upstream_delta = _delta(
                cur_up_fields - base_up_fields,
                base_up_fields - cur_up_fields,
                cur_up_edges - base_up_edges,
                base_up_edges - cur_up_edges,
            )
            downstream_delta = _delta(
                cur_down_fields - base_down_fields,
                base_down_fields - cur_down_fields,
                cur_down_edges - base_down_edges,
                base_down_edges - cur_down_edges,
            )
            status = (
                "changed"
                if _delta_nonempty(upstream_delta)
                or _delta_nonempty(downstream_delta)
                else "unchanged"
            )

        if status == "changed":
            changed_target_count += 1
        elif status == "unchanged":
            unchanged_target_count += 1

        target_results.append(
            {
                "field": ref,
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
            "changed_target_count": changed_target_count,
            "unchanged_target_count": unchanged_target_count,
        },
        "changes": changes,
        "targets": target_results,
    }
