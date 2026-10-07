"""Cross-sample violation origin/impact evidence analysis.

The public entry point is :func:`analyze_linked_origins`. It is the
cross-sample counterpart of
:func:`data_quality.origin_analysis.analyze_violation_origins`: it takes
the same check ``results`` and field ``lineage`` graph, plus an
undirected ``sample_links`` graph relating samples across datasets, and
a list of ``targets`` -- ``{"dataset_id", "field_id", "sample_id"}``
triples naming a violated result. For every target it explains which
violations on linked samples are origin evidence (the evidence field is
the target field itself or lies upstream of it along the field lineage)
and which are impact evidence (the evidence field is another field
reachable downstream of the target field).

Every evidence item carries two paths:

* ``sample_path`` -- the shortest undirected path through
  ``sample_links`` from the target sample reference
  (``dataset_id``/``sample_id``) to the evidence sample reference;
  endpoints included, the target sample itself yields a one-node path.
* ``path`` -- written in the canonical forward (upstream -> downstream)
  direction of the field lineage: an origin path runs from the evidence
  field forward to the target field, an impact path runs from the target
  field forward to the evidence field.

Both paths are shortest in edge count; ties are broken by the
lexicographic order of the complete reference sequence. A self-loop on
the target field never makes the target its own impact evidence, and the
same sample on the same field never enters ``impactEvidence``. Evidence
is sorted by sample path length, field path length and then its
identifiers.

Malformed results, lineage graphs, sample links or target lists raise
:class:`LinkedOriginInputError` (a :class:`ValueError`); check results
referencing datasets or fields not declared in the lineage graph, and
sample-link endpoints that do not match a result, raise
:class:`LinkedOriginReferenceError`; targets with no matching violated
result raise :class:`LinkedOriginTargetError` (both
:class:`LookupError`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .correlation import (
    InvalidCorrelationInputError,
    InvalidLinkedCorrelationInputError,
    UnknownLinkedCorrelationReferenceError,
    _validate_lineage,
    _validate_results,
    _validate_sample_links,
)
from .origin_analysis import (
    InvalidOriginInputError,
    _shortest_forward_path,
    _validate_targets,
)

__all__ = [
    "analyze_linked_origins",
    "LinkedOriginInputError",
    "LinkedOriginReferenceError",
    "LinkedOriginTargetError",
]

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]

# A sample reference is identified by its (dataset, sample) pair.
SampleRef = Tuple[str, str]

# A target is identified by its (dataset, field, sample) triple.
TargetRef = Tuple[str, str, str]


class LinkedOriginInputError(ValueError):
    """The results, lineage graph, sample links or targets are malformed."""

    code = "INVALID_INPUT"


class LinkedOriginReferenceError(LookupError):
    """A check result or sample-link endpoint references an unknown node."""

    code = "UNKNOWN_REFERENCE"


class LinkedOriginTargetError(LookupError):
    """A target has no matching violated check result."""

    code = "UNKNOWN_TARGET"


def _check_references(
    records: List[Dict[str, Any]],
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
) -> None:
    for record in records:
        dataset = record["dataset_id"]
        if dataset not in declared_datasets:
            raise LinkedOriginReferenceError(
                f"unknown dataset id: {dataset!r}"
            )
        if (dataset, record["field_id"]) not in declared_fields:
            raise LinkedOriginReferenceError(
                f"unknown field: {dataset!r}.{record['field_id']}"
            )


def _shortest_sample_path(
    start: SampleRef,
    goal: SampleRef,
    neighbours: Dict[SampleRef, Set[SampleRef]],
) -> Optional[Tuple[SampleRef, ...]]:
    """Shortest undirected path ``start`` -> ``goal``, endpoints included.

    The path length is measured in sample-link edges; among the shortest
    paths the lexicographically smallest complete sample-reference
    sequence wins. A breadth first pass fixes the shortest distance of
    every sample reference, and within one distance layer only the
    lexicographically smallest sequence reaching each reference is kept.
    Returns ``None`` when ``goal`` is not reachable from ``start``.
    """
    if start == goal:
        return (start,)

    best: Dict[SampleRef, Tuple[SampleRef, ...]] = {start: (start,)}
    frontier = [start]
    while frontier:
        next_best: Dict[SampleRef, Tuple[SampleRef, ...]] = {}
        for node in frontier:
            for nxt in neighbours.get(node, ()):
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


def _field_path_items(path: Tuple[FieldId, ...]) -> List[Dict[str, str]]:
    return [
        {"dataset_id": dataset, "field_id": field}
        for dataset, field in path
    ]


def _sample_path_items(path: Tuple[SampleRef, ...]) -> List[Dict[str, str]]:
    return [
        {"dataset_id": dataset, "sample_id": sample}
        for dataset, sample in path
    ]


def _evidence(
    record: Dict[str, Any],
    sample_path: Tuple[SampleRef, ...],
    field_path: Tuple[FieldId, ...],
) -> Dict[str, Any]:
    """Keep every check-result key and add the two evidence paths."""
    evidence = dict(record)
    evidence["sample_path"] = _sample_path_items(sample_path)
    evidence["path"] = _field_path_items(field_path)
    return evidence


def _evidence_sort_key(evidence: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        len(evidence["sample_path"]),
        len(evidence["path"]),
        evidence["dataset_id"],
        evidence["field_id"],
        evidence["rule_id"],
        evidence["sample_id"],
    )


def analyze_linked_origins(
    results: Any, lineage: Any, sample_links: Any, targets: Any
) -> Dict[str, Any]:
    """Explain cross-sample origin and impact evidence of violated targets.

    :param results: see
        :func:`data_quality.correlation.correlate_violations`.
    :param lineage: see
        :func:`data_quality.correlation.correlate_violations`.
    :param sample_links: see
        :func:`data_quality.correlation.correlate_linked_violations`:
        an undirected list of ``{"left", "right"}`` links whose
        endpoints are ``{"dataset_id", "sample_id"}`` references that
        must match a result.
    :param targets: list of target objects, each with exactly the keys
        ``dataset_id``, ``field_id`` and ``sample_id`` (non-empty
        strings). Every target must match at least one violated result
        on all three keys; the original order and duplicate entries are
        preserved.
    :returns: ``{"reports": [...]}`` with one report per target, in the
        original ``targets`` order; an empty ``targets`` list yields
        ``{"reports": []}``. Each report is ``{"target",
        "originEvidence", "impactEvidence"}`` where ``target`` echoes
        the ``{"dataset_id", "field_id", "sample_id"}`` triple.
        ``originEvidence`` holds violated results on samples reachable
        through ``sample_links`` whose field is the target field itself
        or lies upstream of it (so the target result on the target
        sample is always origin evidence); ``impactEvidence`` holds
        violated results on reachable samples whose field is another
        field reachable downstream of the target field. A result on the
        same sample and the same field as the target never enters
        ``impactEvidence`` (so a self-loop never yields self impact).
        Every evidence item keeps all check-result keys
        (``rule_id``, ``dataset_id``, ``field_id``, ``sample_id``,
        ``violated``, ``value``) and adds ``sample_path`` (the shortest
        undirected chain of ``{"dataset_id", "sample_id"}`` items from
        the target sample to the evidence sample) and ``path`` (the
        shortest forward chain of ``{"dataset_id", "field_id"}`` items,
        evidence field -> target field for origins and target field ->
        evidence field for impacts). Path ties are broken by the
        lexicographic complete reference sequence. Evidence is sorted by
        sample path length, field path length, ``dataset_id``,
        ``field_id``, ``rule_id`` and ``sample_id``; a target with no
        qualifying evidence gets an empty list.
    :raises ValueError: :class:`LinkedOriginInputError` on malformed
        results, lineage graphs, sample links or target lists.
    :raises LookupError: :class:`LinkedOriginReferenceError` when a
        check result references a dataset or field not declared in the
        lineage graph or a sample-link endpoint matches no result, and
        :class:`LinkedOriginTargetError` when a target has no matching
        violated result.
    """
    try:
        declared_datasets, declared_fields, edges = _validate_lineage(lineage)
        records = _validate_results(results)
    except InvalidCorrelationInputError as exc:
        raise LinkedOriginInputError(str(exc)) from exc
    try:
        target_refs = _validate_targets(targets)
    except InvalidOriginInputError as exc:
        raise LinkedOriginInputError(str(exc)) from exc
    _check_references(records, declared_datasets, declared_fields)
    try:
        links = _validate_sample_links(sample_links, records)
    except InvalidLinkedCorrelationInputError as exc:
        raise LinkedOriginInputError(str(exc)) from exc
    except UnknownLinkedCorrelationReferenceError as exc:
        raise LinkedOriginReferenceError(str(exc)) from exc

    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)

    sample_neighbours: Dict[SampleRef, Set[SampleRef]] = {
        (record["dataset_id"], record["sample_id"]): set()
        for record in records
    }
    for left, right in links:
        sample_neighbours[left].add(right)
        sample_neighbours[right].add(left)

    violated_records = [record for record in records if record["violated"]]

    reports: List[Dict[str, Any]] = []
    for dataset_id, field_id, sample_id in target_refs:
        target_field = (dataset_id, field_id)
        target_sample = (dataset_id, sample_id)
        if not any(
            record["dataset_id"] == dataset_id
            and record["field_id"] == field_id
            and record["sample_id"] == sample_id
            for record in violated_records
        ):
            raise LinkedOriginTargetError(
                f"target has no violated result: "
                f"{{'dataset_id': {dataset_id!r}, "
                f"'field_id': {field_id!r}, 'sample_id': {sample_id!r}}}"
            )

        origin_evidence: List[Dict[str, Any]] = []
        impact_evidence: List[Dict[str, Any]] = []
        for record in violated_records:
            evidence_sample = (record["dataset_id"], record["sample_id"])
            sample_path = _shortest_sample_path(
                target_sample, evidence_sample, sample_neighbours
            )
            if sample_path is None:
                # The evidence sample belongs to another sample-link
                # component and cannot be attributed to this target.
                continue

            field = (record["dataset_id"], record["field_id"])
            if field == target_field:
                # The target field itself (on this or on a linked
                # sample) is always self origin evidence with a
                # one-node field path; it can never be its own impact
                # evidence, regardless of self loops.
                origin_evidence.append(
                    _evidence(record, sample_path, (target_field,))
                )
                continue

            origin_path = _shortest_forward_path(
                field, target_field, downstream_of
            )
            if origin_path is not None:
                origin_evidence.append(
                    _evidence(record, sample_path, origin_path)
                )
            impact_path = _shortest_forward_path(
                target_field, field, downstream_of
            )
            if impact_path is not None:
                impact_evidence.append(
                    _evidence(record, sample_path, impact_path)
                )

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
