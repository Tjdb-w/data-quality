"""Cross-sample anomaly origin/impact evidence analysis.

The public entry point is :func:`analyze_linked_origins`. It extends
:func:`data_quality.origin_analysis.analyze_violation_origins` beyond a
single sample: in addition to the check ``results`` and the field
``lineage`` graph it takes an undirected ``sample_links`` graph relating
samples (potentially across datasets) and explains violated ``targets``
with evidence reached through both graphs.

Evidence paths come in two orthogonal dimensions:

* ``sample_path`` -- the shortest undirected path over
  ``dataset_id``/``sample_id`` references from the target sample to the
  evidence sample (a single node for evidence on the target sample);
* ``path`` -- the shortest path written in the canonical forward
  (upstream -> downstream) direction of the field lineage graph, from the
  evidence field forward to the target field (origin evidence) or from
  the target field forward to the evidence field (impact evidence).

A violated result on the target field itself or on one of its upstream
fields is origin evidence; a violated result on another field the target
field can reach along forward edges is impact evidence. The self
exclusion for impact evidence is scoped to the target sample: a violated
result on the target field in another linked sample is both origin and
impact evidence, while the target sample's own result on the target field
is never its own impact (a lineage self-loop never creates such an
impact either). Samples not connected to the target sample through
``sample_links`` provide no evidence.

Every shortest path is minimal in edge count; ties are broken by the
lexicographic order of the complete reference sequence. Evidence is
sorted by field path length, then dataset, field, sample and rule
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
    _check_sample_link_references,
    _validate_lineage,
    _validate_results,
    _validate_sample_link_structure,
)

__all__ = [
    "analyze_linked_origins",
    "LinkedOriginInputError",
    "LinkedOriginReferenceError",
    "LinkedOriginTargetError",
]

_TARGET_KEYS = frozenset({"dataset_id", "field_id", "sample_id"})

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
    """A reference cannot be resolved in the lineage graph or results."""

    code = "UNKNOWN_REFERENCE"


class LinkedOriginTargetError(LookupError):
    """A target has no matching violated check result."""

    code = "UNKNOWN_TARGET"


def _input_error(message: str) -> LinkedOriginInputError:
    return LinkedOriginInputError(message)


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
            raise LinkedOriginReferenceError(
                f"unknown dataset id: {dataset!r}"
            )
        if (dataset, record["field_id"]) not in declared_fields:
            raise LinkedOriginReferenceError(
                f"unknown field: {dataset!r}.{record['field_id']}"
            )


def _shortest_forward_path(
    start: FieldId,
    goal: FieldId,
    downstream_of: Dict[FieldId, Set[FieldId]],
) -> Optional[Tuple[FieldId, ...]]:
    """Shortest forward field path from ``start`` to ``goal``.

    Endpoints are included. The path length is measured in edges; among
    the shortest paths the lexicographically smallest complete field
    sequence wins. A breadth first pass fixes the shortest distance of
    every field, and within one distance layer only the
    lexicographically smallest sequence reaching each field is kept.
    Returns ``None`` when ``goal`` is not reachable along forward edges.
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


def _shortest_sample_path(
    start: SampleRef,
    goal: SampleRef,
    neighbours: Dict[SampleRef, Set[SampleRef]],
) -> Optional[Tuple[SampleRef, ...]]:
    """Shortest undirected sample-link path from ``start`` to ``goal``.

    Endpoints are included; ties are broken by the lexicographic order of
    the complete ``(dataset_id, sample_id)`` sequence. Returns ``None``
    when the two samples are not connected.
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


def _field_path(path: Tuple[FieldId, ...]) -> List[Dict[str, str]]:
    return [
        {"dataset_id": dataset, "field_id": field}
        for dataset, field in path
    ]


def _sample_path(path: Tuple[SampleRef, ...]) -> List[Dict[str, str]]:
    return [
        {"dataset_id": dataset, "sample_id": sample}
        for dataset, sample in path
    ]


def _evidence(
    record: Dict[str, Any],
    field_path_value: Tuple[FieldId, ...],
    sample_path_value: Tuple[SampleRef, ...],
) -> Dict[str, Any]:
    # Preserve every check-result key, then append the two evidence paths.
    item = dict(record)
    item["sample_path"] = _sample_path(sample_path_value)
    item["path"] = _field_path(field_path_value)
    return item


def _evidence_sort_key(evidence: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        len(evidence["path"]),
        evidence["dataset_id"],
        evidence["field_id"],
        evidence["sample_id"],
        evidence["rule_id"],
    )


def analyze_linked_origins(
    results: Any, lineage: Any, sample_links: Any, targets: Any
) -> Dict[str, Any]:
    """Explain target anomalies with field lineage and linked samples.

    :param results: see
        :func:`data_quality.correlation.correlate_violations`.
    :param lineage: see
        :func:`data_quality.correlation.correlate_violations`.
    :param sample_links: see
        :func:`data_quality.correlation.correlate_linked_violations`:
        undirected ``{"left", "right"}`` links whose endpoints are
        ``{"dataset_id", "sample_id"}`` objects matching a result.
    :param targets: list of target objects, each with exactly the keys
        ``dataset_id``, ``field_id`` and ``sample_id`` (non-empty
        strings). Every target must match at least one violated result
        on all three keys; the original order (including duplicates) is
        preserved.
    :returns: ``{"reports": [...]}`` with one report per target. Each
        report is ``{"target", "originEvidence", "impactEvidence"}``;
        ``target`` echoes the ``{"dataset_id", "field_id", "sample_id"}``
        triple. An evidence item keeps all of its check-result keys
        (``rule_id``, ``dataset_id``, ``field_id``, ``sample_id``,
        ``violated``, ``value``) and adds ``sample_path`` -- the shortest
        undirected sequence of ``{"dataset_id", "sample_id"}`` references
        from the target sample to the evidence sample -- and ``path``,
        written in forward field-lineage direction: evidence field to
        target field for ``originEvidence``, target field to evidence
        field for ``impactEvidence``. Origin evidence lies on the target
        field itself or upstream of it; impact evidence lies on other
        fields downstream-reachable from the target field. The target
        sample's own result on the target field is never impact
        evidence, nor does a field self-loop create such self impact;
        the same field in another linked sample is both. Evidence is
        sorted by field path length then dataset, field, sample and rule
        identifiers; targets without qualifying evidence get empty
        lists, and an empty ``targets`` list yields ``{"reports": []}``.
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
        pairs = _validate_sample_link_structure(sample_links)
    except InvalidLinkedCorrelationInputError as exc:
        raise LinkedOriginInputError(str(exc)) from exc

    target_refs = _validate_targets(targets)

    _check_references(records, declared_datasets, declared_fields)

    try:
        _check_sample_link_references(pairs, records)
    except UnknownLinkedCorrelationReferenceError as exc:
        raise LinkedOriginReferenceError(str(exc)) from exc

    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)

    sample_neighbours: Dict[SampleRef, Set[SampleRef]] = {
        (record["dataset_id"], record["sample_id"]): set()
        for record in records
    }
    for left, right in pairs:
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
            record_sample = (
                record["dataset_id"],
                record["sample_id"],
            )
            sample_path_value = _shortest_sample_path(
                target_sample, record_sample, sample_neighbours
            )
            if sample_path_value is None:
                # Evidence must be reachable through the undirected
                # sample-link graph.
                continue

            field = (record["dataset_id"], record["field_id"])
            same_target_sample = record_sample == target_sample
            if field == target_field:
                origin_evidence.append(
                    _evidence(record, (target_field,), sample_path_value)
                )
                if not same_target_sample:
                    # The same field in another linked sample is also an
                    # impact reached through the sample association; the
                    # target sample's own field is never its own impact.
                    impact_evidence.append(
                        _evidence(record, (target_field,), sample_path_value)
                    )
                continue

            origin_path = _shortest_forward_path(
                field, target_field, downstream_of
            )
            if origin_path is not None:
                origin_evidence.append(
                    _evidence(record, origin_path, sample_path_value)
                )
            impact_path = _shortest_forward_path(
                target_field, field, downstream_of
            )
            if impact_path is not None:
                impact_evidence.append(
                    _evidence(record, impact_path, sample_path_value)
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
