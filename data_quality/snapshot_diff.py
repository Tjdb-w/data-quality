"""Anomaly drift comparison between two quality check snapshots.

The public entry point is :func:`compare_quality_snapshots`. It consumes
two already evaluated check-result snapshots (``baseline`` and
``current``, each carrying ``results`` in the same shape as
:func:`data_quality.correlation.correlate_violations`) plus one shared
field ``lineage`` graph. No rule is re-run: the snapshots are interpreted
as-is and only violated results are compared.

A violation is identified by the quadruple
``(rule_id, dataset_id, field_id, sample_id)``:

* ``new`` violations occur in ``current`` only;
* ``resolved`` violations occur in ``baseline`` only;
* ``persisted`` violations occur in both snapshots.

For every change, ``upstream_changes`` names the other value-drifting
violations of the same sample that sit on fields upstream-reachable to
the change's field along the lineage graph (a forward path from the
evidence field to the target field exists). The entry itself and
self-loops never qualify. ``paths`` gives, in the same order, the
shortest field sequence of each such path; ties are broken by the
lexicographic order of the complete field sequence.

Malformed snapshots, results or lineage graphs raise
:class:`InvalidSnapshotInputError` (a :class:`ValueError`); check results
referencing datasets or fields not declared in the lineage graph raise
:class:`UnknownSnapshotReferenceError` (a :class:`LookupError`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

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

_SNAPSHOT_KEYS = frozenset({"baseline", "current", "lineage"})
_RESULTS_KEYS = frozenset({"results"})

# A violation is identified by (rule, dataset, field, sample).
ViolationId = Tuple[str, str, str, str]

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]


class InvalidSnapshotInputError(ValueError):
    """The snapshots, check results or lineage graph are malformed."""

    code = "INVALID_SNAPSHOT_INPUT"


class UnknownSnapshotReferenceError(LookupError):
    """A check result references an undeclared dataset or field."""

    code = "UNKNOWN_SNAPSHOT_REFERENCE"


def _input_error(message: str) -> InvalidSnapshotInputError:
    return InvalidSnapshotInputError(message)


def _check_keys(
    obj: Any, expected: frozenset, where: str
) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise _input_error(f"{where} must be an object")
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise _input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise _input_error(f"{where} has unsupported keys: {unknown}")
    return obj


def _violation_map(
    records: List[Dict[str, Any]],
) -> Dict[ViolationId, Dict[str, Any]]:
    """Index the violated records by their four-part identity."""
    return {
        (
            record["rule_id"],
            record["dataset_id"],
            record["field_id"],
            record["sample_id"],
        ): record
        for record in records
        if record["violated"]
    }


def _check_references(
    records: List[Dict[str, Any]],
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
    where: str,
) -> None:
    for record in records:
        dataset = record["dataset_id"]
        if dataset not in declared_datasets:
            raise UnknownSnapshotReferenceError(
                f"{where} references an unknown dataset id: {dataset!r}"
            )
        if (dataset, record["field_id"]) not in declared_fields:
            raise UnknownSnapshotReferenceError(
                f"{where} references an unknown field: "
                f"{dataset!r}.{record['field_id']}"
            )


def _value_drifted(
    identity: ViolationId,
    state: str,
    baseline: Dict[ViolationId, Dict[str, Any]],
    current: Dict[ViolationId, Dict[str, Any]],
) -> bool:
    """Whether the other anomaly itself carries a value change.

    A new or resolved anomaly only exists on one side, so its appearance
    or disappearance is a value change regardless of the observed value.
    A persisted anomaly only counts when its two observed values differ
    under JSON equality (booleans never equal numbers, containers compare
    structurally).
    """
    if state in ("new", "resolved"):
        return True
    return not _json_equal(
        baseline[identity]["value"], current[identity]["value"]
    )


def _identity_ref(identity: ViolationId) -> Dict[str, str]:
    rule_id, dataset_id, field_id, sample_id = identity
    return {
        "rule_id": rule_id,
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
    }


def _field_path_refs(path: Tuple[FieldId, ...]) -> List[Dict[str, str]]:
    return [
        {"dataset_id": dataset, "field_id": field}
        for dataset, field in path
    ]


def compare_quality_snapshots(payload: Any) -> Dict[str, Any]:
    """Compare the violated results of two quality check snapshots.

    :param payload: object with exactly the keys ``baseline``,
        ``current`` and ``lineage``. ``baseline`` and ``current`` are
        objects with exactly the key ``results``; each results list
        follows the :func:`~data_quality.correlation.correlate_violations`
        contract (exact keys, non-empty string identifiers, boolean
        ``violated``, any JSON ``value``, conflicting duplicates
        rejected). ``lineage`` is the shared field lineage graph and
        follows the same contract as in ``correlate``.
    :returns: ``{"status": "ok", "summary": {...}, "changes": [...]}``.
        The summary carries ``baseline_violation_count``,
        ``current_violation_count``, ``new_count``, ``resolved_count`` and
        ``persistent_count``. Each change carries, in order, ``state``
        (``"new"``, ``"resolved"`` or ``"persisted"``), ``rule_id``,
        ``dataset_id``, ``field_id``, ``sample_id``, ``baseline_value``,
        ``current_value``, ``upstream_changes`` and ``paths``; the value
        on the missing side is ``null`` and a persisted change keeps both
        original values. Changes are sorted by the lexicographic order of
        their identity quadruple. ``upstream_changes`` lists the other
        same-sample value-drifting violations whose field is upstream of
        the change's field (the entry itself and self-loops are excluded),
        as identity objects sorted the same way; ``paths`` holds, in the
        same order, the shortest field sequence from each evidence field
        to the target field, ties broken by lexicographic field sequence.
    :raises ValueError: :class:`InvalidSnapshotInputError` on malformed
        snapshots, results or lineage graphs.
    :raises LookupError: :class:`UnknownSnapshotReferenceError` when a
        check result references a dataset or field not declared in the
        lineage graph.
    """
    _check_keys(payload, _SNAPSHOT_KEYS, "payload")
    baseline_snapshot = _check_keys(
        payload["baseline"], _RESULTS_KEYS, "baseline"
    )
    current_snapshot = _check_keys(payload["current"], _RESULTS_KEYS, "current")

    try:
        declared_datasets, declared_fields, edges = _validate_lineage(
            payload["lineage"]
        )
        baseline_records = _validate_results(baseline_snapshot["results"])
        current_records = _validate_results(current_snapshot["results"])
    except InvalidCorrelationInputError as exc:
        raise InvalidSnapshotInputError(str(exc)) from exc

    _check_references(
        baseline_records, declared_datasets, declared_fields, "baseline"
    )
    _check_references(
        current_records, declared_datasets, declared_fields, "current"
    )

    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)

    baseline_violations = _violation_map(baseline_records)
    current_violations = _violation_map(current_records)

    states: Dict[ViolationId, str] = {}
    for identity in current_violations:
        states[identity] = (
            "persisted" if identity in baseline_violations else "new"
        )
    for identity in baseline_violations:
        if identity not in current_violations:
            states[identity] = "resolved"

    identities = sorted(states)
    drifted = {
        identity: _value_drifted(
            identity, states[identity], baseline_violations, current_violations
        )
        for identity in identities
    }

    by_sample: Dict[str, List[ViolationId]] = {}
    for identity in identities:
        by_sample.setdefault(identity[3], []).append(identity)

    changes: List[Dict[str, Any]] = []
    for identity in identities:
        rule_id, dataset_id, field_id, sample_id = identity
        state = states[identity]
        baseline_record = baseline_violations.get(identity)
        current_record = current_violations.get(identity)
        target_field: FieldId = (dataset_id, field_id)

        upstream_changes: List[Dict[str, str]] = []
        paths: List[List[Dict[str, str]]] = []
        for candidate in by_sample.get(sample_id, ()):
            if candidate == identity or not drifted[candidate]:
                continue
            evidence_field: FieldId = (candidate[1], candidate[2])
            path: Optional[Tuple[FieldId, ...]] = _shortest_forward_path(
                evidence_field, target_field, downstream_of
            )
            # A path needs at least one real edge, so the entry itself
            # and same-field evidence reached only via a self-loop are
            # excluded.
            if path is None or len(path) < 2:
                continue
            upstream_changes.append(_identity_ref(candidate))
            paths.append(_field_path_refs(path))

        changes.append(
            {
                "state": state,
                "rule_id": rule_id,
                "dataset_id": dataset_id,
                "field_id": field_id,
                "sample_id": sample_id,
                "baseline_value": (
                    None if baseline_record is None
                    else baseline_record["value"]
                ),
                "current_value": (
                    None if current_record is None
                    else current_record["value"]
                ),
                "upstream_changes": upstream_changes,
                "paths": paths,
            }
        )

    return {
        "status": "ok",
        "summary": {
            "baseline_violation_count": len(baseline_violations),
            "current_violation_count": len(current_violations),
            "new_count": sum(1 for state in states.values() if state == "new"),
            "resolved_count": sum(
                1 for state in states.values() if state == "resolved"
            ),
            "persistent_count": sum(
                1 for state in states.values() if state == "persisted"
            ),
        },
        "changes": changes,
    }
