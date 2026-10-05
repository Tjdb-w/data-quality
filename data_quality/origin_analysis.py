"""Violation origin/impact evidence analysis over a field lineage graph.

The public entry point is :func:`analyze_violation_origins`. It takes the
same check ``results`` and field ``lineage`` graph as
:func:`data_quality.correlation.correlate_violations` plus a list of
``targets`` -- ``{"dataset_id", "field_id", "sample_id"}`` triples naming a
violated result -- and explains, for every target, which same-sample
violations are its upstream/self origin evidence and which are its
downstream impact evidence.

Evidence paths follow the canonical forward (upstream -> downstream)
direction of the lineage graph: an origin path runs from the evidence
field forward to the target field, an impact path runs from the target
field forward to the evidence field. Every path is the shortest one in
edge count; ties are broken by the lexicographic order of the complete
field sequence. A self-loop on the target field never makes the target
its own impact evidence.

Malformed results, lineage graphs or target lists raise
:class:`InvalidOriginInputError` (a :class:`ValueError`); check results
referencing datasets or fields not declared in the lineage graph raise
:class:`UnknownOriginReferenceError`, and targets with no matching
violated result raise :class:`UnknownOriginTargetError` (both
:class:`LookupError`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .correlation import (
    InvalidCorrelationInputError,
    _validate_lineage,
    _validate_results,
)

__all__ = [
    "analyze_violation_origins",
    "InvalidOriginInputError",
    "UnknownOriginReferenceError",
    "UnknownOriginTargetError",
]

_TARGET_KEYS = frozenset({"dataset_id", "field_id", "sample_id"})

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]

# A target is identified by its (dataset, field, sample) triple.
TargetRef = Tuple[str, str, str]


class InvalidOriginInputError(ValueError):
    """The results, lineage graph or target list are malformed."""

    code = "INVALID_ORIGIN_INPUT"


class UnknownOriginReferenceError(LookupError):
    """A check result references an undeclared dataset or field."""

    code = "UNKNOWN_ORIGIN_REFERENCE"


class UnknownOriginTargetError(LookupError):
    """A target has no matching violated check result."""

    code = "UNKNOWN_ORIGIN_TARGET"


def _input_error(message: str) -> InvalidOriginInputError:
    return InvalidOriginInputError(message)


def _validate_targets(targets: Any) -> List[TargetRef]:
    """Validate ``targets`` and keep the original order (duplicates kept)."""
    if not isinstance(targets, list):
        raise _input_error("targets must be a list")

    validated: List[TargetRef] = []
    for index, target in enumerate(targets):
        where = f"targets[{index}]"
        if not isinstance(target, dict):
            raise _input_error(f"{where} must be an object")
        keys = set(target)
        if keys != _TARGET_KEYS:
            missing = sorted(_TARGET_KEYS - keys)
            if missing:
                raise _input_error(f"{where} is missing keys: {missing}")
            unknown = sorted(keys - _TARGET_KEYS)
            raise _input_error(f"{where} has unsupported keys: {unknown}")
        for key in ("dataset_id", "field_id", "sample_id"):
            if not isinstance(target[key], str) or not target[key]:
                raise _input_error(
                    f"{where}.{key} must be a non-empty string"
                )
        validated.append(
            (target["dataset_id"], target["field_id"], target["sample_id"])
        )
    return validated


def _check_references(
    records: List[Dict[str, Any]],
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
) -> None:
    for record in records:
        dataset = record["dataset_id"]
        if dataset not in declared_datasets:
            raise UnknownOriginReferenceError(
                f"unknown dataset id: {dataset!r}"
            )
        if (dataset, record["field_id"]) not in declared_fields:
            raise UnknownOriginReferenceError(
                f"unknown field: {dataset!r}.{record['field_id']}"
            )


def _shortest_forward_path(
    start: FieldId,
    goal: FieldId,
    downstream_of: Dict[FieldId, Set[FieldId]],
) -> Optional[Tuple[FieldId, ...]]:
    """Shortest forward path from ``start`` to ``goal``, endpoints included.

    The path length is measured in edges; among the shortest paths the
    lexicographically smallest complete field sequence wins. A breadth
    first pass fixes the shortest distance of every field, and within one
    distance layer only the lexicographically smallest sequence reaching
    each field is kept -- the smallest sequence to ``goal`` is then built
    from the smallest sequences one edge closer. Returns ``None`` when
    ``goal`` is not reachable along forward edges.
    """
    if start == goal:
        return (start,)

    best: Dict[FieldId, Tuple[FieldId, ...]] = {start: (start,)}
    frontier = [start]
    while frontier:
        next_best: Dict[FieldId, Tuple[FieldId, ...]] = {}
        for node in frontier:
            for nxt in downstream_of.get(node, ()):
                if nxt in best:
                    continue
                candidate = best[node] + (nxt,)
                if nxt not in next_best or candidate < next_best[nxt]:
                    next_best[nxt] = candidate
        if goal in next_best:
            return next_best[goal]
        best.update(next_best)
        frontier = list(next_best)
    return None


def _evidence(
    record: Dict[str, Any], path: Tuple[FieldId, ...]
) -> Dict[str, Any]:
    return {
        "rule_id": record["rule_id"],
        "dataset_id": record["dataset_id"],
        "field_id": record["field_id"],
        "sample_id": record["sample_id"],
        "violated": record["violated"],
        "value": record["value"],
        "path": [
            {"dataset_id": dataset, "field_id": field}
            for dataset, field in path
        ],
    }


def _evidence_sort_key(evidence: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        len(evidence["path"]),
        evidence["dataset_id"],
        evidence["field_id"],
        evidence["rule_id"],
    )


def analyze_violation_origins(
    results: Any, lineage: Any, targets: Any
) -> Dict[str, Any]:
    """Explain the origin and impact evidence of violated targets.

    :param results: see
        :func:`data_quality.correlation.correlate_violations`.
    :param lineage: see
        :func:`data_quality.correlation.correlate_violations`.
    :param targets: list of target objects, each with exactly the keys
        ``dataset_id``, ``field_id`` and ``sample_id`` (non-empty
        strings). Every target must match at least one violated result
        on all three keys.
    :returns: ``{"reports": [...]}`` with one report per target, in the
        original ``targets`` order; an empty ``targets`` list yields
        ``{"reports": []}``. Each report is ``{"target",
        "originEvidence", "impactEvidence"}`` where ``target`` echoes
        the ``{"dataset_id", "field_id", "sample_id"}`` triple.
        ``originEvidence`` holds the same-sample violated results on the
        target field itself or on fields upstream of it;
        ``impactEvidence`` holds the same-sample violated results on
        other fields reachable from the target along forward edges (a
        self-loop never makes the target its own impact evidence). Each
        evidence item is ``{"rule_id", "dataset_id", "field_id",
        "sample_id", "violated", "value", "path"}``; ``path`` is the
        shortest forward chain of ``{"dataset_id", "field_id"}`` items
        from the evidence field to the target (origin) or from the
        target to the evidence field (impact), ties broken by the
        lexicographic field sequence. Evidence is sorted by path length,
        then ``dataset_id``, ``field_id`` and ``rule_id``; a target with
        no qualifying evidence gets an empty list.
    :raises ValueError: :class:`InvalidOriginInputError` on malformed
        results, lineage graphs or target lists.
    :raises LookupError: :class:`UnknownOriginReferenceError` when a
        check result references a dataset or field not declared in the
        lineage graph, and :class:`UnknownOriginTargetError` when a
        target has no matching violated result.
    """
    try:
        declared_datasets, declared_fields, edges = _validate_lineage(lineage)
        records = _validate_results(results)
    except InvalidCorrelationInputError as exc:
        raise InvalidOriginInputError(str(exc)) from exc
    target_refs = _validate_targets(targets)
    _check_references(records, declared_datasets, declared_fields)

    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)

    violated_records = [record for record in records if record["violated"]]

    reports: List[Dict[str, Any]] = []
    for dataset_id, field_id, sample_id in target_refs:
        target_field = (dataset_id, field_id)
        if not any(
            record["dataset_id"] == dataset_id
            and record["field_id"] == field_id
            and record["sample_id"] == sample_id
            for record in violated_records
        ):
            raise UnknownOriginTargetError(
                f"target has no violated result: "
                f"{{'dataset_id': {dataset_id!r}, "
                f"'field_id': {field_id!r}, 'sample_id': {sample_id!r}}}"
            )

        origin_evidence: List[Dict[str, Any]] = []
        impact_evidence: List[Dict[str, Any]] = []
        for record in violated_records:
            if record["sample_id"] != sample_id:
                continue
            field = (record["dataset_id"], record["field_id"])
            if field == target_field:
                origin_evidence.append(_evidence(record, (target_field,)))
                continue
            origin_path = _shortest_forward_path(
                field, target_field, downstream_of
            )
            if origin_path is not None:
                origin_evidence.append(_evidence(record, origin_path))
            impact_path = _shortest_forward_path(
                target_field, field, downstream_of
            )
            if impact_path is not None:
                impact_evidence.append(_evidence(record, impact_path))

        origin_evidence.sort(key=_evidence_sort_key)
        impact_evidence.sort(key=_evidence_sort_key)
        reports.append(
            {
                "target": {
                    "dataset_id": dataset_id,
                    "field_id": field_id,
                    "sample_id": sample_id,
                },
                "originEvidence": origin_evidence,
                "impactEvidence": impact_evidence,
            }
        )

    return {"reports": reports}
