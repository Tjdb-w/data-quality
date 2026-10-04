"""Cross-rule correlation of anomalous samples over a field lineage graph.

The public entry point is :func:`correlate_violations`. It takes a batch of
data quality check results (each carrying a stable sample id) plus a lineage
graph of dataset/field nodes joined by typed upstream/downstream edges, and
groups the violation results that share a sample and were triggered by the
same field or by lineage-adjacent fields into one correlation event per
connected group.

Malformed check results or lineage graphs raise
:class:`InvalidCorrelationInputError` (a :class:`ValueError`); check results
referencing datasets or fields not declared in the lineage graph raise
:class:`UnknownCorrelationReferenceError` (a :class:`LookupError`).

The companion entry point :func:`correlate_linked_violations` additionally
takes explicit cross-dataset sample links and groups violation results over
the transitive closure of those links instead of per sample. Malformed
sample links raise :class:`InvalidLinkedCorrelationInputError`; link
endpoints not present in the check results raise
:class:`UnknownLinkedCorrelationReferenceError`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

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

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]


class InvalidCorrelationInputError(ValueError):
    """The check results or the lineage graph are malformed."""

    code = "INVALID_CORRELATION_INPUT"


class UnknownCorrelationReferenceError(LookupError):
    """A check result references an undeclared dataset or field."""

    code = "UNKNOWN_CORRELATION_REFERENCE"


class InvalidLinkedCorrelationInputError(ValueError):
    """The sample links of a linked correlation are malformed."""

    code = "INVALID_LINKED_CORRELATION_INPUT"


class UnknownLinkedCorrelationReferenceError(LookupError):
    """A sample link endpoint is not present in the check results."""

    code = "UNKNOWN_LINKED_CORRELATION_REFERENCE"


def _input_error(message: str) -> InvalidCorrelationInputError:
    return InvalidCorrelationInputError(message)


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

    upstream_of: Dict[FieldId, Set[FieldId]] = {}
    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)
        upstream_of.setdefault(downstream, set()).add(upstream)

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

        # Two violated fields are adjacent when a single valid lineage
        # edge connects them (in either direction); events are the
        # connected components of this adjacency. Fields that merely
        # share a dataset without an edge stay in separate events.
        neighbours: Dict[FieldId, Set[FieldId]] = {
            field: set() for field in violated_fields
        }
        for upstream, downstream in edges:
            if upstream in violated_fields and downstream in violated_fields:
                neighbours[upstream].add(downstream)
                neighbours[downstream].add(upstream)

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


_LINK_KEYS = frozenset({"left", "right"})
_LINK_ENDPOINT_KEYS = frozenset({"dataset_id", "sample_id"})

# A linked sample is identified by its (dataset_id, sample_id) pair.
SampleRef = Tuple[str, str]


def _linked_input_error(message: str) -> InvalidLinkedCorrelationInputError:
    return InvalidLinkedCorrelationInputError(message)


def _check_linked_keys(obj: Dict[str, Any], expected: frozenset, where: str) -> None:
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise _linked_input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - expected)
        raise _linked_input_error(f"{where} has unsupported keys: {unknown}")


def _validate_link_endpoint(endpoint: Any, where: str) -> SampleRef:
    if not isinstance(endpoint, dict):
        raise _linked_input_error(f"{where} must be an object")
    _check_linked_keys(endpoint, _LINK_ENDPOINT_KEYS, where)
    dataset_id = endpoint["dataset_id"]
    sample_id = endpoint["sample_id"]
    if not isinstance(dataset_id, str) or not dataset_id:
        raise _linked_input_error(
            f"{where}.dataset_id must be a non-empty string"
        )
    if not isinstance(sample_id, str) or not sample_id:
        raise _linked_input_error(
            f"{where}.sample_id must be a non-empty string"
        )
    return (dataset_id, sample_id)


def _validate_sample_links(sample_links: Any) -> List[Tuple[SampleRef, SampleRef]]:
    if not isinstance(sample_links, list):
        raise _linked_input_error("sample_links must be a list")

    links: List[Tuple[SampleRef, SampleRef]] = []
    seen: Set[Tuple[SampleRef, SampleRef]] = set()
    for index, link in enumerate(sample_links):
        where = f"sample_links[{index}]"
        if not isinstance(link, dict):
            raise _linked_input_error(f"{where} must be an object")
        _check_linked_keys(link, _LINK_KEYS, where)

        left = _validate_link_endpoint(link["left"], f"{where}.left")
        right = _validate_link_endpoint(link["right"], f"{where}.right")
        if left == right:
            raise _linked_input_error(
                f"{where} links a sample to itself: {left!r}"
            )
        pair = (left, right) if left < right else (right, left)
        if pair in seen:
            raise _linked_input_error(
                f"duplicate sample link between {pair[0]!r} and {pair[1]!r}"
            )
        seen.add(pair)
        links.append((left, right))
    return links


def _sample_ref(ref: SampleRef) -> Dict[str, str]:
    return {"dataset_id": ref[0], "sample_id": ref[1]}


def correlate_linked_violations(
    results: Any, lineage: Any, sample_links: Any
) -> Dict[str, Any]:
    """Group violation results across linked samples into events.

    ``results`` and ``lineage`` follow exactly the rules of
    :func:`correlate_violations`. ``sample_links`` is a list of
    ``{"left": ..., "right": ...}`` objects whose endpoints are
    ``{"dataset_id": ..., "sample_id": ...}`` objects naming samples that
    appear in ``results``; links are undirected, may not relate a sample to
    itself and may not be stated twice.

    The transitive closure of the links partitions the linked samples into
    components. Within each component the violated results are grouped by
    lineage-adjacent fields exactly as in :func:`correlate_violations`, and
    each such group yields one event ``{"sample_refs", "rules", "fields",
    "upstream_fields", "downstream_fields"}``: ``sample_refs`` are the
    deduplicated ``{"dataset_id", "sample_id"}`` objects of the violated
    results in the group, ``rules`` the deduplicated violated rule ids
    sorted ascending, ``fields`` the affected ``{"dataset", "field"}``
    objects, and the upstream/downstream sets the direct lineage neighbours
    of the affected fields outside the group itself. All collections are
    deduplicated and sorted; events are sorted by sample refs then fields.
    Components without violations produce no events, so an all-passing
    batch yields ``{"events": []}``.

    :raises ValueError: :class:`InvalidCorrelationInputError` on malformed
        results or lineage graphs, :class:`InvalidLinkedCorrelationInputError`
        on malformed sample links (missing or unsupported keys, empty
        identifiers, self links, duplicate relations).
    :raises LookupError: :class:`UnknownCorrelationReferenceError` when a
        check result references an undeclared dataset or field,
        :class:`UnknownLinkedCorrelationReferenceError` when a sample link
        endpoint does not appear in the check results.
    """
    declared_datasets, declared_fields, edges = _validate_lineage(lineage)
    records = _validate_results(results)
    _check_references(records, declared_datasets, declared_fields)
    links = _validate_sample_links(sample_links)

    known_refs: Set[SampleRef] = {
        (record["dataset_id"], record["sample_id"]) for record in records
    }
    for left, right in links:
        for ref in (left, right):
            if ref not in known_refs:
                raise UnknownLinkedCorrelationReferenceError(
                    f"unknown linked sample: "
                    f"dataset_id={ref[0]!r} sample_id={ref[1]!r}"
                )

    upstream_of: Dict[FieldId, Set[FieldId]] = {}
    downstream_of: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        downstream_of.setdefault(upstream, set()).add(downstream)
        upstream_of.setdefault(downstream, set()).add(upstream)

    violated_by_ref: Dict[SampleRef, List[Dict[str, Any]]] = {}
    for record in records:
        if record["violated"]:
            ref = (record["dataset_id"], record["sample_id"])
            violated_by_ref.setdefault(ref, []).append(record)

    # Connected components of the undirected link graph.
    linked_neighbours: Dict[SampleRef, Set[SampleRef]] = {}
    for left, right in links:
        linked_neighbours.setdefault(left, set()).add(right)
        linked_neighbours.setdefault(right, set()).add(left)

    events: List[Dict[str, Any]] = []
    seen_refs: Set[SampleRef] = set()
    for seed in sorted(linked_neighbours):
        if seed in seen_refs:
            continue
        component_refs: Set[SampleRef] = set()
        stack = [seed]
        seen_refs.add(seed)
        while stack:
            current = stack.pop()
            component_refs.add(current)
            for nxt in sorted(linked_neighbours[current]):
                if nxt not in seen_refs:
                    seen_refs.add(nxt)
                    stack.append(nxt)

        component_records = [
            record
            for ref in component_refs
            for record in violated_by_ref.get(ref, [])
        ]
        if not component_records:
            continue

        violated_fields = {
            (record["dataset_id"], record["field_id"])
            for record in component_records
        }

        # Same adjacency semantics as correlate_violations: two violated
        # fields are adjacent when a single valid lineage edge connects
        # them (in either direction).
        neighbours: Dict[FieldId, Set[FieldId]] = {
            field: set() for field in violated_fields
        }
        for upstream, downstream in edges:
            if upstream in violated_fields and downstream in violated_fields:
                neighbours[upstream].add(downstream)
                neighbours[downstream].add(upstream)

        seen_fields: Set[FieldId] = set()
        for field_seed in sorted(violated_fields):
            if field_seed in seen_fields:
                continue
            component: Set[FieldId] = set()
            field_stack = [field_seed]
            seen_fields.add(field_seed)
            while field_stack:
                current = field_stack.pop()
                component.add(current)
                for nxt in sorted(neighbours[current]):
                    if nxt not in seen_fields:
                        seen_fields.add(nxt)
                        field_stack.append(nxt)

            group_records = [
                record
                for record in component_records
                if (record["dataset_id"], record["field_id"]) in component
            ]
            rules = sorted({record["rule_id"] for record in group_records})
            sample_refs = sorted(
                {
                    (record["dataset_id"], record["sample_id"])
                    for record in group_records
                }
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
                    "sample_refs": [_sample_ref(ref) for ref in sample_refs],
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
            [
                (ref["dataset_id"], ref["sample_id"])
                for ref in event["sample_refs"]
            ],
            [(field["dataset"], field["field"]) for field in event["fields"]],
        )
    )
    return {"events": events}
