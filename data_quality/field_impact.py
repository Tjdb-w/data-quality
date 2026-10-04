"""Field-level quality impact analysis over datasets, rules and samples.

The public entry point is :func:`analyze_field_impacts`. It combines a
field-level lineage graph with rule validation results and anomalous
samples: from every seed field it collects the fields reachable along at
least one directed edge (self-loops and cycles leading back to a seed
count as reachability), then reports the non-passing rules touching those
fields together with the anomalous samples that can actually be located.

Dangling edges (an endpoint referencing a dataset or field not declared
in ``datasets``) never abort the analysis: they are skipped during
traversal and written, sorted by their four endpoint keys, to
``unresolvedReferences``.

Structural problems raise :class:`ImpactInputError`, a :class:`ValueError`;
there is no partial result and no other business error.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set, Tuple

__all__ = [
    "analyze_field_impacts",
    "ImpactInputError",
]

_PAYLOAD_KEYS = frozenset(
    {
        "datasets",
        "lineageEdges",
        "validationResults",
        "anomalySamples",
        "seedFields",
    }
)
_EDGE_KEYS = frozenset(
    {"sourceDataset", "sourceField", "targetDataset", "targetField"}
)
_RULE_KEYS = frozenset(
    {"ruleId", "status", "fields", "failedSampleIds"}
)
_SAMPLE_KEYS = frozenset({"dataset", "fieldValues"})
_FIELD_REF_KEYS = frozenset({"dataset", "field"})
_VALID_STATUSES = frozenset({"passed", "failed", "skipped"})

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]


class ImpactInputError(ValueError):
    """The impact-analysis payload is missing or malformed."""


def _error(message: str) -> ImpactInputError:
    return ImpactInputError(message)


def _check_keys(obj: Any, expected: frozenset, where: str) -> None:
    if not isinstance(obj, dict):
        raise _error(f"{where} must be an object")
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise _error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise _error(f"{where} has unsupported keys: {unknown}")


def _check_field_ref(ref: Any, where: str) -> FieldId:
    _check_keys(ref, _FIELD_REF_KEYS, where)
    dataset = ref["dataset"]
    field = ref["field"]
    if not isinstance(dataset, str) or not dataset:
        raise _error(f"{where}.dataset must be a non-empty string")
    if not isinstance(field, str) or not field:
        raise _error(f"{where}.field must be a non-empty string")
    return (dataset, field)


def _full_name(field_id: FieldId) -> str:
    return f"{field_id[0]}.{field_id[1]}"


def _name_path(path: Tuple[FieldId, ...]) -> Tuple[str, ...]:
    """Ordering view of a reference sequence: full names ``dataset.field``."""
    return tuple(_full_name(node) for node in path)


def _validate_datasets(datasets: Any) -> Set[FieldId]:
    """Validate ``datasets`` and return the declared fields.

    Duplicate datasets (as object keys they are already distinct) and
    duplicate field names are legal, so the declarations form a set.
    """
    if not isinstance(datasets, dict):
        raise _error(
            "datasets must be an object mapping dataset ids to field lists"
        )

    declared_fields: Set[FieldId] = set()
    for dataset, fields in datasets.items():
        if not isinstance(dataset, str) or not dataset:
            raise _error(
                f"datasets key must be a non-empty dataset id string: "
                f"{dataset!r}"
            )
        if not isinstance(fields, list):
            raise _error(
                f"datasets[{dataset!r}] must be a list of field ids"
            )
        for index, field in enumerate(fields):
            if not isinstance(field, str) or not field:
                raise _error(
                    f"datasets[{dataset!r}][{index}] must be a non-empty "
                    "field id string"
                )
            declared_fields.add((dataset, field))
    return declared_fields


def _validate_edges(
    edges: Any,
    declared_fields: Set[FieldId],
) -> Tuple[
    List[Tuple[FieldId, FieldId]],
    List[Dict[str, str]],
]:
    """Validate ``lineageEdges``.

    Returns the resolvable directed edges as ``(source, target)`` pairs and
    the dangling edges (an endpoint not declared in ``datasets``) as
    four-key dictionaries. Duplicate edges in either group are legal and
    deduplicated.
    """
    if not isinstance(edges, list):
        raise _error("lineageEdges must be a list")

    resolved_set: Set[Tuple[FieldId, FieldId]] = set()
    unresolved_set: Set[Tuple[str, str, str, str]] = set()

    for index, edge in enumerate(edges):
        _check_keys(edge, _EDGE_KEYS, f"lineageEdges[{index}]")
        source_dataset = edge["sourceDataset"]
        source_field = edge["sourceField"]
        target_dataset = edge["targetDataset"]
        target_field = edge["targetField"]
        for key, value in (
            ("sourceDataset", source_dataset),
            ("sourceField", source_field),
            ("targetDataset", target_dataset),
            ("targetField", target_field),
        ):
            if not isinstance(value, str) or not value:
                raise _error(
                    f"lineageEdges[{index}].{key} must be a non-empty string"
                )

        source = (source_dataset, source_field)
        target = (target_dataset, target_field)
        if source in declared_fields and target in declared_fields:
            resolved_set.add((source, target))
        else:
            unresolved_set.add(
                (source_dataset, source_field, target_dataset, target_field)
            )

    resolved = sorted(resolved_set, key=lambda pair: (_full_name(pair[0]), _full_name(pair[1])))
    unresolved = [
        {
            "sourceDataset": sd,
            "sourceField": sf,
            "targetDataset": td,
            "targetField": tf,
        }
        for sd, sf, td, tf in sorted(unresolved_set)
    ]
    return resolved, unresolved


def _validate_rules(rules: Any) -> List[Dict[str, Any]]:
    """Validate ``validationResults``.

    Rule field references must be structurally valid ``{dataset, field}``
    objects with non-empty identifiers; a reference to a field not declared
    in ``datasets`` is tolerated (it can never intersect a downstream set).
    """
    if not isinstance(rules, list):
        raise _error("validationResults must be a list")

    validated: List[Dict[str, Any]] = []
    for index, rule in enumerate(rules):
        _check_keys(rule, _RULE_KEYS, f"validationResults[{index}]")

        rule_id = rule["ruleId"]
        if not isinstance(rule_id, str) or not rule_id:
            raise _error(
                f"validationResults[{index}].ruleId must be a non-empty string"
            )

        status = rule["status"]
        if not isinstance(status, str) or status not in _VALID_STATUSES:
            raise _error(
                f"validationResults[{index}].status must be one of "
                f"{sorted(_VALID_STATUSES)}, got {status!r}"
            )

        raw_fields = rule["fields"]
        if not isinstance(raw_fields, list):
            raise _error(
                f"validationResults[{index}].fields must be a list"
            )
        fields: Set[FieldId] = set()
        for field_index, ref in enumerate(raw_fields):
            fields.add(
                _check_field_ref(
                    ref,
                    f"validationResults[{index}].fields[{field_index}]",
                )
            )

        failed_ids = rule["failedSampleIds"]
        if not isinstance(failed_ids, list):
            raise _error(
                f"validationResults[{index}].failedSampleIds must be a list"
            )
        for sample_index, sample_id in enumerate(failed_ids):
            if not isinstance(sample_id, str) or not sample_id:
                raise _error(
                    f"validationResults[{index}].failedSampleIds"
                    f"[{sample_index}] must be a non-empty string"
                )

        validated.append(
            {
                "ruleId": rule_id,
                "status": status,
                "fields": fields,
                "failedSampleIds": failed_ids,
            }
        )
    return validated


def _validate_samples(samples: Any) -> Set[str]:
    """Validate ``anomalySamples``; return the located sample ids.

    A sample id is locatable simply by being a key in ``anomalySamples``.
    """
    if not isinstance(samples, dict):
        raise _error(
            "anomalySamples must be an object mapping sample ids to samples"
        )

    for sample_id, sample in samples.items():
        if not isinstance(sample_id, str) or not sample_id:
            raise _error(
                f"anomalySamples key must be a non-empty sample id string: "
                f"{sample_id!r}"
            )
        _check_keys(sample, _SAMPLE_KEYS, f"anomalySamples[{sample_id!r}]")

        dataset = sample["dataset"]
        if not isinstance(dataset, str) or not dataset:
            raise _error(
                f"anomalySamples[{sample_id!r}].dataset must be a "
                "non-empty string"
            )

        field_values = sample["fieldValues"]
        if not isinstance(field_values, dict):
            raise _error(
                f"anomalySamples[{sample_id!r}].fieldValues must be an object"
            )
        for field_name in field_values:
            if not isinstance(field_name, str) or not field_name:
                raise _error(
                    f"anomalySamples[{sample_id!r}].fieldValues key must be "
                    f"a non-empty field id string: {field_name!r}"
                )
            # Values are any JSON value, including null; nothing to check.
    return set(samples)


def _validate_seeds(seeds: Any) -> List[FieldId]:
    """Validate ``seedFields`` and keep the original order (duplicates kept)."""
    if not isinstance(seeds, list):
        raise _error("seedFields must be a list")

    ordered: List[FieldId] = []
    for index, ref in enumerate(seeds):
        ordered.append(_check_field_ref(ref, f"seedFields[{index}]"))
    return ordered


def _shortest_paths(
    start: FieldId,
    outgoing: Dict[FieldId, List[FieldId]],
) -> Dict[FieldId, Tuple[FieldId, ...]]:
    """Shortest reference sequence from ``start`` to every field reachable
    along one or more edges.

    A plain BFS fixes the shortest distance of every field; the
    lexicographically smallest sequence of that length is then a dynamic
    program by increasing distance -- each field prepends the smallest
    sequence of a predecessor one edge closer. A self-loop or a longer
    cycle leading back to the seed also makes the seed reachable: its
    sequence is the smallest ``path(u) + (seed,)`` over every edge
    ``u -> seed``.
    """
    dist: Dict[FieldId, int] = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in outgoing.get(current, ()):
            if nxt not in dist:
                dist[nxt] = dist[current] + 1
                queue.append(nxt)

    # Incoming adjacency lets every field scan its predecessors.
    incoming: Dict[FieldId, List[FieldId]] = {}
    for source, targets in outgoing.items():
        for target in targets:
            incoming.setdefault(target, []).append(source)

    best: Dict[FieldId, Tuple[FieldId, ...]] = {start: (start,)}
    for depth in range(1, max(dist.values(), default=0) + 1):
        for field_id in (node for node, d in dist.items() if d == depth):
            candidates = [
                best[predecessor] + (field_id,)
                for predecessor in incoming.get(field_id, ())
                if dist.get(predecessor) == depth - 1
            ]
            best[field_id] = min(candidates, key=_name_path)

    paths = {node: best[node] for node in dist if node != start}

    # Any edge u -> start closes a cycle; the shortest one wins, then the
    # lexicographically smallest reference sequence.
    cycles = [
        best[node] + (start,)
        for node in incoming.get(start, ())
        if node != start
    ]
    self_loop = start in outgoing.get(start, ())
    if self_loop:
        cycles.append((start, start))
    if cycles:
        paths[start] = min(
            cycles, key=lambda sequence: (len(sequence), _name_path(sequence))
        )

    return paths


def analyze_field_impacts(payload: Any) -> Dict[str, Any]:
    """Analyze the downstream quality impact of a batch of seed fields.

    :param payload: object with exactly the keys ``datasets``,
        ``lineageEdges``, ``validationResults``, ``anomalySamples`` and
        ``seedFields``. ``datasets`` maps non-empty dataset ids to lists of
        non-empty field ids; ``lineageEdges`` lists ``{sourceDataset,
        sourceField, targetDataset, targetField}`` objects;
        ``validationResults`` lists ``{ruleId, status, fields,
        failedSampleIds}`` rules with ``status`` one of ``passed`` /
        ``failed`` / ``skipped`` and ``fields`` a list of ``{dataset,
        field}`` references declared in ``datasets``; ``anomalySamples``
        maps non-empty sample ids to ``{dataset, fieldValues}``;
        ``seedFields`` is a list of ``{dataset, field}`` references.
    :returns: ``{"status": "ok", "impacts": [...],
        "unresolvedReferences": [...]}``. ``impacts`` keeps the original
        ``seedFields`` order; each impact is ``{"seed", "downstreamFields",
        "affectedRules", "anomalySamples", "paths"}``. Downstream fields
        are the fields reachable along one or more resolvable directed
        edges (self-loops and edges back to the seed count), deduplicated
        and sorted by full name ``dataset.field``; rules are the
        non-``passed`` rules touching a downstream field, sorted by
        ``ruleId``; samples are the locatable ids (those present in
        ``anomalySamples``) among their ``failedSampleIds``, sorted
        ascending; ``paths`` holds one reference sequence per downstream
        field in the same ascending full-name order, each the shortest
        chain of ``{dataset, field}`` refs from the seed to that field
        (ties broken lexicographically). Dangling edges are skipped
        during traversal and listed (four keys, sorted) in
        ``unresolvedReferences``. An unknown seed has an empty impact; an
        empty ``seedFields`` yields an empty ``impacts`` while dangling
        edges are still reported.
    :raises ValueError: :class:`ImpactInputError` on any malformed payload.
    """
    _check_keys(payload, _PAYLOAD_KEYS, "payload")

    declared_fields = _validate_datasets(payload["datasets"])
    resolved_edges, unresolved = _validate_edges(
        payload["lineageEdges"], declared_fields
    )
    rules = _validate_rules(payload["validationResults"])
    locatable_samples = _validate_samples(payload["anomalySamples"])
    seeds = _validate_seeds(payload["seedFields"])

    outgoing: Dict[FieldId, List[FieldId]] = {}
    for source, target in resolved_edges:
        outgoing.setdefault(source, []).append(target)

    impacts: List[Dict[str, Any]] = []
    for seed in seeds:
        if seed not in declared_fields:
            shortest: Dict[FieldId, Tuple[FieldId, ...]] = {}
        else:
            shortest = _shortest_paths(seed, outgoing)

        downstream = set(shortest)
        downstream_names = sorted(downstream, key=_full_name)

        # Qualifying rules are non-passed rules that touch at least one
        # downstream field; affectedRules lists their ids (deduped) and
        # anomalySamples the locatable ids among their failedSampleIds.
        qualifying = [
            rule
            for rule in rules
            if rule["status"] != "passed" and rule["fields"] & downstream
        ]
        affected_rule_ids = sorted(
            {rule["ruleId"] for rule in qualifying}
        )
        anomaly_samples = sorted(
            {
                sample_id
                for rule in qualifying
                for sample_id in rule["failedSampleIds"]
                if sample_id in locatable_samples
            }
        )

        # One reference sequence per downstream field, in the same
        # ascending full-name order as ``downstreamFields``: each is the
        # shortest chain of {dataset, field} refs from the seed to that
        # field, ties broken lexicographically.
        paths = [
            [
                {"dataset": node[0], "field": node[1]}
                for node in shortest[field_id]
            ]
            for field_id in downstream_names
        ]

        impacts.append(
            {
                "seed": {"dataset": seed[0], "field": seed[1]},
                "downstreamFields": [
                    {"dataset": field_id[0], "field": field_id[1]}
                    for field_id in downstream_names
                ],
                "affectedRules": affected_rule_ids,
                "anomalySamples": anomaly_samples,
                "paths": paths,
            }
        )

    return {
        "status": "ok",
        "impacts": impacts,
        "unresolvedReferences": unresolved,
    }
