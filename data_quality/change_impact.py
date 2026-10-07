"""Change impact analysis over registered dataset and field lineage.

The public entry point is :func:`analyze_change_impact`. A caller
describes one entry dataset change -- a deletion, a rename or a type
change of one or more entry fields -- together with the registered
metadata (declared datasets/fields and directed lineage edges). The
analysis follows the confirmed directed edges downstream, reports every
directly or indirectly affected dataset, the provably affected columns
of each dataset, the entry-field sources of every such column and one
stable shortest path from the entry dataset to the dataset.

Only provable column-level dependencies populate affected fields. A
dataset reachable through dataset-level dependencies alone is reported
with an empty field list and ``unknown`` marked ``true``; names are
never guessed. Edges are only traversed in their confirmed direction, so
upstreams are never reported as affected. Self-loops and cycles are
legal: reachability always means crossing at least one edge and the
shortest-path dynamic program terminates. The analysis never mutates the
payload, writes to disk or contacts any service; it is a pure function.

Business validation raises one of the :class:`ChangeImpactError`
subclasses below, each carrying a stable ``code``. There is never a
partial result.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

__all__ = [
    "analyze_change_impact",
    "ChangeImpactError",
    "ChangeImpactInputError",
    "DatasetNotFoundError",
    "FieldNotFoundError",
    "InvalidRenameError",
    "DuplicateFieldError",
    "InvalidFieldsError",
]

_CHANGE_TYPES = ("delete", "rename", "type_change")
_BASE_KEYS = frozenset({"dataset", "changeType", "fields", "metadata"})
_TYPE_CHANGE_KEYS = _BASE_KEYS | {"newType"}
_METADATA_KEYS = frozenset({"datasets", "edges"})
_EDGE_KEYS = frozenset({"source", "target"})
_FIELD_DECL_KEYS = frozenset({"name", "type"})
_RENAME_KEYS = frozenset({"oldField", "newField"})
_DATASET_REF_KEYS = frozenset({"dataset"})
_FIELD_REF_KEYS = frozenset({"dataset", "field"})

# A column node is a (dataset, field) pair.
FieldId = Tuple[str, str]
Path = Tuple[Any, ...]


class ChangeImpactError(ValueError):
    """Base class for change-impact errors; carries a CLI error ``code``."""

    code = "INVALID_CHANGE_IMPACT_INPUT"


class ChangeImpactInputError(ChangeImpactError):
    """The request or the registered metadata is structurally malformed."""

    code = "INVALID_CHANGE_IMPACT_INPUT"


class DatasetNotFoundError(ChangeImpactError):
    """The entry dataset is not declared in the metadata."""

    code = "DATASET_NOT_FOUND"


class FieldNotFoundError(ChangeImpactError):
    """An entry field is not declared in the entry dataset metadata."""

    code = "FIELD_NOT_FOUND"


class InvalidRenameError(ChangeImpactError):
    """A rename carries the same old and new field name."""

    code = "INVALID_RENAME"


class DuplicateFieldError(ChangeImpactError):
    """The request lists the same field more than once."""

    code = "DUPLICATE_FIELD"


class InvalidFieldsError(ChangeImpactError):
    """The change field set is empty."""

    code = "INVALID_FIELDS"


def _error(message: str) -> ChangeImpactInputError:
    return ChangeImpactInputError(message)


def _json_equal(left: Any, right: Any) -> bool:
    """Structural JSON equality (booleans never equal numbers)."""
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


def _validate_metadata(
    metadata: Any,
) -> Tuple[
    Set[str],
    Dict[FieldId, Any],
    Dict[FieldId, List[FieldId]],
    Set[Tuple[str, str]],
]:
    """Validate ``metadata``.

    Returns the declared dataset ids, the declared field type map, the
    column-level outgoing adjacency and the set of explicit
    dataset-level edges. Column edges are deliberately *not* projected
    onto datasets here: a column edge only proves dataset reachability
    when its source column is itself reachable from the changed fields.
    """
    if not isinstance(metadata, dict):
        raise _error("metadata must be an object")
    keys = set(metadata)
    if keys != _METADATA_KEYS:
        missing = sorted(_METADATA_KEYS - keys)
        if missing:
            raise _error(f"metadata is missing keys: {missing}")
        unknown = sorted(keys - _METADATA_KEYS)
        raise _error(f"metadata has unsupported keys: {unknown}")

    datasets = metadata["datasets"]
    if not isinstance(datasets, dict):
        raise _error(
            "metadata.datasets must be an object mapping dataset ids to "
            "field lists"
        )

    declared_datasets: Set[str] = set()
    field_types: Dict[FieldId, Any] = {}
    for dataset, decls in datasets.items():
        if not isinstance(dataset, str) or not dataset:
            raise _error(
                "metadata.datasets key must be a non-empty dataset id "
                f"string: {dataset!r}"
            )
        declared_datasets.add(dataset)
        if not isinstance(decls, list):
            raise _error(
                f"metadata.datasets[{dataset!r}] must be a list of field "
                "declarations"
            )
        seen_names: Set[str] = set()
        for index, decl in enumerate(decls):
            if not isinstance(decl, dict):
                raise _error(
                    f"metadata.datasets[{dataset!r}][{index}] must be an "
                    "object"
                )
            decl_keys = set(decl)
            if decl_keys != _FIELD_DECL_KEYS:
                missing = sorted(_FIELD_DECL_KEYS - decl_keys)
                if missing:
                    raise _error(
                        f"metadata.datasets[{dataset!r}][{index}] is "
                        f"missing keys: {missing}"
                    )
                unknown_fields = sorted(decl_keys - _FIELD_DECL_KEYS)
                raise _error(
                    f"metadata.datasets[{dataset!r}][{index}] has "
                    f"unsupported keys: {unknown_fields}"
                )
            name = decl["name"]
            if not isinstance(name, str) or not name:
                raise _error(
                    f"metadata.datasets[{dataset!r}][{index}].name must "
                    "be a non-empty string"
                )
            if name in seen_names:
                raise _error(
                    f"duplicate field {name!r} in dataset {dataset!r}"
                )
            seen_names.add(name)
            # The type value is preserved verbatim; any JSON value is legal.
            field_types[(dataset, name)] = decl["type"]

    edges = metadata["edges"]
    if not isinstance(edges, list):
        raise _error("metadata.edges must be a list")

    column_outgoing: Dict[FieldId, List[FieldId]] = {}
    dataset_edges: Set[Tuple[str, str]] = set()
    seen_column_edges: Set[Tuple[FieldId, FieldId]] = set()

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise _error(f"metadata.edges[{index}] must be an object")
        edge_keys = set(edge)
        if edge_keys != _EDGE_KEYS:
            missing = sorted(_EDGE_KEYS - edge_keys)
            if missing:
                raise _error(
                    f"metadata.edges[{index}] is missing keys: {missing}"
                )
            unknown_edges = sorted(edge_keys - _EDGE_KEYS)
            raise _error(
                f"metadata.edges[{index}] has unsupported keys: "
                f"{unknown_edges}"
            )
        source = edge["source"]
        target = edge["target"]
        source_field = _check_endpoint(
            source, declared_datasets, field_types, f"metadata.edges[{index}].source"
        )
        target_field = _check_endpoint(
            target, declared_datasets, field_types, f"metadata.edges[{index}].target"
        )
        if (source_field is None) != (target_field is None):
            raise _error(
                f"metadata.edges[{index}] mixes dataset-level and "
                "column-level endpoints"
            )

        if source_field is not None:
            pair = (source_field, target_field)
            if pair in seen_column_edges:
                raise _error(
                    f"duplicate column edge: {source!r} -> {target!r}"
                )
            seen_column_edges.add(pair)
            column_outgoing.setdefault(source_field, []).append(target_field)
        else:
            ds_pair = (source["dataset"], target["dataset"])
            if ds_pair in dataset_edges:
                raise _error(
                    f"duplicate dataset edge: {source!r} -> {target!r}"
                )
            dataset_edges.add(ds_pair)

    return declared_datasets, field_types, column_outgoing, dataset_edges


def _check_endpoint(
    endpoint: Any,
    declared_datasets: Set[str],
    field_types: Dict[FieldId, Any],
    where: str,
) -> Optional[FieldId]:
    """Validate one edge endpoint.

    Returns the ``(dataset, field)`` pair for a column-level endpoint or
    ``None`` for a dataset-level endpoint.
    """
    if not isinstance(endpoint, dict):
        raise _error(f"{where} must be an object")
    keys = set(endpoint)
    if keys not in (_DATASET_REF_KEYS, _FIELD_REF_KEYS):
        missing = sorted(_DATASET_REF_KEYS - keys)
        if missing:
            raise _error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - _FIELD_REF_KEYS)
        raise _error(f"{where} has unsupported keys: {unknown}")

    dataset = endpoint["dataset"]
    if not isinstance(dataset, str) or not dataset:
        raise _error(f"{where}.dataset must be a non-empty string")
    if dataset not in declared_datasets:
        raise _error(f"{where} names an undeclared dataset: {dataset!r}")

    if "field" not in endpoint:
        return None

    field = endpoint["field"]
    if not isinstance(field, str) or not field:
        raise _error(f"{where}.field must be a non-empty string")
    field_id = (dataset, field)
    if field_id not in field_types:
        raise _error(f"{where} names an undeclared field: {endpoint!r}")
    return field_id


def _validate_change(
    payload: Dict[str, Any],
    declared_datasets: Set[str],
    field_types: Dict[FieldId, Any],
) -> Tuple[str, List[FieldId], Any]:
    """Validate the entry dataset and change fields.

    Returns the change type, the ordered effective seed fields and (for
    type changes) the new type value. Structural shape problems come
    first, then an empty field set, rename equality, duplicates, the
    entry dataset declaration and finally the entry field declarations.
    """
    change_type = payload["changeType"]
    dataset = payload["dataset"]
    if not isinstance(dataset, str) or not dataset:
        raise _error("dataset must be a non-empty string")

    new_type: Any = None
    if change_type == "type_change":
        new_type = payload["newType"]

    if change_type == "rename":
        rename = payload["fields"]
        if not isinstance(rename, dict):
            raise _error("fields must be an object with oldField and newField")
        rename_keys = set(rename)
        if rename_keys != _RENAME_KEYS:
            missing = sorted(_RENAME_KEYS - rename_keys)
            if missing:
                raise _error(f"fields is missing keys: {missing}")
            unknown = sorted(rename_keys - _RENAME_KEYS)
            raise _error(f"fields has unsupported keys: {unknown}")
        old_field = rename["oldField"]
        new_field = rename["newField"]
        for key, value in (("oldField", old_field), ("newField", new_field)):
            if not isinstance(value, str) or not value:
                raise _error(f"fields.{key} must be a non-empty string")
        if old_field == new_field:
            raise InvalidRenameError(
                f"rename oldField and newField must differ: {old_field!r}"
            )
        if dataset not in declared_datasets:
            raise DatasetNotFoundError(
                f"entry dataset is not declared: {dataset!r}"
            )
        if (dataset, old_field) not in field_types:
            raise FieldNotFoundError(
                f"entry field is not declared: {dataset!r}.{old_field!r}"
            )
        seeds = [(dataset, old_field)]
        # The new name may already be a registered field (carrying its
        # own downstream dependencies); an unregistered new name simply
        # has no registered downstream yet.
        if (dataset, new_field) in field_types:
            seeds.append((dataset, new_field))
        return change_type, seeds, new_type

    fields = payload["fields"]
    if not isinstance(fields, list):
        raise _error("fields must be a list of field id strings")
    if not fields:
        raise InvalidFieldsError("fields must not be empty")
    for index, field in enumerate(fields):
        if not isinstance(field, str) or not field:
            raise _error(
                f"fields[{index}] must be a non-empty field id string"
            )
    if len(set(fields)) != len(fields):
        raise DuplicateFieldError("fields must not contain duplicates")

    if dataset not in declared_datasets:
        raise DatasetNotFoundError(
            f"entry dataset is not declared: {dataset!r}"
        )

    effective: List[FieldId] = []
    for field in fields:
        field_id = (dataset, field)
        if field_id not in field_types:
            raise FieldNotFoundError(
                f"entry field is not declared: {dataset!r}.{field!r}"
            )
        if change_type == "type_change":
            # Same raw type means no change: that seed produces no impact.
            if not _json_equal(field_types[field_id], new_type):
                effective.append(field_id)
        else:
            effective.append(field_id)

    return change_type, effective, new_type


def _shortest_paths(
    start: Any,
    outgoing: Dict[Any, List[Any]],
) -> Dict[Any, Path]:
    """Shortest node sequence from ``start`` to every node reachable
    across one or more edges.

    BFS fixes the shortest distance of every node; the lexicographically
    smallest node sequence of that length is then a dynamic program by
    increasing distance -- each node prepends the smallest sequence of a
    predecessor one edge closer. A self-loop or a longer cycle leading
    back to the start also makes the start reachable across at least one
    edge.
    """
    dist: Dict[Any, int] = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in outgoing.get(current, ()):
            if nxt not in dist:
                dist[nxt] = dist[current] + 1
                queue.append(nxt)

    incoming: Dict[Any, List[Any]] = {}
    for source, targets in outgoing.items():
        for target in targets:
            incoming.setdefault(target, []).append(source)

    best: Dict[Any, Path] = {start: (start,)}
    for depth in range(1, max(dist.values(), default=0) + 1):
        for node in (node for node, d in dist.items() if d == depth):
            candidates = [
                best[predecessor] + (node,)
                for predecessor in incoming.get(node, ())
                if dist.get(predecessor) == depth - 1
            ]
            best[node] = min(candidates)

    paths = {node: best[node] for node in dist if node != start}

    # A cycle back to the start only closes through a predecessor that is
    # itself reachable from the start; upstream-only predecessors pointing
    # at the start are not on a downstream path.
    cycles = [
        best[node] + (start,)
        for node in incoming.get(start, ())
        if node != start and node in best
    ]
    if start in outgoing.get(start, ()):
        cycles.append((start, start))
    if cycles:
        paths[start] = min(cycles, key=lambda sequence: (len(sequence), sequence))

    return paths


def _field_ref(field_id: FieldId) -> Dict[str, str]:
    return {"dataset": field_id[0], "field": field_id[1]}


def analyze_change_impact(payload: Any) -> Dict[str, Any]:
    """Analyze the downstream impact of one entry dataset change.

    :param payload: object with exactly the keys ``dataset``,
        ``changeType``, ``fields`` and ``metadata`` (plus ``newType`` for
        a type change). ``dataset`` is a non-empty entry dataset id.
        ``changeType`` is one of ``"delete"``, ``"rename"`` or
        ``"type_change"``. For deletion and type change ``fields`` is a
        non-empty list of distinct non-empty field name strings; for a
        rename it is a single ``{"oldField", "newField"}`` object of two
        distinct non-empty strings. ``newType`` is the raw new type
        value, compared structurally against the registered type.
        ``metadata`` is ``{"datasets": {...}, "edges": [...]}`` where
        ``datasets`` maps dataset ids to ``[{"name", "type"}]``
        declarations (the raw ``type`` value is preserved) and ``edges``
        lists directed edges from ``source`` to ``target``; each endpoint
        is either ``{"dataset"}`` (dataset-level) or ``{"dataset",
        "field"}`` (column-level, both endpoints at the same granularity)
        and must be declared. Self-loops and cycles are legal.
    :returns: ``{"status": "ok", "change": {...}, "affectedDatasets":
        [...]}``. ``change`` echoes the entry summary (``dataset`` and
        ``changeType`` plus ``fields`` for deletion/type change, or
        ``oldField``/``newField`` for rename; type-change ``fields`` only
        contains fields whose registered type differs and ``newType`` is
        the raw value). Each affected dataset (reachable across at least
        one edge) contains ``dataset``, ``distance``, ``unknown``,
        ``fields`` and ``path``; datasets sort by ``(distance,
        dataset)``. A dataset reached only through dataset-level
        dependencies has ``unknown`` true and empty ``fields``.
        Otherwise every listed field carries ``sources`` -- one
        ``{"dataset", "field", "path"}`` entry per entry field that
        provably reaches it, sorted by source, each a shortest column
        path with lexicographically smallest ties. ``path`` is the
        shortest dataset sequence from the entry dataset to the dataset
        (lexicographically smallest ties). With no downstream,
        ``affectedDatasets`` is an empty list and the entry summary is
        still returned.
    :raises ValueError: :class:`ChangeImpactInputError` for structural
        faults, :class:`InvalidFieldsError` for an empty field set,
        :class:`DuplicateFieldError` for duplicate fields,
        :class:`InvalidRenameError` for old and new rename names being
        equal, :class:`DatasetNotFoundError` for an undeclared entry
        dataset and :class:`FieldNotFoundError` for an undeclared entry
        field; never a partial result.
    """
    if not isinstance(payload, dict):
        raise _error("payload must be an object")

    change_type = payload.get("changeType")
    if not isinstance(change_type, str) or change_type not in _CHANGE_TYPES:
        raise _error(
            f"changeType must be one of {list(_CHANGE_TYPES)}, "
            f"got {change_type!r}"
        )

    expected_keys = (
        _TYPE_CHANGE_KEYS if change_type == "type_change" else _BASE_KEYS
    )
    keys = set(payload)
    if keys != expected_keys:
        missing = sorted(expected_keys - keys)
        if missing:
            raise _error(f"payload is missing keys: {missing}")
        unknown = sorted(keys - expected_keys)
        raise _error(f"payload has unsupported keys: {unknown}")

    (
        declared_datasets,
        field_types,
        column_outgoing,
        dataset_edges,
    ) = _validate_metadata(payload["metadata"])

    change_type, seeds, new_type = _validate_change(
        payload, declared_datasets, field_types
    )

    dataset = payload["dataset"]

    # Column reachability is proven per entry seed along column edges
    # only; every provenance keeps its own shortest column path.
    field_sources: Dict[FieldId, List[Tuple[FieldId, Path]]] = {}
    for seed in seeds:
        for target, path in _shortest_paths(seed, column_outgoing).items():
            field_sources.setdefault(target, []).append((seed, path))

    # Dataset-level reachability follows the explicit dataset edges plus
    # a column edge projected onto its datasets, but only when the edge
    # leaves a column that is itself provably reached from a changed
    # field (a column edge out of an unrelated entry column proves
    # nothing). Without any effective seed (e.g. a type change where
    # every field keeps the same raw type) nothing propagates at all.
    dataset_outgoing: Dict[str, List[str]] = {}
    if seeds:
        proven_columns: Set[FieldId] = set(seeds)
        proven_columns.update(field_sources)

        def add_dataset_edge(source_dataset: str, target_dataset: str) -> None:
            targets = dataset_outgoing.setdefault(source_dataset, [])
            if target_dataset not in targets:
                targets.append(target_dataset)

        for source_dataset, target_dataset in dataset_edges:
            add_dataset_edge(source_dataset, target_dataset)
        for source_field, target_fields in column_outgoing.items():
            if source_field in proven_columns:
                for target_field in target_fields:
                    add_dataset_edge(source_field[0], target_field[0])

    dataset_paths = (
        _shortest_paths(dataset, dataset_outgoing) if seeds else {}
    )

    affected: List[Dict[str, Any]] = []
    for target_dataset in sorted(
        dataset_paths,
        key=lambda ds: (len(dataset_paths[ds]) - 1, ds),
    ):
        dataset_path = [
            {"dataset": node} for node in dataset_paths[target_dataset]
        ]
        hit_fields = sorted(
            field for (ds, field) in field_sources if ds == target_dataset
        )
        if not hit_fields:
            affected.append(
                {
                    "dataset": target_dataset,
                    "distance": len(dataset_path) - 1,
                    "unknown": True,
                    "fields": [],
                    "path": dataset_path,
                }
            )
            continue

        fields_out: List[Dict[str, Any]] = []
        for field_name in hit_fields:
            provenance = sorted(
                field_sources[(target_dataset, field_name)],
                key=lambda item: item[0],
            )
            sources = [
                {
                    "dataset": source[0],
                    "field": source[1],
                    "path": [_field_ref(node) for node in path],
                }
                for source, path in provenance
            ]
            fields_out.append({"field": field_name, "sources": sources})

        affected.append(
            {
                "dataset": target_dataset,
                "distance": len(dataset_path) - 1,
                "unknown": False,
                "fields": fields_out,
                "path": dataset_path,
            }
        )

    change: Dict[str, Any] = {"dataset": dataset, "changeType": change_type}
    if change_type == "rename":
        change["oldField"] = payload["fields"]["oldField"]
        change["newField"] = payload["fields"]["newField"]
    else:
        change["fields"] = [field_id[1] for field_id in seeds]
        if change_type == "type_change":
            change["newType"] = new_type

    return {"status": "ok", "change": change, "affectedDatasets": affected}
