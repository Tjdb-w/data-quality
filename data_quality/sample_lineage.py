"""Sample-level upstream/downstream lineage tracing.

The public entry point is :func:`trace_sample_lineage`. ``samples`` declares
the known samples as ``dataset_id``/``sample_id`` pairs; ``fields`` and
``edges`` reuse the field-level semantics of
:func:`data_quality.trace_field_lineage`, with every edge additionally
carrying ``witnesses``. A field edge runs from its ``source`` field to its
``target`` field and every witness's ``(table, column)`` must match one of
those two endpoint pairs: a sample witnessing the source field is connected
downstream to the samples witnessing the target field (and upstream the
other way round). ``sample_links`` is an undirected graph between sample
references and may be crossed in either direction on both sides.

Walking from one sample to another costs exactly one relationship hop,
whether it crosses a field edge or a sample link.

Structure problems (including malformed witness references) raise
:class:`InvalidSampleLineageInputError`, bad query arguments raise
:class:`InvalidSampleLineageQueryError`, a target that is not declared in
``samples`` raises :class:`UnknownSampleLineageTargetError` and a
``sample_links`` endpoint that is not declared in ``samples`` raises
:class:`UnknownSampleLineageReferenceError`.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

__all__ = [
    "trace_sample_lineage",
    "InvalidSampleLineageInputError",
    "InvalidSampleLineageQueryError",
    "UnknownSampleLineageTargetError",
    "UnknownSampleLineageReferenceError",
]

VALID_DIRECTIONS = ("upstream", "downstream", "both")
_SAMPLE_KEYS = frozenset({"dataset_id", "sample_id"})
_FIELD_KEYS = frozenset({"table", "column"})
_EDGE_KEYS = frozenset({"source", "target", "witnesses"})
_WITNESS_KEYS = frozenset(
    {"dataset_id", "sample_id", "table", "column"}
)
_LINK_KEYS = frozenset({"left", "right"})

# A sample is identified by its (dataset_id, sample_id) pair.
SampleId = Tuple[str, str]
# A field is identified by its (table, column) pair.
FieldId = Tuple[str, str]


class InvalidSampleLineageInputError(ValueError):
    """The sample lineage input (``samples``/``fields``/``edges``/
    ``sample_links``) is missing or malformed."""


class InvalidSampleLineageQueryError(ValueError):
    """The query arguments (``target``/``direction``/``max_depth``) are bad."""


class UnknownSampleLineageTargetError(LookupError):
    """The queried ``target`` is not declared among ``samples``."""


class UnknownSampleLineageReferenceError(LookupError):
    """A ``sample_links`` endpoint is not declared among ``samples``."""


def _sample_ref(sample: SampleId) -> Dict[str, str]:
    return {"dataset_id": sample[0], "sample_id": sample[1]}


def _validate_samples(samples: Any) -> Set[SampleId]:
    if not isinstance(samples, list):
        raise InvalidSampleLineageInputError("samples must be a list")

    declared: Set[SampleId] = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise InvalidSampleLineageInputError(
                f"samples[{index}] must be an object"
            )
        keys = set(sample)
        if keys != _SAMPLE_KEYS:
            missing = sorted(_SAMPLE_KEYS - keys)
            if missing:
                raise InvalidSampleLineageInputError(
                    f"samples[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _SAMPLE_KEYS)
            raise InvalidSampleLineageInputError(
                f"samples[{index}] has unsupported keys: {unknown}"
            )
        dataset = sample["dataset_id"]
        sample_key = sample["sample_id"]
        if not isinstance(dataset, str) or not dataset:
            raise InvalidSampleLineageInputError(
                f"samples[{index}].dataset_id must be a non-empty string"
            )
        if not isinstance(sample_key, str) or not sample_key:
            raise InvalidSampleLineageInputError(
                f"samples[{index}].sample_id must be a non-empty string"
            )
        ref = (dataset, sample_key)
        if ref in declared:
            raise InvalidSampleLineageInputError(
                f"duplicate sample: {sample!r}"
            )
        declared.add(ref)
    return declared


def _validate_fields(fields: Any) -> Set[FieldId]:
    if not isinstance(fields, dict):
        raise InvalidSampleLineageInputError(
            "fields must be an object mapping table ids to field id lists"
        )

    declared: Set[FieldId] = set()
    for table, columns in fields.items():
        if not isinstance(table, str) or not table:
            raise InvalidSampleLineageInputError(
                f"fields key must be a non-empty table id string: {table!r}"
            )
        if not isinstance(columns, list):
            raise InvalidSampleLineageInputError(
                f"fields[{table!r}] must be a list of field ids"
            )
        seen_columns: Set[str] = set()
        for index, column in enumerate(columns):
            if not isinstance(column, str) or not column:
                raise InvalidSampleLineageInputError(
                    f"fields[{table!r}][{index}] must be a non-empty "
                    "field id string"
                )
            if column in seen_columns:
                raise InvalidSampleLineageInputError(
                    f"duplicate field id in table {table!r}: {column!r}"
                )
            seen_columns.add(column)
            declared.add((table, column))
    return declared


def _check_field_endpoint(
    endpoint: Any, declared: Set[FieldId], where: str
) -> FieldId:
    if not isinstance(endpoint, dict):
        raise InvalidSampleLineageInputError(f"{where} must be an object")
    keys = set(endpoint)
    if keys != _FIELD_KEYS:
        missing = sorted(_FIELD_KEYS - keys)
        if missing:
            raise InvalidSampleLineageInputError(
                f"{where} is missing keys: {missing}"
            )
        unknown = sorted(keys - _FIELD_KEYS)
        raise InvalidSampleLineageInputError(
            f"{where} has unsupported keys: {unknown}"
        )
    table = endpoint["table"]
    column = endpoint["column"]
    if not isinstance(table, str) or not table:
        raise InvalidSampleLineageInputError(
            f"{where}.table must be a non-empty string"
        )
    if not isinstance(column, str) or not column:
        raise InvalidSampleLineageInputError(
            f"{where}.column must be a non-empty string"
        )
    field = (table, column)
    if field not in declared:
        raise InvalidSampleLineageInputError(
            f"{where} is not a declared field: {endpoint!r}"
        )
    return field


def _validate_witnesses(
    witnesses: Any,
    index: int,
    declared_samples: Set[SampleId],
    declared_fields: Set[FieldId],
    source: FieldId,
    target: FieldId,
) -> List[Dict[str, Any]]:
    if not isinstance(witnesses, list):
        raise InvalidSampleLineageInputError(
            f"edges[{index}].witnesses must be a list"
        )

    endpoint_fields = {source, target}
    seen: Set[Tuple[SampleId, FieldId]] = set()
    result: List[Dict[str, Any]] = []
    for witness_index, witness in enumerate(witnesses):
        where = f"edges[{index}].witnesses[{witness_index}]"
        if not isinstance(witness, dict):
            raise InvalidSampleLineageInputError(f"{where} must be an object")
        keys = set(witness)
        if keys != _WITNESS_KEYS:
            missing = sorted(_WITNESS_KEYS - keys)
            if missing:
                raise InvalidSampleLineageInputError(
                    f"{where} is missing keys: {missing}"
                )
            unknown = sorted(keys - _WITNESS_KEYS)
            raise InvalidSampleLineageInputError(
                f"{where} has unsupported keys: {unknown}"
            )
        for key in ("dataset_id", "sample_id", "table", "column"):
            value = witness[key]
            if not isinstance(value, str) or not value:
                raise InvalidSampleLineageInputError(
                    f"{where}.{key} must be a non-empty string"
                )
        sample = (witness["dataset_id"], witness["sample_id"])
        if sample not in declared_samples:
            raise InvalidSampleLineageInputError(
                f"{where} is not a declared sample: {_sample_ref(sample)!r}"
            )
        field = (witness["table"], witness["column"])
        if field not in declared_fields:
            raise InvalidSampleLineageInputError(
                f"{where} is not a declared field: {witness!r}"
            )
        # Every witness must match the edge's source or target pair.
        if field not in endpoint_fields:
            raise InvalidSampleLineageInputError(
                f"{where} does not match edges[{index}] source/target "
                f"fields: {witness!r}"
            )
        pair = (sample, field)
        if pair in seen:
            raise InvalidSampleLineageInputError(
                f"duplicate witness in edges[{index}]: {witness!r}"
            )
        seen.add(pair)
        result.append({"sample": sample, "field": field})
    return result


def _validate_edges(
    edges: Any,
    declared_samples: Set[SampleId],
    declared_fields: Set[FieldId],
) -> List[Dict[str, Any]]:
    if not isinstance(edges, list):
        raise InvalidSampleLineageInputError("edges must be a list")

    seen_edges: Set[Tuple[FieldId, FieldId]] = set()
    validated: List[Dict[str, Any]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise InvalidSampleLineageInputError(
                f"edges[{index}] must be an object"
            )
        keys = set(edge)
        if keys != _EDGE_KEYS:
            missing = sorted(_EDGE_KEYS - keys)
            if missing:
                raise InvalidSampleLineageInputError(
                    f"edges[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _EDGE_KEYS)
            raise InvalidSampleLineageInputError(
                f"edges[{index}] has unsupported keys: {unknown}"
            )

        source = _check_field_endpoint(
            edge["source"], declared_fields, f"edges[{index}].source"
        )
        target = _check_field_endpoint(
            edge["target"], declared_fields, f"edges[{index}].target"
        )
        witnesses = _validate_witnesses(
            edge["witnesses"],
            index,
            declared_samples,
            declared_fields,
            source,
            target,
        )

        pair = (source, target)
        if pair in seen_edges:
            raise InvalidSampleLineageInputError(
                f"duplicate edge: {edge['source']!r} -> {edge['target']!r}"
            )
        seen_edges.add(pair)
        validated.append(
            {
                "source": source,
                "target": target,
                "source_ref": edge["source"],
                "target_ref": edge["target"],
                "witnesses": witnesses,
            }
        )
    return validated


def _check_link_endpoint(endpoint: Any, where: str) -> SampleId:
    if not isinstance(endpoint, dict):
        raise InvalidSampleLineageInputError(f"{where} must be an object")
    keys = set(endpoint)
    if keys != _SAMPLE_KEYS:
        missing = sorted(_SAMPLE_KEYS - keys)
        if missing:
            raise InvalidSampleLineageInputError(
                f"{where} is missing keys: {missing}"
            )
        unknown = sorted(keys - _SAMPLE_KEYS)
        raise InvalidSampleLineageInputError(
            f"{where} has unsupported keys: {unknown}"
        )
    dataset = endpoint["dataset_id"]
    sample = endpoint["sample_id"]
    if not isinstance(dataset, str) or not dataset:
        raise InvalidSampleLineageInputError(
            f"{where}.dataset_id must be a non-empty string"
        )
    if not isinstance(sample, str) or not sample:
        raise InvalidSampleLineageInputError(
            f"{where}.sample_id must be a non-empty string"
        )
    return (dataset, sample)


def _validate_sample_links_structure(
    sample_links: Any,
) -> List[Tuple[SampleId, SampleId]]:
    if not isinstance(sample_links, list):
        raise InvalidSampleLineageInputError("sample_links must be a list")

    pairs: List[Tuple[SampleId, SampleId]] = []
    for index, link in enumerate(sample_links):
        if not isinstance(link, dict):
            raise InvalidSampleLineageInputError(
                f"sample_links[{index}] must be an object"
            )
        keys = set(link)
        if keys != _LINK_KEYS:
            missing = sorted(_LINK_KEYS - keys)
            if missing:
                raise InvalidSampleLineageInputError(
                    f"sample_links[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _LINK_KEYS)
            raise InvalidSampleLineageInputError(
                f"sample_links[{index}] has unsupported keys: {unknown}"
            )
        left = _check_link_endpoint(link["left"], f"sample_links[{index}].left")
        right = _check_link_endpoint(
            link["right"], f"sample_links[{index}].right"
        )
        pairs.append((left, right))

    seen_relations: Set[frozenset] = set()
    for left, right in pairs:
        if left == right:
            raise InvalidSampleLineageInputError(
                f"sample link connects a sample to itself: "
                f"{_sample_ref(left)!r}"
            )
        relation = frozenset((left, right))
        if relation in seen_relations:
            raise InvalidSampleLineageInputError(
                f"duplicate sample link between {_sample_ref(left)!r} and "
                f"{_sample_ref(right)!r}"
            )
        seen_relations.add(relation)
    return pairs


def _check_link_references(
    pairs: List[Tuple[SampleId, SampleId]], declared_samples: Set[SampleId]
) -> None:
    for left, right in pairs:
        for endpoint in (left, right):
            if endpoint not in declared_samples:
                raise UnknownSampleLineageReferenceError(
                    "sample link endpoint is not a declared sample: "
                    f"{_sample_ref(endpoint)!r}"
                )


def _validate_query(target: Any, direction: Any, max_depth: Any) -> SampleId:
    if not isinstance(target, dict):
        raise InvalidSampleLineageQueryError(
            "target must be an object with 'dataset_id' and 'sample_id'"
        )
    keys = set(target)
    if keys != _SAMPLE_KEYS:
        missing = sorted(_SAMPLE_KEYS - keys)
        if missing:
            raise InvalidSampleLineageQueryError(
                f"target is missing keys: {missing}"
            )
        unknown = sorted(keys - _SAMPLE_KEYS)
        raise InvalidSampleLineageQueryError(
            f"target has unsupported keys: {unknown}"
        )
    if not isinstance(target["dataset_id"], str) or not target["dataset_id"]:
        raise InvalidSampleLineageQueryError(
            "target.dataset_id must be a non-empty string"
        )
    if not isinstance(target["sample_id"], str) or not target["sample_id"]:
        raise InvalidSampleLineageQueryError(
            "target.sample_id must be a non-empty string"
        )
    if direction not in VALID_DIRECTIONS:
        raise InvalidSampleLineageQueryError(
            f"direction must be one of {list(VALID_DIRECTIONS)}, "
            f"got {direction!r}"
        )
    if max_depth is not None:
        # bool is a subclass of int, but JSON true/false are not depths.
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise InvalidSampleLineageQueryError(
                "max_depth must be null or an integer >= 0"
            )
        if max_depth < 0:
            raise InvalidSampleLineageQueryError(
                f"max_depth must be >= 0, got {max_depth}"
            )
    return (target["dataset_id"], target["sample_id"])


def _walk_side(
    start: SampleId,
    edges: List[Dict[str, Any]],
    links: List[Tuple[SampleId, SampleId]],
    forward: bool,
    max_depth: Optional[int],
) -> Dict[SampleId, int]:
    """BFS over samples for one side.

    ``forward`` selects downstream (source witnesses -> target witnesses)
    when true and upstream (target witnesses -> source witnesses) when
    false. Sample links are always undirected. First visit wins, so depths
    are shortest relationship-hop depths.
    """
    depths = {start: 0}
    if max_depth == 0:
        return depths

    # Samples witnessing each edge's source field and target field.
    source_witnesses: List[Set[SampleId]] = []
    target_witnesses: List[Set[SampleId]] = []
    for edge in edges:
        sources: Set[SampleId] = set()
        targets: Set[SampleId] = set()
        for witness in edge["witnesses"]:
            if witness["field"] == edge["source"]:
                sources.add(witness["sample"])
            if witness["field"] == edge["target"]:
                targets.add(witness["sample"])
        source_witnesses.append(sources)
        target_witnesses.append(targets)

    link_neighbours: Dict[SampleId, Set[SampleId]] = {}
    for left, right in links:
        link_neighbours.setdefault(left, set()).add(right)
        link_neighbours.setdefault(right, set()).add(left)

    queue = deque([(start, 0)])
    while queue:
        sample, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        next_depth = depth + 1

        for other in link_neighbours.get(sample, ()):
            if other not in depths:
                depths[other] = next_depth
                queue.append((other, next_depth))

        for sources, targets in zip(source_witnesses, target_witnesses):
            current_side = sources if forward else targets
            next_side = targets if forward else sources
            if sample not in current_side:
                continue
            for other in next_side:
                if other not in depths:
                    depths[other] = next_depth
                    queue.append((other, next_depth))
    return depths


def _witness_object(witness: Dict[str, Any]) -> Dict[str, str]:
    sample = witness["sample"]
    field = witness["field"]
    return {
        "dataset_id": sample[0],
        "sample_id": sample[1],
        "table": field[0],
        "column": field[1],
    }


def _side_edges(
    depths: Dict[SampleId, int],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Field edges with both relationship ends reached on this side.

    An edge is included when at least one reached sample witnesses its
    source field and at least one reached sample witnesses its target
    field. The edge keeps its original ``source``/``target`` references and
    full witness list; witnesses are sorted by field reference and then by
    sample reference. Edges are sorted by the reached witness sample
    references and then by their source/target field references.
    """
    side_edges: List[Dict[str, Any]] = []
    for edge in edges:
        reached_sources: Set[SampleId] = set()
        reached_targets: Set[SampleId] = set()
        for witness in edge["witnesses"]:
            sample = witness["sample"]
            if sample not in depths:
                continue
            if witness["field"] == edge["source"]:
                reached_sources.add(sample)
            if witness["field"] == edge["target"]:
                reached_targets.add(sample)
        if not reached_sources or not reached_targets:
            continue

        witnesses = [_witness_object(witness) for witness in edge["witnesses"]]
        witnesses.sort(
            key=lambda item: (
                item["table"],
                item["column"],
                item["dataset_id"],
                item["sample_id"],
            )
        )
        sample_sort_key = tuple(
            sorted({witness["sample"] for witness in edge["witnesses"]})
        )
        side_edges.append(
            {
                "source": edge["source_ref"],
                "target": edge["target_ref"],
                "witnesses": witnesses,
                "_sort_key": (
                    sample_sort_key,
                    edge["source"],
                    edge["target"],
                ),
            }
        )

    side_edges.sort(key=lambda item: item["_sort_key"])
    for item in side_edges:
        item.pop("_sort_key")
    return side_edges


def _side(
    depths: Optional[Dict[SampleId, int]],
    edges: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if depths is None:
        return {"samples": [], "edges": []}

    side_samples = [
        {"dataset_id": dataset, "sample_id": sample, "depth": depth}
        for (dataset, sample), depth in sorted(
            depths.items(), key=lambda item: (item[1], item[0][0], item[0][1])
        )
    ]
    return {"samples": side_samples, "edges": _side_edges(depths, edges)}


def trace_sample_lineage(
    samples: Any,
    fields: Any,
    edges: Any,
    sample_links: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Trace sample-level upstream/downstream lineage of ``target``.

    :param samples: list of distinct ``{"dataset_id", "sample_id"}`` objects
        with non-empty string values.
    :param fields: object mapping non-empty table ids to lists of distinct
        non-empty field ids, as in :func:`data_quality.trace_field_lineage`.
    :param edges: list of ``{"source", "target", "witnesses"}`` objects;
        ``source``/``target`` are declared ``{"table", "column"}`` fields and
        the edge runs from source to target. ``witnesses`` is a list of
        distinct ``{"dataset_id", "sample_id", "table", "column"}`` objects
        referencing declared samples and fields, with every
        ``(table, column)`` equal to the edge's source or target pair.
        Duplicate edges are rejected; self-loops and cycles are legal.
    :param sample_links: undirected list of ``{"left", "right"}`` links whose
        endpoints are ``{"dataset_id", "sample_id"}`` objects; self links
        and duplicate (including reversed) links are rejected and every
        endpoint must be declared in ``samples``.
    :param target: ``{"dataset_id", "sample_id"}`` sample to trace from;
        depth 0 in every returned side.
    :param direction: ``"upstream"`` (edge witnesses target -> source,
        links both ways), ``"downstream"`` (source -> target, links both
        ways) or ``"both"`` (default).
    :param max_depth: ``None`` for unlimited (default) or an integer ``>= 0``;
        ``0`` returns only ``target``. Booleans are not integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"samples", "edges"}, "downstream": {"samples",
        "edges"}}``. Each populated side lists samples as
        ``{"dataset_id", "sample_id", "depth"}`` sorted by
        ``(depth, dataset_id, sample_id)`` and includes every field edge
        whose source and target fields each have a reached witness, as
        ``{"source", "target", "witnesses"}`` sorted by reached sample
        reference (then field reference), with witnesses sorted by field
        reference (then sample reference). The side not queried is returned
        empty.
    :raises ValueError: :class:`InvalidSampleLineageInputError` or
        :class:`InvalidSampleLineageQueryError`.
    :raises LookupError: :class:`UnknownSampleLineageTargetError` or
        :class:`UnknownSampleLineageReferenceError`.
    """
    declared_samples = _validate_samples(samples)
    declared_fields = _validate_fields(fields)
    validated_edges = _validate_edges(
        edges, declared_samples, declared_fields
    )
    links = _validate_sample_links_structure(sample_links)
    start = _validate_query(target, direction, max_depth)

    if start not in declared_samples:
        raise UnknownSampleLineageTargetError(
            f"target is not declared in samples: {target!r}"
        )
    _check_link_references(links, declared_samples)

    upstream_depths: Optional[Dict[SampleId, int]] = None
    downstream_depths: Optional[Dict[SampleId, int]] = None
    if direction in ("upstream", "both"):
        upstream_depths = _walk_side(
            start, validated_edges, links, False, max_depth
        )
    if direction in ("downstream", "both"):
        downstream_depths = _walk_side(
            start, validated_edges, links, True, max_depth
        )

    return {
        "target": target,
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _side(upstream_depths, validated_edges),
        "downstream": _side(downstream_depths, validated_edges),
    }
