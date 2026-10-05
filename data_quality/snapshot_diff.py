"""Anomaly drift comparison between two validation-result snapshots.

The public entry point is :func:`compare_quality_snapshots`. It consumes
a payload of two already computed snapshots ``baseline`` and ``current``
plus the field ``lineage`` graph shared by both, using exactly the same
check-result and lineage shapes as
:func:`data_quality.correlation.correlate_violations` -- no rules are
re-run, nothing is written and no external service is contacted.

Only results with ``violated`` true participate. Each anomaly is
identified by the ``rule_id``/``dataset_id``/``field_id``/``sample_id``
quadruple:

* ``new`` anomalies are present in ``current`` only;
* ``resolved`` anomalies are present in ``baseline`` only;
* ``persisted`` anomalies are present in both (both original values are
  kept).

Every change additionally carries ``upstream_changes`` and ``paths``, two
same-order lists. ``upstream_changes`` names the other
new/resolved/persisted anomalies of the *same* ``sample_id`` whose fields
reach this change's field along upstream lineage edges and whose values
differ between the two snapshots; a change itself and self-loops are
never included. Each ``paths`` item is the shortest field sequence from
the evidence field upstream to this change's field; ties are broken by
the lexicographic order of the complete field sequence.

Malformed snapshots or lineage graphs raise
:class:`InvalidSnapshotInputError` (a :class:`ValueError`); check results
referencing datasets or fields not declared in the lineage graph raise
:class:`UnknownSnapshotReferenceError` (a :class:`LookupError`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from .correlation import (
    InvalidCorrelationInputError,
    _json_equal,
    _validate_lineage,
    _validate_results,
)
from .origin_analysis import _shortest_forward_path

__all__ = [
    "compare_quality_snapshots",
    "InvalidSnapshotInputError",
    "UnknownSnapshotReferenceError",
]

_PAYLOAD_KEYS = frozenset({"baseline", "current", "lineage"})
_SNAPSHOT_KEYS = frozenset({"results"})

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]

# An anomaly is identified by (rule, dataset, field, sample).
AnomalyId = Tuple[str, str, str, str]


class InvalidSnapshotInputError(ValueError):
    """The snapshots or the lineage graph are malformed."""

    code = "INVALID_SNAPSHOT_INPUT"


class UnknownSnapshotReferenceError(LookupError):
    """A check result references an undeclared dataset or field."""

    code = "UNKNOWN_SNAPSHOT_REFERENCE"


def _input_error(message: str) -> InvalidSnapshotInputError:
    return InvalidSnapshotInputError(message)


def _validate_snapshot(snapshot: Any, where: str) -> List[Dict[str, Any]]:
    """Validate one snapshot wrapper and its results.

    Each wrapper must be an object containing exactly the ``results`` key,
    whose list follows the correlate result contract.
    """
    if not isinstance(snapshot, dict):
        raise _input_error(f"{where} must be an object")
    keys = set(snapshot)
    if keys != _SNAPSHOT_KEYS:
        missing = sorted(_SNAPSHOT_KEYS - keys)
        if missing:
            raise _input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - _SNAPSHOT_KEYS)
        raise _input_error(f"{where} has unsupported keys: {unknown}")
    try:
        return _validate_results(snapshot["results"])
    except InvalidCorrelationInputError as exc:
        raise _input_error(str(exc)) from exc


def _check_references(
    records: List[Dict[str, Any]],
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
) -> None:
    for record in records:
        dataset = record["dataset_id"]
        if dataset not in declared_datasets:
            raise UnknownSnapshotReferenceError(
                f"unknown dataset id: {dataset!r}"
            )
        if (dataset, record["field_id"]) not in declared_fields:
            raise UnknownSnapshotReferenceError(
                f"unknown field: {dataset!r}.{record['field_id']}"
            )


def _violated_index(
    records: List[Dict[str, Any]],
) -> Dict[AnomalyId, Dict[str, Any]]:
    """Index violated results by their four-part identity.

    :func:`_validate_results` already rejects conflicting repetitions of
    the same ``(rule_id, sample_id)``, so a four-part identity can never
    map to two different records.
    """
    index: Dict[AnomalyId, Dict[str, Any]] = {}
    for record in records:
        if not record["violated"]:
            continue
        identity: AnomalyId = (
            record["rule_id"],
            record["dataset_id"],
            record["field_id"],
            record["sample_id"],
        )
        index[identity] = record
    return index


def _field_of(identity: AnomalyId) -> FieldId:
    return (identity[1], identity[2])


def _field_ref(field: FieldId) -> Dict[str, str]:
    return {"dataset_id": field[0], "field_id": field[1]}


def _upstream_evidence(
    identity: AnomalyId,
    peers: List[AnomalyId],
    baseline_index: Dict[AnomalyId, Dict[str, Any]],
    current_index: Dict[AnomalyId, Dict[str, Any]],
    downstream_of: Dict[FieldId, Set[FieldId]],
) -> Tuple[List[Dict[str, str]], List[List[Dict[str, str]]]]:
    """Collect a change's upstream anomalies and their parallel paths.

    A peer qualifies when it is a different anomaly of the same sample,
    its field reaches this change's field along forward (upstream ->
    downstream) lineage edges, and its value moved between the two
    snapshots. Anomalies on the same field only "reach" it through a
    self-loop, which the spec excludes. Evidence is sorted by path
    length, then the lexicographic field sequence, then identity.
    """
    target_field = _field_of(identity)
    sample_id = identity[3]

    qualified: List[Tuple[Tuple[FieldId, ...], AnomalyId]] = []
    for peer in peers:
        if peer == identity or peer[3] != sample_id:
            continue
        peer_field = _field_of(peer)
        if peer_field == target_field:
            # Same field can only be reached via a (excluded) self-loop.
            continue
        path = _shortest_forward_path(peer_field, target_field, downstream_of)
        if path is None:
            continue
        baseline_value = baseline_index.get(peer, {}).get("value")
        current_value = current_index.get(peer, {}).get("value")
        if _json_equal(baseline_value, current_value):
            continue
        qualified.append((path, peer))

    qualified.sort(key=lambda item: (len(item[0]), item[0], item[1]))

    upstream_changes: List[Dict[str, str]] = []
    paths: List[List[Dict[str, str]]] = []
    for path, peer in qualified:
        upstream_changes.append(
            {
                "rule_id": peer[0],
                "dataset_id": peer[1],
                "field_id": peer[2],
                "sample_id": peer[3],
            }
        )
        paths.append([_field_ref(node) for node in path])
    return upstream_changes, paths


def compare_quality_snapshots(payload: Any) -> Dict[str, Any]:
    """Compare the violated anomalies of two result snapshots.

    :param payload: object with exactly the keys ``baseline``, ``current``
        and ``lineage``. ``baseline`` and ``current`` are objects with
        exactly the key ``results`` -- a list of check result objects
        following the correlate contract (exactly the keys ``rule_id``,
        ``dataset_id``, ``field_id``, ``sample_id``, ``violated`` and
        ``value``; the same rule repeated on the same sample must not
        conflict). ``lineage`` follows
        :func:`data_quality.correlation.correlate_violations`.
    :returns: ``{"status": "ok", "summary": {...}, "changes": [...]}``.
        ``summary`` holds ``baseline_violation_count``,
        ``current_violation_count``, ``new_count``, ``resolved_count`` and
        ``persistent_count``. Each change has exactly the keys ``state``
        (``"new"``, ``"resolved"`` or ``"persisted"``), ``rule_id``,
        ``dataset_id``, ``field_id``, ``sample_id``, ``baseline_value``,
        ``current_value``, ``upstream_changes`` and ``paths``; the missing
        side's value is ``null`` and persisted changes keep both original
        values. ``changes`` are sorted by their four-part identity.
        ``upstream_changes`` lists the other value-changing anomalies of
        the same sample on upstream-reachable fields; the same-order
        ``paths`` list holds one shortest field sequence per upstream
        change (ties broken by lexicographic field order).
    :raises ValueError: :class:`InvalidSnapshotInputError` on malformed
        snapshots, results or lineage graphs.
    :raises LookupError: :class:`UnknownSnapshotReferenceError` when a
        check result of either snapshot references a dataset or field not
        declared in the lineage graph.
    """
    if not isinstance(payload, dict):
        raise InvalidSnapshotInputError("payload must be an object")
    keys = set(payload)
    if keys != _PAYLOAD_KEYS:
        missing = sorted(_PAYLOAD_KEYS - keys)
        if missing:
            raise _input_error(f"payload is missing keys: {missing}")
        unknown = sorted(keys - _PAYLOAD_KEYS)
        raise _input_error(f"payload has unsupported keys: {unknown}")

    try:
        declared_datasets, declared_fields, edges = _validate_lineage(
            payload["lineage"]
        )
    except InvalidCorrelationInputError as exc:
        raise InvalidSnapshotInputError(str(exc)) from exc
    baseline_records = _validate_snapshot(
        payload["baseline"], "baseline"
    )
    current_records = _validate_snapshot(payload["current"], "current")
    _check_references(baseline_records, declared_datasets, declared_fields)
    _check_references(current_records, declared_datasets, declared_fields)

    baseline_index = _violated_index(baseline_records)
    current_index = _violated_index(current_records)

    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)

    baseline_ids = set(baseline_index)
    current_ids = set(current_index)
    new_ids = sorted(current_ids - baseline_ids)
    resolved_ids = sorted(baseline_ids - current_ids)
    persistent_ids = sorted(baseline_ids & current_ids)
    all_peers = new_ids + resolved_ids + persistent_ids

    def build_change(identity: AnomalyId, state: str) -> Dict[str, Any]:
        rule_id, dataset_id, field_id, sample_id = identity
        baseline_record = baseline_index.get(identity)
        current_record = current_index.get(identity)
        upstream_changes, paths = _upstream_evidence(
            identity,
            all_peers,
            baseline_index,
            current_index,
            downstream_of,
        )
        return {
            "state": state,
            "rule_id": rule_id,
            "dataset_id": dataset_id,
            "field_id": field_id,
            "sample_id": sample_id,
            "baseline_value": (
                None if baseline_record is None else baseline_record["value"]
            ),
            "current_value": (
                None if current_record is None else current_record["value"]
            ),
            "upstream_changes": upstream_changes,
            "paths": paths,
        }

    changes: List[Dict[str, Any]] = []
    changes.extend(build_change(identity, "new") for identity in new_ids)
    changes.extend(
        build_change(identity, "resolved") for identity in resolved_ids
    )
    changes.extend(
        build_change(identity, "persisted") for identity in persistent_ids
    )
    # Every identity belongs to exactly one state set, so sorting by the
    # four-part identity gives the mandated global lexicographic order.
    changes.sort(
        key=lambda change: (
            change["rule_id"],
            change["dataset_id"],
            change["field_id"],
            change["sample_id"],
        )
    )

    return {
        "status": "ok",
        "summary": {
            "baseline_violation_count": len(baseline_index),
            "current_violation_count": len(current_index),
            "new_count": len(new_ids),
            "resolved_count": len(resolved_ids),
            "persistent_count": len(persistent_ids),
        },
        "changes": changes,
    }
