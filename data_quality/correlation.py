"""Cross-rule correlation of anomalous samples over a field lineage graph.

The public entry point is :func:`correlate_sample_anomalies`. It consumes a
batch of data quality check results (each carrying a rule id, dataset id,
field id, stable sample id, pass/fail flag and violating value) together
with a typed lineage graph of declared dataset/field nodes.

For each sample, violations triggered on the same field or on lineage
adjacent fields (connected by a chain of valid edges) are grouped into one
correlation event. Fields that merely belong to the same dataset without a
lineage edge between them stay in separate events.

Result/record problems raise :class:`InvalidCorrelationInputError`, graph
structure problems raise :class:`InvalidCorrelationGraphError` (both
subclass :class:`ValueError`) and a result that references an undeclared
dataset or field raises :class:`UnknownCorrelationReferenceError` (a
:class:`LookupError`); a conflicting duplicate result raises a plain
:class:`ValueError`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

__all__ = [
    "correlate_sample_anomalies",
    "InvalidCorrelationInputError",
    "InvalidCorrelationGraphError",
    "UnknownCorrelationReferenceError",
]

RESULT_KEYS = frozenset(
    {"rule_id", "dataset_id", "field_id", "sample_id", "is_violation", "violating_value"}
)
FIELD_KEYS = frozenset({"dataset_id", "field_id"})
EDGE_KEYS = frozenset({"source", "target", "type"})
VALID_EDGE_TYPES = frozenset({"upstream", "downstream"})

# A field is identified by its (dataset_id, field_id) pair.
FieldId = Tuple[str, str]
# Directed data flow as an (upstream_field, downstream_field) pair: the
# upstream field feeds the downstream field.
Flow = Tuple[FieldId, FieldId]


class InvalidCorrelationInputError(ValueError):
    """The check-results batch is missing or malformed."""


class InvalidCorrelationGraphError(ValueError):
    """The lineage graph (``nodes``/``edges``) is missing or malformed."""


class UnknownCorrelationReferenceError(LookupError):
    """A check result references a dataset or field not declared in ``nodes``."""


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidCorrelationInputError(
            f"{where} must be a non-empty string"
        )
    return value


def _json_equal(left: Any, right: Any) -> bool:
    """JSON value equality: booleans never equal numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[k], right[k]) for k in left
        )
    return left == right


def _field_ref(obj: Dict[str, Any], where: str, error: type) -> FieldId:
    """Validate a ``{"dataset_id", "field_id"}`` object and return the pair."""
    if not isinstance(obj, dict):
        raise error(f"{where} must be an object")
    keys = set(obj)
    if keys != FIELD_KEYS:
        missing = sorted(FIELD_KEYS - keys)
        if missing:
            raise error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - FIELD_KEYS)
        raise error(f"{where} has unsupported keys: {unknown}")
    if not isinstance(obj["dataset_id"], str) or not obj["dataset_id"]:
        raise error(f"{where}.dataset_id must be a non-empty string")
    if not isinstance(obj["field_id"], str) or not obj["field_id"]:
        raise error(f"{where}.field_id must be a non-empty string")
    return obj["dataset_id"], obj["field_id"]


def _validate_results(results: Any) -> List[Dict[str, Any]]:
    if not isinstance(results, list):
        raise InvalidCorrelationInputError("results must be a list")

    clean: List[Dict[str, Any]] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise InvalidCorrelationInputError(
                f"results[{index}] must be an object"
            )
        keys = set(result)
        if keys != RESULT_KEYS:
            missing = sorted(RESULT_KEYS - keys)
            if missing:
                raise InvalidCorrelationInputError(
                    f"results[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - RESULT_KEYS)
            raise InvalidCorrelationInputError(
                f"results[{index}] has unsupported keys: {unknown}"
            )

        rule_id = _identifier(result["rule_id"], f"results[{index}].rule_id")
        dataset_id = _identifier(
            result["dataset_id"], f"results[{index}].dataset_id"
        )
        field_id = _identifier(
            result["field_id"], f"results[{index}].field_id"
        )
        sample_id = _identifier(
            result["sample_id"], f"results[{index}].sample_id"
        )

        is_violation = result["is_violation"]
        # bool is a subclass of int; only JSON booleans are valid flags.
        if not isinstance(is_violation, bool):
            raise InvalidCorrelationInputError(
                f"results[{index}].is_violation must be a boolean"
            )

        clean.append(
            {
                "rule_id": rule_id,
                "dataset_id": dataset_id,
                "field_id": field_id,
                "sample_id": sample_id,
                "is_violation": is_violation,
                "violating_value": result["violating_value"],
            }
        )
    return clean


def _validate_nodes(nodes: Any) -> Set[FieldId]:
    if not isinstance(nodes, list):
        raise InvalidCorrelationGraphError("nodes must be a list")

    declared: Set[FieldId] = set()
    for index, node in enumerate(nodes):
        pair = _field_ref(node, f"nodes[{index}]", InvalidCorrelationGraphError)
        if pair in declared:
            raise InvalidCorrelationGraphError(
                f"duplicate field node: {node!r}"
            )
        declared.add(pair)
    return declared


def _validate_edges(edges: Any, declared: Set[FieldId]) -> Dict[Tuple[FieldId, FieldId], Flow]:
    """Validate typed edges.

    Returns a mapping from the unordered endpoint pair to the canonical
    directed flow ``(upstream_field, downstream_field)``. An edge of type
    ``downstream`` means ``source`` feeds ``target``; type ``upstream``
    means ``target`` feeds ``source``. An identical repeat is tolerated,
    but a repeat with a contradictory flow is rejected.
    """
    if not isinstance(edges, list):
        raise InvalidCorrelationGraphError("edges must be a list")

    flows: Dict[Tuple[FieldId, FieldId], Flow] = {}

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise InvalidCorrelationGraphError(
                f"edges[{index}] must be an object"
            )
        keys = set(edge)
        if keys != EDGE_KEYS:
            missing = sorted(EDGE_KEYS - keys)
            if missing:
                raise InvalidCorrelationGraphError(
                    f"edges[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - EDGE_KEYS)
            raise InvalidCorrelationGraphError(
                f"edges[{index}] has unsupported keys: {unknown}"
            )

        source = _field_ref(
            edge["source"], f"edges[{index}].source", InvalidCorrelationGraphError
        )
        target = _field_ref(
            edge["target"], f"edges[{index}].target", InvalidCorrelationGraphError
        )
        edge_type = edge["type"]
        if edge_type not in VALID_EDGE_TYPES:
            raise InvalidCorrelationGraphError(
                f"edges[{index}].type must be one of "
                f"{sorted(VALID_EDGE_TYPES)}, got {edge_type!r}"
            )

        # Dangling edge: both endpoints must be declared field nodes.
        if source not in declared:
            raise InvalidCorrelationGraphError(
                f"edges[{index}].source is not a declared node: "
                f"{edge['source']!r}"
            )
        if target not in declared:
            raise InvalidCorrelationGraphError(
                f"edges[{index}].target is not a declared node: "
                f"{edge['target']!r}"
            )

        if edge_type == "downstream":
            flow = (source, target)
        else:
            flow = (target, source)

        pair = tuple(sorted((source, target)))
        previous = flows.get(pair)
        if previous is not None and previous != flow:
            raise InvalidCorrelationGraphError(
                f"contradictory duplicate edge between {source!r} and "
                f"{target!r}"
            )
        flows[pair] = flow

    return flows


def _merge_components(
    fields: Set[FieldId], adjacency: Dict[FieldId, Set[FieldId]]
) -> List[Set[FieldId]]:
    """Partition ``fields`` into connected components.

    Two fields share a component only when they are identical or joined by
    a chain of valid lineage edges. Merely sharing a dataset never merges.
    """
    remaining = set(fields)
    components: List[Set[FieldId]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        queue = [seed]
        while queue:
            current = queue.pop()
            for nxt in adjacency.get(current, ()):
                if nxt in remaining:
                    remaining.remove(nxt)
                    component.add(nxt)
                    queue.append(nxt)
        components.append(component)
    return components


def _field_objects(fields: Set[FieldId]) -> List[Dict[str, str]]:
    return [
        {"dataset_id": dataset_id, "field_id": field_id}
        for dataset_id, field_id in sorted(fields)
    ]


def correlate_sample_anomalies(results: Any, lineage_graph: Any) -> Dict[str, Any]:
    """Group per-sample violations by lineage-adjacent fields.

    Inputs are fully read and validated before any output is produced;
    neither ``results`` nor ``lineage_graph`` is mutated.

    :param results: list of check-result objects, each with non-empty
        string ``rule_id``, ``dataset_id``, ``field_id`` and ``sample_id``,
        a boolean ``is_violation`` and a free-form ``violating_value``
        (``null`` when not applicable). The same ``(sample_id, rule_id)``
        pair may repeat only when every other field agrees, including the
        violating value.
    :param lineage_graph: ``{"nodes": [...], "edges": [...]}`` where nodes
        are ``{"dataset_id", "field_id"}`` objects and edges are
        ``{"source", "target", "type"}`` objects with ``type`` either
        ``"upstream"`` or ``"downstream"``. Both endpoints of every edge
        must be declared nodes; contradictory duplicate edges are rejected.
    :returns: ``{"events": [...]}``. Each event is
        ``{"sample_id", "rule_ids", "fields", "upstream_fields",
        "downstream_fields"}``. ``rule_ids`` are sorted; ``fields`` is the
        sorted set of fields carrying violations in the event;
        ``upstream_fields`` / ``downstream_fields`` are the sorted direct
        lineage neighbours of the event fields (excluding the event
        fields themselves). Events are sorted by ``sample_id``. Samples
        without violations produce no event.
    :raises ValueError: :class:`InvalidCorrelationInputError`,
        :class:`InvalidCorrelationGraphError` or a plain
        :class:`ValueError` for a conflicting duplicate result.
    :raises LookupError: :class:`UnknownCorrelationReferenceError` when a
        result references an undeclared dataset or field.
    """
    clean_results = _validate_results(results)

    if not isinstance(lineage_graph, dict):
        raise InvalidCorrelationGraphError("lineage_graph must be an object")
    if "nodes" not in lineage_graph:
        raise InvalidCorrelationGraphError("lineage_graph is missing 'nodes'")
    if "edges" not in lineage_graph:
        raise InvalidCorrelationGraphError("lineage_graph is missing 'edges'")
    declared = _validate_nodes(lineage_graph["nodes"])
    flows = _validate_edges(lineage_graph["edges"], declared)

    # Undirected adjacency: an edge connects its two endpoints regardless
    # of its declared type.
    adjacency: Dict[FieldId, Set[FieldId]] = {}
    for left, right in flows:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)

    # Directed lookup keyed from either endpoint orientation. Self-loops
    # map only once.
    directed: Dict[Tuple[FieldId, FieldId], Flow] = {}
    for (left, right), flow in flows.items():
        if left == right:
            directed[(left, right)] = flow
        else:
            directed[(left, right)] = flow
            directed[(right, left)] = flow

    # Deduplicate identical (sample, rule) results; reject conflicts.
    # Track each violating rule's field per sample.
    canonical: Dict[Tuple[str, str], Dict[str, Any]] = {}
    violations_by_sample: Dict[str, Dict[str, FieldId]] = {}

    for result in clean_results:
        key = (result["sample_id"], result["rule_id"])
        previous = canonical.get(key)
        if previous is not None:
            if (
                previous["dataset_id"] != result["dataset_id"]
                or previous["field_id"] != result["field_id"]
                or previous["is_violation"] != result["is_violation"]
                or not _json_equal(
                    previous["violating_value"], result["violating_value"]
                )
            ):
                raise ValueError(
                    f"conflicting duplicate result for sample "
                    f"{result['sample_id']!r} and rule {result['rule_id']!r}"
                )
            continue
        canonical[key] = result

        field = (result["dataset_id"], result["field_id"])
        # Every referenced dataset/field must be declared.
        if field not in declared:
            raise UnknownCorrelationReferenceError(
                f"result references an undeclared field: "
                f"{result['dataset_id']!r}.{result['field_id']!r}"
            )

        if not result["is_violation"]:
            continue

        violations_by_sample.setdefault(result["sample_id"], {})[
            result["rule_id"]
        ] = field

    events: List[Dict[str, Any]] = []
    for sample_id in sorted(violations_by_sample):
        rule_fields = violations_by_sample[sample_id]
        for component in _merge_components(set(rule_fields.values()), adjacency):
            rule_ids = sorted(
                rule_id
                for rule_id, field in rule_fields.items()
                if field in component
            )

            upstream: Set[FieldId] = set()
            downstream: Set[FieldId] = set()
            for inside in component:
                for other in adjacency.get(inside, ()):
                    if other in component:
                        continue
                    up_field, down_field = directed[(inside, other)]
                    if inside == up_field:
                        # inside feeds other: other is directly downstream.
                        downstream.add(other)
                    else:
                        # other feeds inside: other is directly upstream.
                        upstream.add(other)

            events.append(
                {
                    "sample_id": sample_id,
                    "rule_ids": rule_ids,
                    "fields": _field_objects(component),
                    "upstream_fields": _field_objects(upstream),
                    "downstream_fields": _field_objects(downstream),
                }
            )

    events.sort(key=lambda event: event["sample_id"])
    return {"events": events}
