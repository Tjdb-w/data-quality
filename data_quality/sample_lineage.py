"""Upstream/downstream lineage tracing at sample granularity.

The public entry point is :func:`trace_sample_lineage`. ``samples``
declares the known samples, each identified by its ``dataset_id`` /
``sample_id`` pair; ``fields`` and ``edges`` reuse the field-level
semantics of :func:`data_quality.trace_field_lineage`, except that a
sample-level field edge points from a ``source`` sample to a ``target``
sample and additionally carries the ``witnesses`` (``table`` /
``column`` field references) that justify it; ``sample_links`` declares
undirected relationships between samples.

Field edges point from ``source`` to ``target``: upstream traversal
follows them backwards, downstream traversal follows them forwards, while
sample links connect samples in either direction. A field edge is
reported on a side only when both endpoint samples are reached on that
side, and its witnesses are returned sorted by field reference.

Graph structure problems raise :class:`InvalidSampleLineageInputError`,
bad query arguments raise :class:`InvalidSampleLineageQueryError` and a
target that is not declared in ``samples`` raises
:class:`UnknownSampleLineageTargetError` (all three subclass
:class:`ValueError`); a ``sample_links`` endpoint that is not declared in
``samples`` raises :class:`UnknownSampleLineageReferenceError`
(subclass of :class:`LookupError`).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

__all__ = [
    "trace_sample_lineage",
    "InvalidSampleLineageInputError",
    "InvalidSampleLineageQueryError",
    "UnknownSampleLineageTargetError",
    "UnknownSampleLineageReferenceError",
]

VALID_DIRECTIONS = ("upstream", "downstream", "both")
_EDGE_KEYS = frozenset({"source", "target", "witnesses"})
_FIELD_KEYS = frozenset({"table", "column"})
_SAMPLE_KEYS = frozenset({"dataset_id", "sample_id"})
_LINK_KEYS = frozenset({"left", "right"})

# A field is identified by its (table, column) pair.
FieldId = Tuple[str, str]
# A sample is identified by its (dataset_id, sample_id) pair.
SampleId = Tuple[str, str]


class InvalidSampleLineageInputError(ValueError):
    """The sample lineage input (``samples``/``fields``/``edges``/
    ``sample_links``) is missing or malformed."""


class InvalidSampleLineageQueryError(ValueError):
    """The query arguments (``target``/``direction``/``max_depth``) are bad."""


class UnknownSampleLineageTargetError(ValueError):
    """The queried ``target`` is not declared among ``samples``."""


class UnknownSampleLineageReferenceError(LookupError):
    """A ``sample_links`` endpoint is not declared among ``samples``."""


def _sample_id(ref: Dict[str, Any]) -> SampleId:
    return (ref["dataset_id"], ref["sample_id"])


def _field_ref(field: FieldId) -> Dict[str, str]:
    return {"table": field[0], "column": field[1]}


def _sample_ref(sample: SampleId) -> Dict[str, str]:
    return {"dataset_id": sample[0], "sample_id": sample[1]}


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


def _validate_field_ref(ref: Any, declared: Set[FieldId], where: str) -> FieldId:
    if not isinstance(ref, dict):
        raise InvalidSampleLineageInputError(f"{where} must be an object")
    keys = set(ref)
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
    table = ref["table"]
    column = ref["column"]
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
            f"{where} is not a declared field: {ref!r}"
        )
    return field


def _validate_samples(samples: Any) -> Set[SampleId]:
    if not isinstance(samples, list):
        raise InvalidSampleLineageInputError("samples must be a list")

    declared: Set[SampleId] = set()
    for index, sample in enumerate(samples):
        ref = _validate_sample_ref(sample, f"samples[{index}]")
        if ref in declared:
            raise InvalidSampleLineageInputError(
                f"duplicate sample: {sample!r}"
            )
        declared.add(ref)
    return declared


def _validate_sample_ref(ref: Any, where: str) -> SampleId:
    """Validate a ``{dataset_id, sample_id}`` reference's shape only."""
    if not isinstance(ref, dict):
        raise InvalidSampleLineageInputError(f"{where} must be an object")
    keys = set(ref)
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
    dataset = ref["dataset_id"]
    sample_key = ref["sample_id"]
    if not isinstance(dataset, str) or not dataset:
        raise InvalidSampleLineageInputError(
            f"{where}.dataset_id must be a non-empty string"
        )
    if not isinstance(sample_key, str) or not sample_key:
        raise InvalidSampleLineageInputError(
            f"{where}.sample_id must be a non-empty string"
        )
    return (dataset, sample_key)


def _validate_edges(
    edges: Any, declared_fields: Set[FieldId], declared_samples: Set[SampleId]
) -> List[Dict[str, Any]]:
    if not isinstance(edges, list):
        raise InvalidSampleLineageInputError("edges must be a list")

    validated: List[Dict[str, Any]] = []
    seen_edges: Set[Tuple[SampleId, SampleId, Tuple[FieldId, ...]]] = set()
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

        source = _validate_declared_sample(
            edge["source"], declared_samples, f"edges[{index}].source"
        )
        target = _validate_declared_sample(
            edge["target"], declared_samples, f"edges[{index}].target"
        )

        witnesses = edge["witnesses"]
        if not isinstance(witnesses, list):
            raise InvalidSampleLineageInputError(
                f"edges[{index}].witnesses must be a list"
            )
        witness_fields: List[FieldId] = []
        seen_witnesses: Set[FieldId] = set()
        for witness_index, witness in enumerate(witnesses):
            field = _validate_field_ref(
                witness,
                declared_fields,
                f"edges[{index}].witnesses[{witness_index}]",
            )
            if field in seen_witnesses:
                raise InvalidSampleLineageInputError(
                    f"edges[{index}] has a duplicate witness: "
                    f"{_field_ref(field)!r}"
                )
            seen_witnesses.add(field)
            witness_fields.append(field)

        # Edges are identical only when endpoints and witnesses agree;
        # the same sample pair carrying different witnesses is distinct.
        identity = (source, target, tuple(sorted(witness_fields)))
        if identity in seen_edges:
            raise InvalidSampleLineageInputError(
                f"duplicate edge: {edge['source']!r} -> {edge['target']!r}"
            )
        seen_edges.add(identity)
        validated.append(
            {
                "source": source,
                "target": target,
                "witnesses": witness_fields,
            }
        )
    return validated


def _validate_declared_sample(
    ref: Any, declared_samples: Set[SampleId], where: str
) -> SampleId:
    sample = _validate_sample_ref(ref, where)
    if sample not in declared_samples:
        raise InvalidSampleLineageInputError(
            f"{where} is not a declared sample: {ref!r}"
        )
    return sample


def _validate_query(target: Any, direction: Any, max_depth: Any) -> None:
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


def _validate_sample_links(
    sample_links: Any,
) -> List[Tuple[SampleId, SampleId]]:
    """Validate the shape of the undirected sample link graph.

    Endpoints are only checked structurally here; resolving them against
    the declared samples happens separately. Structural problems (not a
    list, missing/extra keys, empty identifiers, self links and duplicate
    relationships) take precedence over unknown-reference errors.
    """
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
        left = _validate_sample_ref(link["left"], f"sample_links[{index}].left")
        right = _validate_sample_ref(
            link["right"], f"sample_links[{index}].right"
        )
        pairs.append((left, right))

    seen_relations: Set[FrozenSet[SampleId]] = set()
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
    links: List[Tuple[SampleId, SampleId]], declared_samples: Set[SampleId]
) -> None:
    for left, right in links:
        for endpoint in (left, right):
            if endpoint not in declared_samples:
                raise UnknownSampleLineageReferenceError(
                    f"sample link endpoint is not a declared sample: "
                    f"{_sample_ref(endpoint)!r}"
                )


def _build_adjacency(
    field_edges: List[Dict[str, Any]],
    links: List[Tuple[SampleId, SampleId]],
) -> Tuple[Dict[SampleId, List[SampleId]], Dict[SampleId, List[SampleId]]]:
    """Build directed outgoing/incoming sample adjacency.

    Field edges contribute one directed hop (source -> target); undirected
    sample links contribute the hop both ways.
    """
    outgoing: Dict[SampleId, List[SampleId]] = {}
    incoming: Dict[SampleId, List[SampleId]] = {}
    for edge in field_edges:
        outgoing.setdefault(edge["source"], []).append(edge["target"])
        incoming.setdefault(edge["target"], []).append(edge["source"])
    for left, right in links:
        outgoing.setdefault(left, []).append(right)
        incoming.setdefault(left, []).append(right)
        outgoing.setdefault(right, []).append(left)
        incoming.setdefault(right, []).append(left)
    return outgoing, incoming


def _walk(
    start: SampleId,
    neighbors: Dict[SampleId, List[SampleId]],
    max_depth: Optional[int],
) -> Dict[SampleId, int]:
    """BFS from ``start``; first visit wins, so depths are shortest."""
    depths = {start: 0}
    if max_depth == 0:
        return depths

    queue = deque([(start, 0)])
    while queue:
        sample, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        next_depth = depth + 1
        for nxt in neighbors.get(sample, ()):
            if nxt not in depths:
                depths[nxt] = next_depth
                queue.append((nxt, next_depth))
    return depths


def _side(
    depths: Optional[Dict[SampleId, int]],
    field_edges: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if depths is None:
        return {"samples": [], "edges": []}

    samples = [
        {"dataset_id": dataset, "sample_id": sample_key, "depth": depth}
        for (dataset, sample_key), depth in sorted(
            depths.items(), key=lambda item: (item[1], item[0][0], item[0][1])
        )
    ]

    side_edges: List[Dict[str, Any]] = []
    for edge in field_edges:
        if edge["source"] not in depths or edge["target"] not in depths:
            continue
        witnesses = sorted(edge["witnesses"])
        side_edges.append(
            {
                "source": _sample_ref(edge["source"]),
                "target": _sample_ref(edge["target"]),
                "witnesses": [_field_ref(field) for field in witnesses],
                "_sort": (
                    edge["source"][0],
                    edge["source"][1],
                    edge["target"][0],
                    edge["target"][1],
                    tuple(witnesses),
                ),
            }
        )
    side_edges.sort(key=lambda item: item["_sort"])
    for item in side_edges:
        del item["_sort"]

    return {"samples": samples, "edges": side_edges}


def trace_sample_lineage(
    samples: Any,
    fields: Any,
    edges: Any,
    sample_links: Any,
    target: Any,
    direction: Any = "both",
    max_depth: Any = None,
) -> Dict[str, Any]:
    """Trace upstream/downstream sample-level lineage of ``target``.

    :param samples: list of distinct samples, each an object with exactly
        the keys ``dataset_id`` and ``sample_id`` (non-empty strings).
    :param fields: object mapping non-empty table ids to lists of distinct
        non-empty field ids; witness references must be declared here.
    :param edges: list of field-edge objects, each with exactly the keys
        ``source``, ``target`` and ``witnesses``. ``source`` and
        ``target`` are ``{"dataset_id", "sample_id"}`` samples declared in
        ``samples``; the edge runs from source to target. ``witnesses``
        is a list of distinct ``{"table", "column"}`` field references
        declared in ``fields`` (an empty list is legal). Two edges are
        duplicates only when endpoints and witnesses agree.
    :param sample_links: list of undirected link objects, each with
        exactly the keys ``left`` and ``right``; endpoints are
        ``{"dataset_id", "sample_id"}`` objects. A link and its
        left/right reversal are the same relationship; self links and
        duplicate links are rejected. Every endpoint must be declared in
        ``samples``.
    :param target: ``{"dataset_id", "sample_id"}`` sample to trace from;
        depth 0 in every returned side.
    :param direction: ``"upstream"``, ``"downstream"`` or ``"both"``
        (default ``"both"``).
    :param max_depth: ``None`` for unlimited (default) or an integer
        ``>= 0``; ``0`` returns only ``target``. Booleans are not
        integers here.
    :returns: ``{"target", "direction", "max_depth",
        "upstream": {"samples", "edges"},
        "downstream": {"samples", "edges"}}``. Each populated side lists
        samples as ``{"dataset_id", "sample_id", "depth"}`` sorted by
        ``(depth, dataset_id, sample_id)`` and includes every field edge
        whose source and target samples are both reached, sorted by
        ``(source.dataset_id, source.sample_id, target.dataset_id,
        target.sample_id, witnesses)`` with its witnesses sorted by
        ``(table, column)``. The side not queried is returned empty.
    :raises ValueError: :class:`InvalidSampleLineageInputError`,
        :class:`InvalidSampleLineageQueryError` or
        :class:`UnknownSampleLineageTargetError`.
    :raises LookupError: :class:`UnknownSampleLineageReferenceError` when
        a ``sample_links`` endpoint is not declared in ``samples``.
    """
    declared_fields = _validate_fields(fields)
    declared_samples = _validate_samples(samples)
    field_edges = _validate_edges(edges, declared_fields, declared_samples)
    links = _validate_sample_links(sample_links)
    _validate_query(target, direction, max_depth)

    start = _sample_id(target)
    if start not in declared_samples:
        raise UnknownSampleLineageTargetError(
            f"target is not declared in samples: {target!r}"
        )
    _check_link_references(links, declared_samples)

    outgoing, incoming = _build_adjacency(field_edges, links)

    upstream_depths: Optional[Dict[SampleId, int]] = None
    downstream_depths: Optional[Dict[SampleId, int]] = None
    if direction in ("upstream", "both"):
        upstream_depths = _walk(start, incoming, max_depth)
    if direction in ("downstream", "both"):
        downstream_depths = _walk(start, outgoing, max_depth)

    return {
        "target": target,
        "direction": direction,
        "max_depth": max_depth,
        "upstream": _side(upstream_depths, field_edges),
        "downstream": _side(downstream_depths, field_edges),
    }
