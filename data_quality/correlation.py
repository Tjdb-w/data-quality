"""Cross-rule correlation of anomalous samples over a field lineage graph.

The public entry points are :func:`correlate_violations` and
:func:`correlate_linked_violations`. They take a batch of data quality check
results (each carrying a stable sample id) plus a lineage graph of
dataset/field nodes joined by typed upstream/downstream edges.

``correlate_violations`` groups the violation results that share a sample and
were triggered by the same field or by lineage-adjacent fields into one
correlation event per connected group.

``correlate_linked_violations`` additionally takes an undirected
``sample_links`` graph that relates samples, potentially across datasets. It
builds sample references from each result's ``dataset_id`` and
``sample_id``, takes the undirected transitive closure of the links so each
connected component of samples becomes one association group, and emits one
correlation event per lineage-connected cluster of violated fields inside
that group.

Malformed check results, lineage graphs or sample links raise
:class:`InvalidCorrelationInputError` /
:class:`InvalidLinkedCorrelationInputError` (both :class:`ValueError`); check
results referencing datasets or fields not declared in the lineage graph
raise :class:`UnknownCorrelationReferenceError`, and sample-link endpoints
that do not match a result raise
:class:`UnknownLinkedCorrelationReferenceError` (both :class:`LookupError`).
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Set, Tuple

__all__ = [
    "correlate_violations",
    "correlate_linked_violations",
    "InvalidCorrelationInputError",
    "UnknownCorrelationReferenceError",
    "InvalidLinkedCorrelationInputError",
    "UnknownLinkedCorrelationReferenceError",
]

EDGE_TYPES = ("upstream", "downstream")

_RESULT_KEYS = frozenset(
    {"rule_id", "dataset_id", "field_id", "sample_id", "violated", "value"}
)
_LINEAGE_KEYS = frozenset({"datasets", "fields", "edges"})
_EDGE_KEYS = frozenset({"source", "target", "type"})
_ENDPOINT_KEYS = frozenset({"dataset", "field"})
_LINK_KEYS = frozenset({"left", "right"})
_LINK_ENDPOINT_KEYS = frozenset({"dataset_id", "sample_id"})

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]

# A sample reference is identified by its (dataset, sample) pair.
SampleRef = Tuple[str, str]


class InvalidCorrelationInputError(ValueError):
    """The check results or the lineage graph are malformed."""

    code = "INVALID_CORRELATION_INPUT"


class UnknownCorrelationReferenceError(LookupError):
    """A check result references an undeclared dataset or field."""

    code = "UNKNOWN_CORRELATION_REFERENCE"


class InvalidLinkedCorrelationInputError(ValueError):
    """The sample links are malformed."""

    code = "INVALID_LINKED_CORRELATION_INPUT"


class UnknownLinkedCorrelationReferenceError(LookupError):
    """A sample-link endpoint does not match any check result."""

    code = "UNKNOWN_LINKED_CORRELATION_REFERENCE"


def _input_error(message: str) -> InvalidCorrelationInputError:
    return InvalidCorrelationInputError(message)


def _linked_input_error(
    message: str,
) -> InvalidLinkedCorrelationInputError:
    return InvalidLinkedCorrelationInputError(message)


def _json_equal(left: Any, right: Any) -> bool:
    """JSON value equality.

    Unlike Python ``==`` this never considers booleans equal to numbers
    (JSON ``true`` is not ``1``) and compares containers structurally.
    """
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


def _check_keys(obj: Dict[str, Any], expected: frozenset, where: str) -> None:
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise _input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise _input_error(f"{where} has unsupported keys: {unknown}")


def _validate_endpoint(
    endpoint: Any, declared_fields: Set[FieldId], where: str
) -> FieldId:
    if not isinstance(endpoint, dict):
        raise _input_error(f"{where} must be an object")
    _check_keys(endpoint, _ENDPOINT_KEYS, where)
    dataset = endpoint["dataset"]
    field = endpoint["field"]
    if not isinstance(dataset, str) or not dataset:
        raise _input_error(f"{where}.dataset must be a non-empty string")
    if not isinstance(field, str) or not field:
        raise _input_error(f"{where}.field must be a non-empty string")
    if (dataset, field) not in declared_fields:
        raise _input_error(f"{where} is not a declared field: {endpoint!r}")
    return (dataset, field)


def _validate_lineage(
    lineage: Any,
) -> Tuple[Set[str], Set[FieldId], Set[Tuple[FieldId, FieldId]]]:
    """Validate the lineage graph.

    Returns the declared dataset ids, the declared ``(dataset, field)``
    field ids and the canonical edges as ``(upstream_field,
    downstream_field)`` pairs.
    """
    if not isinstance(lineage, dict):
        raise _input_error("lineage must be an object")
    _check_keys(lineage, _LINEAGE_KEYS, "lineage")

    datasets = lineage["datasets"]
    if not isinstance(datasets, list):
        raise _input_error("lineage.datasets must be a list of dataset ids")
    declared_datasets: Set[str] = set()
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, str) or not dataset:
            raise _input_error(
                f"lineage.datasets[{index}] must be a non-empty string"
            )
        if dataset in declared_datasets:
            raise _input_error(f"duplicate dataset id: {dataset!r}")
        declared_datasets.add(dataset)

    fields = lineage["fields"]
    if not isinstance(fields, dict):
        raise _input_error(
            "lineage.fields must be an object mapping dataset ids to "
            "field id lists"
        )
    declared_fields: Set[FieldId] = set()
    for dataset, columns in fields.items():
        if not isinstance(dataset, str) or not dataset:
            raise _input_error(
                f"lineage.fields key must be a non-empty dataset id "
                f"string: {dataset!r}"
            )
        if dataset not in declared_datasets:
            raise _input_error(
                f"lineage.fields declares fields for an undeclared "
                f"dataset: {dataset!r}"
            )
        if not isinstance(columns, list):
            raise _input_error(
                f"lineage.fields[{dataset!r}] must be a list of field ids"
            )
        seen_columns: Set[str] = set()
        for index, column in enumerate(columns):
            if not isinstance(column, str) or not column:
                raise _input_error(
                    f"lineage.fields[{dataset!r}][{index}] must be a "
                    "non-empty field id string"
                )
            if column in seen_columns:
                raise _input_error(
                    f"duplicate field id in dataset {dataset!r}: {column!r}"
                )
            seen_columns.add(column)
            declared_fields.add((dataset, column))

    edges = lineage["edges"]
    if not isinstance(edges, list):
        raise _input_error("lineage.edges must be a list")
    canonical: Set[Tuple[FieldId, FieldId]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise _input_error(f"lineage.edges[{index}] must be an object")
        _check_keys(edge, _EDGE_KEYS, f"lineage.edges[{index}]")

        edge_type = edge["type"]
        if edge_type not in EDGE_TYPES:
            raise _input_error(
                f"lineage.edges[{index}].type must be one of "
                f"{list(EDGE_TYPES)}, got {edge_type!r}"
            )

        source = _validate_endpoint(
            edge["source"], declared_fields, f"lineage.edges[{index}].source"
        )
        target = _validate_endpoint(
            edge["target"], declared_fields, f"lineage.edges[{index}].target"
        )

        # Canonical form is (upstream field, downstream field): an
        # "upstream" edge declares its target upstream of its source, a
        # "downstream" edge declares its target downstream of its source.
        if edge_type == "upstream":
            pair = (target, source)
        else:
            pair = (source, target)
        if pair in canonical:
            # Identical restatement of an already known edge: keep one.
            continue
        if (pair[1], pair[0]) in canonical:
            raise _input_error(
                f"contradictory edges between {pair[1]!r} and {pair[0]!r}"
            )
        canonical.add(pair)

    return declared_datasets, declared_fields, canonical


def _same_content(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (
        left["dataset_id"] == right["dataset_id"]
        and left["field_id"] == right["field_id"]
        and left["violated"] == right["violated"]
        and _json_equal(left["value"], right["value"])
    )


def _validate_results(results: Any) -> List[Dict[str, Any]]:
    if not isinstance(results, list):
        raise _input_error("results must be a list")

    deduped: List[Dict[str, Any]] = []
    by_rule_sample: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise _input_error(f"results[{index}] must be an object")
        _check_keys(result, _RESULT_KEYS, f"results[{index}]")

        for key in ("rule_id", "dataset_id", "field_id", "sample_id"):
            if not isinstance(result[key], str) or not result[key]:
                raise _input_error(
                    f"results[{index}].{key} must be a non-empty string"
                )
        if not isinstance(result["violated"], bool):
            raise _input_error(f"results[{index}].violated must be a boolean")

        dedup_key = (result["rule_id"], result["sample_id"])
        existing = by_rule_sample.get(dedup_key)
        if existing is None:
            by_rule_sample[dedup_key] = result
            deduped.append(result)
        elif not _same_content(existing, result):
            raise _input_error(
                f"conflicting results for rule {result['rule_id']!r} on "
                f"sample {result['sample_id']!r}"
            )
    return deduped


def _check_references(
    results: List[Dict[str, Any]],
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
) -> None:
    for result in results:
        dataset = result["dataset_id"]
        if dataset not in declared_datasets:
            raise UnknownCorrelationReferenceError(
                f"unknown dataset id: {dataset!r}"
            )
        if (dataset, result["field_id"]) not in declared_fields:
            raise UnknownCorrelationReferenceError(
                f"unknown field: {dataset!r}.{result['field_id']}"
            )


def _field_ref(field: FieldId) -> Dict[str, str]:
    return {"dataset": field[0], "field": field[1]}


def _sample_ref(ref: SampleRef) -> Dict[str, str]:
    return {"dataset_id": ref[0], "sample_id": ref[1]}


def _direct_lineage_neighbours(
    edges: Set[Tuple[FieldId, FieldId]],
) -> Tuple[Dict[FieldId, Set[FieldId]], Dict[FieldId, Set[FieldId]]]:
    """Build the direct upstream/downstream adjacency of canonical edges."""
    upstream_of: Dict[FieldId, Set[FieldId]] = {}
    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)
        upstream_of.setdefault(downstream, set()).add(upstream)
    return upstream_of, downstream_of


def _lineage_field_components(
    violated_fields: Set[FieldId], edges: Set[Tuple[FieldId, FieldId]]
) -> List[Set[FieldId]]:
    """Connected components of violated fields joined by lineage edges.

    Two violated fields are adjacent when a single valid lineage edge
    connects them (in either direction). Fields that merely share a
    dataset without an edge stay in separate components. Traversal order
    is deterministic so equal inputs always compare equal.
    """
    neighbours: Dict[FieldId, Set[FieldId]] = {
        field: set() for field in violated_fields
    }
    for upstream, downstream in edges:
        if upstream in violated_fields and downstream in violated_fields:
            neighbours[upstream].add(downstream)
            neighbours[downstream].add(upstream)

    components: List[Set[FieldId]] = []
    seen: Set[FieldId] = set()
    for seed in sorted(violated_fields):
        if seed in seen:
            continue
        component: Set[FieldId] = set()
        stack = [seed]
        seen.add(seed)
        while stack:
            current = stack.pop()
            component.add(current)
            for nxt in sorted(neighbours[current]):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(component)
    return components


def _validate_link_endpoint(endpoint: Any, where: str) -> SampleRef:
    if not isinstance(endpoint, dict):
        raise _linked_input_error(f"{where} must be an object")
    keys = set(endpoint)
    if keys != _LINK_ENDPOINT_KEYS:
        missing = sorted(_LINK_ENDPOINT_KEYS - keys)
        if missing:
            raise _linked_input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - _LINK_ENDPOINT_KEYS)
        raise _linked_input_error(f"{where} has unsupported keys: {unknown}")
    dataset = endpoint["dataset_id"]
    sample = endpoint["sample_id"]
    if not isinstance(dataset, str) or not dataset:
        raise _linked_input_error(
            f"{where}.dataset_id must be a non-empty string"
        )
    if not isinstance(sample, str) or not sample:
        raise _linked_input_error(
            f"{where}.sample_id must be a non-empty string"
        )
    return (dataset, sample)


def _validate_sample_link_structure(
    sample_links: Any,
) -> List[Tuple[SampleRef, SampleRef]]:
    """Validate the shape of the undirected sample link graph.

    Returns the links as ``(left_ref, right_ref)`` pairs. Structural
    problems (not a list, missing/extra keys, empty identifiers), self
    links and duplicate (including reversed) relationships raise
    :class:`InvalidLinkedCorrelationInputError`. Endpoint existence is
    checked separately by :func:`_validate_sample_links`.
    """
    if not isinstance(sample_links, list):
        raise _linked_input_error("sample_links must be a list")

    pairs: List[Tuple[SampleRef, SampleRef]] = []
    for index, link in enumerate(sample_links):
        if not isinstance(link, dict):
            raise _linked_input_error(
                f"sample_links[{index}] must be an object"
            )
        keys = set(link)
        if keys != _LINK_KEYS:
            missing = sorted(_LINK_KEYS - keys)
            if missing:
                raise _linked_input_error(
                    f"sample_links[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _LINK_KEYS)
            raise _linked_input_error(
                f"sample_links[{index}] has unsupported keys: {unknown}"
            )
        left = _validate_link_endpoint(
            link["left"], f"sample_links[{index}].left"
        )
        right = _validate_link_endpoint(
            link["right"], f"sample_links[{index}].right"
        )
        pairs.append((left, right))

    # Self links and duplicate relationships are structural errors.
    seen_relations: Set[FrozenSet[SampleRef]] = set()
    for left, right in pairs:
        if left == right:
            raise _linked_input_error(
                f"sample link connects a sample to itself: "
                f"{_sample_ref(left)!r}"
            )
        relation = frozenset((left, right))
        if relation in seen_relations:
            raise _linked_input_error(
                f"duplicate sample link between {_sample_ref(left)!r} and "
                f"{_sample_ref(right)!r}"
            )
        seen_relations.add(relation)

    return pairs


def _check_sample_link_references(
    pairs: List[Tuple[SampleRef, SampleRef]],
    records: List[Dict[str, Any]],
) -> None:
    """Reject link endpoints that do not match a result."""
    known_refs: Set[SampleRef] = {
        (record["dataset_id"], record["sample_id"]) for record in records
    }
    for left, right in pairs:
        for endpoint in (left, right):
            if endpoint not in known_refs:
                raise UnknownLinkedCorrelationReferenceError(
                    f"sample link endpoint has no matching result: "
                    f"{_sample_ref(endpoint)!r}"
                )


def _validate_sample_links(
    sample_links: Any, records: List[Dict[str, Any]]
) -> List[Tuple[SampleRef, SampleRef]]:
    """Validate the undirected sample link graph.

    Returns the links as ``(left_ref, right_ref)`` pairs. Structural
    problems (not a list, missing/extra keys, empty identifiers), self
    links and duplicate (including reversed) relationships raise
    :class:`InvalidLinkedCorrelationInputError`; endpoints that do not
    match any result raise
    :class:`UnknownLinkedCorrelationReferenceError`. Structural problems
    are reported before unknown references.
    """
    pairs = _validate_sample_link_structure(sample_links)
    _check_sample_link_references(pairs, records)
    return pairs


def _sample_components(
    records: List[Dict[str, Any]],
    links: List[Tuple[SampleRef, SampleRef]],
) -> List[Set[SampleRef]]:
    """Undirected connected components over every result sample ref."""
    refs: Set[SampleRef] = {
        (record["dataset_id"], record["sample_id"]) for record in records
    }
    neighbours: Dict[SampleRef, Set[SampleRef]] = {ref: set() for ref in refs}
    for left, right in links:
        neighbours[left].add(right)
        neighbours[right].add(left)

    components: List[Set[SampleRef]] = []
    seen: Set[SampleRef] = set()
    for seed in sorted(refs):
        if seed in seen:
            continue
        component: Set[SampleRef] = set()
        stack = [seed]
        seen.add(seed)
        while stack:
            current = stack.pop()
            component.add(current)
            for nxt in sorted(neighbours[current]):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(component)
    return components


def correlate_violations(results: Any, lineage: Any) -> Dict[str, Any]:
    """Group violation results on the same sample into correlation events.

    :param results: list of check result objects, each with exactly the
        keys ``rule_id``, ``dataset_id``, ``field_id``, ``sample_id``
        (non-empty strings), ``violated`` (boolean) and ``value`` (any
        JSON value). The same rule appearing more than once for the same
        sample is kept once; conflicting repetitions are rejected.
    :param lineage: object with exactly the keys ``datasets`` (list of
        distinct non-empty dataset ids), ``fields`` (object mapping
        declared dataset ids to lists of distinct non-empty field ids)
        and ``edges`` (list of ``{"source": ..., "target": ...,
        "type": ...}`` objects whose endpoints are ``{"dataset": ...,
        "field": ...}`` objects declared in ``fields`` and whose type is
        ``"upstream"`` or ``"downstream"``). Identical restatements of an
        edge are kept once; mutually contradictory edges are rejected.
    :returns: ``{"events": [...]}`` where each event is ``{"sample_id",
        "rules", "fields", "upstream_fields", "downstream_fields"}``.
        ``rules`` are the violated rule ids sorted ascending; ``fields``
        are the affected ``{"dataset", "field"}`` objects; the upstream
        and downstream sets hold the direct lineage neighbours of the
        affected fields outside the event itself. All field collections
        are deduplicated and sorted by ``(dataset, field)``; events are
        sorted by sample id. Samples without violations produce no
        events, so an all-passing batch yields ``{"events": []}``.
    :raises ValueError: :class:`InvalidCorrelationInputError` on malformed
        results or lineage graphs (empty identifiers, conflicting
        duplicate results, dangling edges, illegal edge types or
        contradictory duplicate edges).
    :raises LookupError: :class:`UnknownCorrelationReferenceError` when a
        check result references a dataset or field not declared in the
        lineage graph.
    """
    declared_datasets, declared_fields, edges = _validate_lineage(lineage)
    records = _validate_results(results)
    _check_references(records, declared_datasets, declared_fields)

    upstream_of, downstream_of = _direct_lineage_neighbours(edges)

    violated_by_sample: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        if record["violated"]:
            violated_by_sample.setdefault(record["sample_id"], []).append(record)

    events: List[Dict[str, Any]] = []
    for sample_id, sample_records in violated_by_sample.items():
        violated_fields = {
            (record["dataset_id"], record["field_id"])
            for record in sample_records
        }

        for component in _lineage_field_components(violated_fields, edges):
            rules = sorted(
                record["rule_id"]
                for record in sample_records
                if (record["dataset_id"], record["field_id"]) in component
            )
            upstream_fields: Set[FieldId] = set()
            downstream_fields: Set[FieldId] = set()
            for field in component:
                upstream_fields |= upstream_of.get(field, set())
                downstream_fields |= downstream_of.get(field, set())
            upstream_fields -= component
            downstream_fields -= component

            events.append(
                {
                    "sample_id": sample_id,
                    "rules": rules,
                    "fields": [_field_ref(field) for field in sorted(component)],
                    "upstream_fields": [
                        _field_ref(field) for field in sorted(upstream_fields)
                    ],
                    "downstream_fields": [
                        _field_ref(field) for field in sorted(downstream_fields)
                    ],
                }
            )

    events.sort(
        key=lambda event: (
            event["sample_id"],
            [(field["dataset"], field["field"]) for field in event["fields"]],
        )
    )
    return {"events": events}


def correlate_linked_violations(
    results: Any, lineage: Any, sample_links: Any
) -> Dict[str, Any]:
    """Group violation results across linked samples into correlation events.

    This is the cross-dataset counterpart of :func:`correlate_violations`:
    ``results`` and ``lineage`` use exactly the same shape and validation,
    while ``sample_links`` declares undirected relationships between the
    samples appearing in ``results``.

    :param results: see :func:`correlate_violations`.
    :param lineage: see :func:`correlate_violations`.
    :param sample_links: list of link objects, each with exactly the keys
        ``left`` and ``right``; each endpoint is an object with exactly
        the keys ``dataset_id`` and ``sample_id`` (non-empty strings).
        Links are undirected, so a link and its left/right reversal are
        the same relationship. Endpoints never carry values and values
        are never compared: matching is by ``dataset_id`` and
        ``sample_id`` only, and every endpoint must match a result.
    :returns: ``{"events": [...]}`` where each event has exactly the keys
        ``sample_refs``, ``rules``, ``fields``, ``upstream_fields`` and
        ``downstream_fields``. Sample references are built from the
        violated results' ``dataset_id``/``sample_id`` and grouped by the
        undirected transitive closure of ``sample_links``; within one
        sample component the violated fields are split into events by the
        same lineage-adjacency semantics as :func:`correlate_violations`.
        ``sample_refs`` are deduplicated and sorted by ``(dataset_id,
        sample_id)``, ``rules`` are deduplicated rule ids sorted
        ascending, ``fields`` are deduplicated ``{"dataset", "field"}``
        references sorted by ``(dataset, field)``, and the upstream /
        downstream sets hold the direct lineage neighbours of the
        affected fields outside the event. Sample components with no
        violated result produce no events, so an all-passing batch yields
        ``{"events": []}``.
    :raises ValueError: :class:`InvalidCorrelationInputError` on malformed
        results or lineage, and
        :class:`InvalidLinkedCorrelationInputError` on malformed
        ``sample_links`` (not a list, missing/extra keys, empty
        identifiers, self links or duplicate relationships).
    :raises LookupError: :class:`UnknownCorrelationReferenceError` when a
        result references an undeclared dataset or field, and
        :class:`UnknownLinkedCorrelationReferenceError` when a sample-link
        endpoint has no matching result.
    """
    declared_datasets, declared_fields, edges = _validate_lineage(lineage)
    records = _validate_results(results)
    _check_references(records, declared_datasets, declared_fields)
    links = _validate_sample_links(sample_links, records)

    upstream_of, downstream_of = _direct_lineage_neighbours(edges)

    violated_records = [record for record in records if record["violated"]]

    events: List[Dict[str, Any]] = []
    for sample_component in _sample_components(records, links):
        group_records = [
            record
            for record in violated_records
            if (record["dataset_id"], record["sample_id"]) in sample_component
        ]
        # A component whose results all pass produces no event.
        if not group_records:
            continue

        violated_fields = {
            (record["dataset_id"], record["field_id"])
            for record in group_records
        }

        for field_component in _lineage_field_components(
            violated_fields, edges
        ):
            component_records = [
                record
                for record in group_records
                if (record["dataset_id"], record["field_id"])
                in field_component
            ]
            refs = {
                (record["dataset_id"], record["sample_id"])
                for record in component_records
            }
            rules = sorted({record["rule_id"] for record in component_records})

            upstream_fields: Set[FieldId] = set()
            downstream_fields: Set[FieldId] = set()
            for field in field_component:
                upstream_fields |= upstream_of.get(field, set())
                downstream_fields |= downstream_of.get(field, set())
            upstream_fields -= field_component
            downstream_fields -= field_component

            events.append(
                {
                    "sample_refs": [
                        _sample_ref(ref) for ref in sorted(refs)
                    ],
                    "rules": rules,
                    "fields": [
                        _field_ref(field)
                        for field in sorted(field_component)
                    ],
                    "upstream_fields": [
                        _field_ref(field) for field in sorted(upstream_fields)
                    ],
                    "downstream_fields": [
                        _field_ref(field) for field in sorted(downstream_fields)
                    ],
                }
            )

    events.sort(
        key=lambda event: (
            [
                (ref["dataset_id"], ref["sample_id"])
                for ref in event["sample_refs"]
            ],
            [
                (field["dataset"], field["field"])
                for field in event["fields"]
            ],
        )
    )
    return {"events": events}
