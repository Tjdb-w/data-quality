"""Field-level data quality impact analysis.

The public entry point is :func:`analyze_field_impacts`. It combines a
field-level lineage graph (``datasets`` + ``lineageEdges``), a batch of
rule validation results (``validationResults``) and a set of located
anomaly samples (``anomalySamples``) and explains, for every seed field
in ``seedFields``, which fields lie downstream of it, which non-passed
rules touch those downstream fields and which anomaly samples can be
located for those rules.

Malformed payloads raise :class:`ImpactInputError` (a
:class:`ValueError`); the analysis never returns partially: every
structural problem is reported before any result is assembled. Dangling
lineage edges do not interrupt the analysis — they are collected into
``unresolvedReferences`` instead of raising.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set, Tuple

__all__ = [
    "analyze_field_impacts",
    "ImpactInputError",
]

VALID_STATUSES = ("passed", "failed", "skipped")

_PAYLOAD_KEYS = frozenset(
    {"datasets", "lineageEdges", "validationResults", "anomalySamples", "seedFields"}
)
_EDGE_KEYS = frozenset(
    {"sourceDataset", "sourceField", "targetDataset", "targetField"}
)
_RULE_KEYS = frozenset({"ruleId", "status", "fields", "failedSampleIds"})
_REFERENCE_KEYS = frozenset({"dataset", "field"})
_SAMPLE_KEYS = frozenset({"dataset", "fieldValues"})

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]


class ImpactInputError(ValueError):
    """The impact analysis payload is missing or malformed."""

    code = "INVALID_IMPACT_INPUT"


def _input_error(message: str) -> ImpactInputError:
    return ImpactInputError(message)


def _check_keys(obj: Dict[str, Any], expected: frozenset, where: str) -> None:
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise _input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise _input_error(f"{where} has unsupported keys: {unknown}")


def _check_identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise _input_error(f"{where} must be a non-empty string")
    return value


def _parse_reference(reference: Any, where: str) -> FieldId:
    if not isinstance(reference, dict):
        raise _input_error(f"{where} must be an object")
    _check_keys(reference, _REFERENCE_KEYS, where)
    dataset = _check_identifier(reference["dataset"], f"{where}.dataset")
    field = _check_identifier(reference["field"], f"{where}.field")
    return (dataset, field)


def _parse_datasets(datasets: Any) -> Dict[str, Set[str]]:
    if not isinstance(datasets, dict):
        raise _input_error(
            "datasets must be an object mapping dataset ids to field id lists"
        )
    declared: Dict[str, Set[str]] = {}
    for dataset, fields in datasets.items():
        _check_identifier(dataset, "datasets key")
        if not isinstance(fields, list):
            raise _input_error(
                f"datasets[{dataset!r}] must be a list of field ids"
            )
        seen: Set[str] = set()
        for index, field in enumerate(fields):
            _check_identifier(field, f"datasets[{dataset!r}][{index}]")
            seen.add(field)
        declared[dataset] = seen
    return declared


def _parse_edges(edges: Any) -> List[Dict[str, str]]:
    if not isinstance(edges, list):
        raise _input_error("lineageEdges must be a list")
    parsed: List[Dict[str, str]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise _input_error(f"lineageEdges[{index}] must be an object")
        _check_keys(edge, _EDGE_KEYS, f"lineageEdges[{index}]")
        parsed.append(
            {
                key: _check_identifier(edge[key], f"lineageEdges[{index}].{key}")
                for key in (
                    "sourceDataset",
                    "sourceField",
                    "targetDataset",
                    "targetField",
                )
            }
        )
    return parsed


def _parse_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _input_error("validationResults must be a list")
    parsed: List[Dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise _input_error(f"validationResults[{index}] must be an object")
        _check_keys(rule, _RULE_KEYS, f"validationResults[{index}]")
        rule_id = _check_identifier(
            rule["ruleId"], f"validationResults[{index}].ruleId"
        )
        status = rule["status"]
        if status not in VALID_STATUSES:
            raise _input_error(
                f"validationResults[{index}].status must be one of "
                f"{list(VALID_STATUSES)}, got {status!r}"
            )
        fields = rule["fields"]
        if not isinstance(fields, list):
            raise _input_error(
                f"validationResults[{index}].fields must be a list"
            )
        references = [
            _parse_reference(
                reference, f"validationResults[{index}].fields[{position}]"
            )
            for position, reference in enumerate(fields)
        ]
        failed = rule["failedSampleIds"]
        if not isinstance(failed, list):
            raise _input_error(
                f"validationResults[{index}].failedSampleIds must be a list"
            )
        for position, sample_id in enumerate(failed):
            _check_identifier(
                sample_id,
                f"validationResults[{index}].failedSampleIds[{position}]",
            )
        parsed.append(
            {
                "ruleId": rule_id,
                "status": status,
                "fields": references,
                "failedSampleIds": list(failed),
            }
        )
    return parsed


def _parse_samples(samples: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(samples, dict):
        raise _input_error("anomalySamples must be an object")
    parsed: Dict[str, Dict[str, Any]] = {}
    for sample_id, sample in samples.items():
        _check_identifier(sample_id, "anomalySamples key")
        if not isinstance(sample, dict):
            raise _input_error(
                f"anomalySamples[{sample_id!r}] must be an object"
            )
        _check_keys(sample, _SAMPLE_KEYS, f"anomalySamples[{sample_id!r}]")
        _check_identifier(
            sample["dataset"], f"anomalySamples[{sample_id!r}].dataset"
        )
        field_values = sample["fieldValues"]
        if not isinstance(field_values, dict):
            raise _input_error(
                f"anomalySamples[{sample_id!r}].fieldValues must be an object"
            )
        for field in field_values:
            _check_identifier(
                field, f"anomalySamples[{sample_id!r}].fieldValues key"
            )
        parsed[sample_id] = sample
    return parsed


def _parse_seeds(seeds: Any) -> List[FieldId]:
    if not isinstance(seeds, list):
        raise _input_error("seedFields must be a list")
    return [
        _parse_reference(seed, f"seedFields[{index}]")
        for index, seed in enumerate(seeds)
    ]


def _full_name(field: FieldId) -> str:
    return f"{field[0]}.{field[1]}"


def _collect_downstream(
    seed: FieldId,
    outgoing: Dict[FieldId, Set[FieldId]],
    incoming: Dict[FieldId, Set[FieldId]],
) -> Dict[FieldId, Tuple[FieldId, ...]]:
    """Shortest downstream reference sequences from ``seed``.

    Returns every field reachable from ``seed`` in at least one step
    (the seed itself only when a self-loop or a cycle leads back to it)
    mapped to the shortest reference sequence from ``seed``; sequences of
    equal length are broken by lexicographic order. Cycles are never
    followed forever: breadth-first depths are shortest, so traversal
    terminates normally.
    """
    depths: Dict[FieldId, int] = {seed: 0}
    queue = deque([seed])
    while queue:
        node = queue.popleft()
        for nxt in outgoing.get(node, ()):
            if nxt not in depths:
                depths[nxt] = depths[node] + 1
                queue.append(nxt)

    downstream: Dict[FieldId, int] = {
        field: depth for field, depth in depths.items() if depth > 0
    }
    # A self-loop or a cycle back to the seed makes the seed its own
    # downstream field; its depth is one past the closest predecessor.
    for pred in incoming.get(seed, ()):
        if pred in depths:
            candidate = depths[pred] + 1
            if seed not in downstream or candidate < downstream[seed]:
                downstream[seed] = candidate

    best: Dict[FieldId, Tuple[FieldId, ...]] = {seed: (seed,)}
    for field in sorted(downstream, key=lambda item: downstream[item]):
        depth = downstream[field]
        candidates: List[Tuple[FieldId, ...]] = []
        for pred in incoming.get(field, ()):
            pred_depth = 0 if pred == seed else downstream.get(pred)
            if pred_depth is not None and pred_depth == depth - 1:
                candidates.append(best[pred] + (field,))
        best[field] = min(candidates)
    return {field: best[field] for field in downstream}


def analyze_field_impacts(payload: Any) -> Dict[str, Any]:
    """Analyze the field-level quality impact of every seed field.

    :param payload: object with exactly the keys ``datasets``,
        ``lineageEdges``, ``validationResults``, ``anomalySamples`` and
        ``seedFields``:

        - ``datasets``: object mapping non-empty dataset ids to lists of
          non-empty field ids.
        - ``lineageEdges``: list of ``{"sourceDataset", "sourceField",
          "targetDataset", "targetField"}`` objects (all non-empty
          strings); edges point from source field to target field. Edges
          touching undeclared fields are dangling: they do not interrupt
          the analysis and are reported in ``unresolvedReferences``.
        - ``validationResults``: list of ``{"ruleId", "status", "fields",
          "failedSampleIds"}`` objects; ``status`` is one of ``"passed"``,
          ``"failed"`` or ``"skipped"`` and ``fields`` is a list of
          ``{"dataset", "field"}`` references.
        - ``anomalySamples``: object mapping sample ids to
          ``{"dataset", "fieldValues"}`` objects.
        - ``seedFields``: list of ``{"dataset", "field"}`` references,
          processed in the given order; duplicates are legal and seeds
          not declared in ``datasets`` yield an empty impact.
    :returns: ``{"status": "ok", "impacts": [...],
        "unresolvedReferences": [...]}``. Each impact is ``{"seed",
        "downstreamFields", "affectedRules", "anomalySamples", "paths"}``:

        - ``downstreamFields``: full names (``dataset.field``) of the
          fields reachable from the seed in at least one step —
          self-loops and cycles back to the seed included — deduplicated
          and sorted ascending.
        - ``affectedRules``: ids of the rules whose ``fields`` touch a
          downstream field and whose ``status`` is not ``"passed"``,
          sorted ascending.
        - ``anomalySamples``: sample ids from the affected rules'
          ``failedSampleIds`` that can be located in ``anomalySamples``,
          sorted ascending.
        - ``paths``: one ``{"field", "path"}`` entry per downstream
          field, sorted by full field name; ``path`` is the shortest
          ``{"dataset", "field"}`` reference sequence from the seed to
          that field (lexicographically smallest on ties).

        ``unresolvedReferences`` holds the dangling edges as their
        original four-key objects, deduplicated and sorted by
        ``(sourceDataset, sourceField, targetDataset, targetField)``.
        An empty ``seedFields`` yields empty ``impacts`` while
        ``unresolvedReferences`` is still reported.
    :raises ImpactInputError: when the payload is not an object, keys are
        missing or unsupported, arrays or references are malformed,
        identifiers are empty or not strings, or a rule ``status`` is not
        one of ``"passed"``, ``"failed"``, ``"skipped"``.
    """
    if not isinstance(payload, dict):
        raise _input_error("payload must be a JSON object")
    _check_keys(payload, _PAYLOAD_KEYS, "payload")

    datasets = _parse_datasets(payload["datasets"])
    edges = _parse_edges(payload["lineageEdges"])
    rules = _parse_rules(payload["validationResults"])
    samples = _parse_samples(payload["anomalySamples"])
    seeds = _parse_seeds(payload["seedFields"])

    declared: Set[FieldId] = {
        (dataset, field) for dataset, fields in datasets.items() for field in fields
    }

    outgoing: Dict[FieldId, Set[FieldId]] = {}
    incoming: Dict[FieldId, Set[FieldId]] = {}
    unresolved: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    for edge in edges:
        source = (edge["sourceDataset"], edge["sourceField"])
        target = (edge["targetDataset"], edge["targetField"])
        if source not in declared or target not in declared:
            key = source + target
            unresolved[key] = {
                "sourceDataset": edge["sourceDataset"],
                "sourceField": edge["sourceField"],
                "targetDataset": edge["targetDataset"],
                "targetField": edge["targetField"],
            }
            continue
        outgoing.setdefault(source, set()).add(target)
        incoming.setdefault(target, set()).add(source)

    impacts: List[Dict[str, Any]] = []
    for seed in seeds:
        if seed in declared:
            paths_by_field = _collect_downstream(seed, outgoing, incoming)
        else:
            # Unknown seeds are treated as having an empty downstream.
            paths_by_field = {}
        downstream_set = set(paths_by_field)

        affected = [
            rule
            for rule in rules
            if rule["status"] != "passed"
            and any(reference in downstream_set for reference in rule["fields"])
        ]
        sample_ids = sorted(
            {
                sample_id
                for rule in affected
                for sample_id in rule["failedSampleIds"]
                if sample_id in samples
            }
        )

        impacts.append(
            {
                "seed": {"dataset": seed[0], "field": seed[1]},
                "downstreamFields": sorted(
                    _full_name(field) for field in downstream_set
                ),
                "affectedRules": sorted({rule["ruleId"] for rule in affected}),
                "anomalySamples": sample_ids,
                "paths": [
                    {
                        "field": _full_name(field),
                        "path": [
                            {"dataset": step[0], "field": step[1]}
                            for step in paths_by_field[field]
                        ],
                    }
                    for field in sorted(downstream_set, key=_full_name)
                ],
            }
        )

    return {
        "status": "ok",
        "impacts": impacts,
        "unresolvedReferences": [unresolved[key] for key in sorted(unresolved)],
    }
