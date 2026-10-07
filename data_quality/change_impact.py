"""Change-impact analysis over registered dataset/field lineage.

The public entry point is :func:`analyze_change_impact`. A caller submits
the entry dataset id, a change type and the changed fields; the analyzer
walks the already-registered directed lineage edges (an edge points from
an upstream object to the object that depends on it) and reports every
directly or indirectly downstream dataset, the fields actually hit in
each dataset, the entry field each hit originates from and the stable
shortest path from the entry dataset to that dataset.

Only three change types are accepted, all purely analytical -- the
function never mutates data, rules or lineage metadata:

* ``delete`` -- the entry fields disappear;
* ``rename`` -- one old field migrates to one new field; downstream
  propagation considers both the deletion of the old field and the
  dependencies of the new field, while the response stays a single
  change summary;
* ``type_change`` -- the semantic type of the same field changes; a
  downstream field is reported only when its raw metadata type differs
  from the entry field's raw type (equal raw values prove no impact).

Field impact is accepted solely on provable column-level dependency
chains. When a dataset is reachable only through dataset-level edges,
its affected field list is empty and it is marked ``unknown`` -- names
are never guessed. Relations whose direction cannot be proved are never
traversed backwards, so upstream objects are never reported as affected.

Structural problems raise :class:`ChangeImpactInputError`. The five
business errors -- :class:`ChangeImpactDatasetNotFoundError`,
:class:`ChangeImpactFieldNotFoundError`,
:class:`ChangeImpactInvalidRenameError`,
:class:`ChangeImpactDuplicateFieldError` and
:class:`ChangeImpactInvalidFieldsError` -- all subclass
:class:`ChangeImpactError` and carry a stable :attr:`code
<ChangeImpactError.code>`. None of them returns a partial result.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set, Tuple

__all__ = [
    "analyze_change_impact",
    "ChangeImpactError",
    "ChangeImpactInputError",
    "ChangeImpactDatasetNotFoundError",
    "ChangeImpactFieldNotFoundError",
    "ChangeImpactInvalidRenameError",
    "ChangeImpactDuplicateFieldError",
    "ChangeImpactInvalidFieldsError",
]

_PAYLOAD_KEYS = frozenset({"metadata", "change"})
_METADATA_KEYS = frozenset({"datasets", "lineageEdges"})
_CHANGE_COMMON_KEYS = frozenset({"dataset", "changeType"})
_FIELDS_CHANGE_KEYS = _CHANGE_COMMON_KEYS | {"fields"}
_RENAME_CHANGE_KEYS = _CHANGE_COMMON_KEYS | {"oldField", "newField"}
_VALID_CHANGE_TYPES = ("delete", "rename", "type_change")

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]


class ChangeImpactError(ValueError):
    """Base class for the business errors of change-impact analysis."""

    code = "CHANGE_IMPACT_ERROR"


class ChangeImpactInputError(ValueError):
    """The request payload, metadata or change object is malformed."""


class ChangeImpactDatasetNotFoundError(ChangeImpactError):
    """The entry dataset id is not declared in the metadata."""

    code = "DATASET_NOT_FOUND"


class ChangeImpactFieldNotFoundError(ChangeImpactError):
    """An entry field is not declared in the entry dataset."""

    code = "FIELD_NOT_FOUND"


class ChangeImpactInvalidRenameError(ChangeImpactError):
    """A rename change names the same field as old and new."""

    code = "INVALID_RENAME"


class ChangeImpactDuplicateFieldError(ChangeImpactError):
    """The same field is referenced more than once in one request."""

    code = "DUPLICATE_FIELD"


class ChangeImpactInvalidFieldsError(ChangeImpactError):
    """The request carries no field to analyze."""

    code = "INVALID_FIELDS"


def _input_error(message: str) -> ChangeImpactInputError:
    return ChangeImpactInputError(message)


def _full_name(field_id: FieldId) -> str:
    return f"{field_id[0]}.{field_id[1]}"


def _name_path(path: Tuple[FieldId, ...]) -> Tuple[str, ...]:
    """Lexicographic ordering view: full names ``dataset.field``."""
    return tuple(_full_name(node) for node in path)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _validate_datasets(
    datasets: Any,
) -> Tuple[Set[str], Dict[FieldId, Any]]:
    """Validate the ``datasets`` declaration.

    Each dataset maps to a list of field declarations. A field is either a
    non-empty field id string (its raw type is unknown) or an object with
    exactly ``field`` and ``type``; the ``type`` value is preserved
    verbatim and may be any JSON value.

    :returns: the declared dataset ids and a map of every declared field
        to its raw type value (``None`` when no type was declared).
    """
    if not isinstance(datasets, dict):
        raise _input_error(
            "metadata.datasets must be an object mapping dataset ids to "
            "field lists"
        )

    declared_datasets: Set[str] = set()
    field_types: Dict[FieldId, Any] = {}
    for dataset, fields in datasets.items():
        if not _is_non_empty_string(dataset):
            raise _input_error(
                "metadata.datasets key must be a non-empty dataset id "
                f"string: {dataset!r}"
            )
        declared_datasets.add(dataset)
        if not isinstance(fields, list):
            raise _input_error(
                f"metadata.datasets[{dataset!r}] must be a list of field "
                "declarations"
            )
        seen_fields: Set[str] = set()
        for index, field_decl in enumerate(fields):
            if isinstance(field_decl, str):
                if not field_decl:
                    raise _input_error(
                        f"metadata.datasets[{dataset!r}][{index}] must be a "
                        "non-empty field id string"
                    )
                field_name, field_type = field_decl, None
            elif isinstance(field_decl, dict):
                keys = set(field_decl)
                if keys != {"field", "type"}:
                    missing = sorted({"field", "type"} - keys)
                    if missing:
                        raise _input_error(
                            f"metadata.datasets[{dataset!r}][{index}] is "
                            f"missing keys: {missing}"
                        )
                    unknown = sorted(keys - {"field", "type"})
                    raise _input_error(
                        f"metadata.datasets[{dataset!r}][{index}] has "
                        f"unsupported keys: {unknown}"
                    )
                field_name = field_decl["field"]
                if not _is_non_empty_string(field_name):
                    raise _input_error(
                        f"metadata.datasets[{dataset!r}][{index}].field must "
                        "be a non-empty string"
                    )
                field_type = field_decl["type"]
            else:
                raise _input_error(
                    f"metadata.datasets[{dataset!r}][{index}] must be a field "
                    "id string or a {field, type} object"
                )
            if field_name in seen_fields:
                raise _input_error(
                    f"duplicate field in dataset {dataset!r}: {field_name!r}"
                )
            seen_fields.add(field_name)
            field_types[(dataset, field_name)] = field_type
    return declared_datasets, field_types


def _check_nested_endpoint(
    endpoint: Any,
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
    where: str,
) -> Tuple[bool, FieldId]:
    """Validate one nested ``{"dataset"}`` / ``{"dataset", "field"}`` end.

    :returns: ``(is_field_level, node)``; at dataset level the node's
        field component is ``None``.
    """
    if not isinstance(endpoint, dict):
        raise _input_error(f"{where} must be an object")
    keys = set(endpoint)
    if keys not in ({"dataset"}, {"dataset", "field"}):
        missing = sorted({"dataset"} - keys)
        if missing:
            raise _input_error(f"{where} is missing keys: {missing}")
        unknown = sorted(keys - {"dataset", "field"})
        raise _input_error(f"{where} has unsupported keys: {unknown}")
    dataset = endpoint["dataset"]
    if not _is_non_empty_string(dataset):
        raise _input_error(f"{where}.dataset must be a non-empty string")
    if dataset not in declared_datasets:
        raise _input_error(
            f"{where}.dataset is not a declared dataset: {dataset!r}"
        )
    if keys == {"dataset"}:
        return False, (dataset, None)
    field = endpoint["field"]
    if not _is_non_empty_string(field):
        raise _input_error(f"{where}.field must be a non-empty string")
    if (dataset, field) not in declared_fields:
        raise _input_error(
            f"{where} is not a declared field: {endpoint!r}"
        )
    return True, (dataset, field)


def _validate_edges(
    edges: Any,
    declared_datasets: Set[str],
    declared_fields: Set[FieldId],
) -> Tuple[Set[Tuple[str, str]], Set[Tuple[FieldId, FieldId]]]:
    """Validate ``lineageEdges``.

    Two equivalent shapes are accepted, directed from the upstream object
    to the dependent object:

    * nested -- ``{"source": {"dataset"}, "target": {"dataset"}}`` for a
      dataset-level edge or ``{"source": {"dataset", "field"}, "target":
      {"dataset", "field"}}`` for a column-level edge;
    * flat -- ``{"sourceDataset", "targetDataset"}`` or the four-key
      ``sourceField``/``targetField`` shape.

    Both endpoints of one edge must use the same granularity. Identical
    repeated edges collapse to one; self-loops and cycles are legal.

    :returns: the dataset-level edge set and the column-level edge set.
    """
    if not isinstance(edges, list):
        raise _input_error("metadata.lineageEdges must be a list")

    dataset_edges: Set[Tuple[str, str]] = set()
    column_edges: Set[Tuple[FieldId, FieldId]] = set()

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise _input_error(
                f"metadata.lineageEdges[{index}] must be an object"
            )
        keys = set(edge)

        if "source" in keys or "target" in keys:
            if keys != {"source", "target"}:
                missing = sorted({"source", "target"} - keys)
                if missing:
                    raise _input_error(
                        f"metadata.lineageEdges[{index}] is missing keys: "
                        f"{missing}"
                    )
                unknown = sorted(keys - {"source", "target"})
                raise _input_error(
                    f"metadata.lineageEdges[{index}] has unsupported keys: "
                    f"{unknown}"
                )
            source_level, source_node = _check_nested_endpoint(
                edge["source"], declared_datasets, declared_fields,
                f"metadata.lineageEdges[{index}].source",
            )
            target_level, target_node = _check_nested_endpoint(
                edge["target"], declared_datasets, declared_fields,
                f"metadata.lineageEdges[{index}].target",
            )
            if source_level != target_level:
                raise _input_error(
                    f"metadata.lineageEdges[{index}] mixes dataset-level and "
                    "field-level endpoints"
                )
            if source_level:
                column_edges.add((source_node, target_node))
            else:
                dataset_edges.add((source_node[0], target_node[0]))
            continue

        source_dataset = edge.get("sourceDataset")
        target_dataset = edge.get("targetDataset")
        if source_dataset is None and "sourceDataset" not in edge:
            raise _input_error(
                f"metadata.lineageEdges[{index}] must have source/target "
                "endpoints or sourceDataset/targetDataset keys"
            )
        if keys == {"sourceDataset", "targetDataset"}:
            for key, value in (
                ("sourceDataset", source_dataset),
                ("targetDataset", target_dataset),
            ):
                if not _is_non_empty_string(value):
                    raise _input_error(
                        f"metadata.lineageEdges[{index}].{key} must be a "
                        "non-empty string"
                    )
            if source_dataset not in declared_datasets:
                raise _input_error(
                    f"metadata.lineageEdges[{index}].sourceDataset is not a "
                    f"declared dataset: {source_dataset!r}"
                )
            if target_dataset not in declared_datasets:
                raise _input_error(
                    f"metadata.lineageEdges[{index}].targetDataset is not a "
                    f"declared dataset: {target_dataset!r}"
                )
            dataset_edges.add((source_dataset, target_dataset))
        elif keys == {
            "sourceDataset",
            "sourceField",
            "targetDataset",
            "targetField",
        }:
            source_field = edge["sourceField"]
            target_field = edge["targetField"]
            for key, value in (
                ("sourceDataset", source_dataset),
                ("sourceField", source_field),
                ("targetDataset", target_dataset),
                ("targetField", target_field),
            ):
                if not _is_non_empty_string(value):
                    raise _input_error(
                        f"metadata.lineageEdges[{index}].{key} must be a "
                        "non-empty string"
                    )
            source = (source_dataset, source_field)
            target = (target_dataset, target_field)
            if source not in declared_fields:
                raise _input_error(
                    f"metadata.lineageEdges[{index}].source is not a declared "
                    f"field: {source!r}"
                )
            if target not in declared_fields:
                raise _input_error(
                    f"metadata.lineageEdges[{index}].target is not a declared "
                    f"field: {target!r}"
                )
            column_edges.add((source, target))
        else:
            raise _input_error(
                f"metadata.lineageEdges[{index}] has an unsupported key set: "
                f"{sorted(keys)}"
            )

    return dataset_edges, column_edges


def _validate_change(
    change: Any,
    declared_datasets: Set[str],
    field_types: Dict[FieldId, Any],
) -> Dict[str, Any]:
    """Validate the ``change`` object and return its normalized form.

    Structural checks (object shape, key sets, non-empty strings) come
    first; business checks follow in a fixed total order so no partial
    result is ever produced:

    * empty field set (``INVALID_FIELDS``, delete/type-change only);
    * entry dataset existence (``DATASET_NOT_FOUND``);
    * rename old/new equality (``INVALID_RENAME``);
    * repeated request fields (``DUPLICATE_FIELD``);
    * entry field existence (``FIELD_NOT_FOUND``).
    """
    if not isinstance(change, dict):
        raise _input_error("change must be an object")

    keys = set(change)
    if "dataset" not in keys or "changeType" not in keys:
        missing = sorted({"dataset", "changeType"} - keys)
        raise _input_error(f"change is missing keys: {missing}")

    change_type = change["changeType"]
    if change_type not in _VALID_CHANGE_TYPES:
        raise _input_error(
            f"change.changeType must be one of {list(_VALID_CHANGE_TYPES)}, "
            f"got {change_type!r}"
        )

    dataset = change["dataset"]
    if not _is_non_empty_string(dataset):
        raise _input_error("change.dataset must be a non-empty string")

    if change_type in ("delete", "type_change"):
        if keys != _FIELDS_CHANGE_KEYS:
            missing = sorted(_FIELDS_CHANGE_KEYS - keys)
            if missing:
                raise _input_error(f"change is missing keys: {missing}")
            unknown = sorted(keys - _FIELDS_CHANGE_KEYS)
            raise _input_error(f"change has unsupported keys: {unknown}")
        fields = change["fields"]
        if not isinstance(fields, list):
            raise _input_error("change.fields must be a list of field ids")
        for index, field_name in enumerate(fields):
            if not _is_non_empty_string(field_name):
                raise _input_error(
                    f"change.fields[{index}] must be a non-empty string"
                )
        if not fields:
            raise ChangeImpactInvalidFieldsError(
                "change.fields must not be empty"
            )
        if dataset not in declared_datasets:
            raise ChangeImpactDatasetNotFoundError(
                f"entry dataset is not declared in metadata: {dataset!r}"
            )
        seen: Set[str] = set()
        for field_name in fields:
            if field_name in seen:
                raise ChangeImpactDuplicateFieldError(
                    f"field is repeated in the request: {field_name!r}"
                )
            seen.add(field_name)
        for field_name in fields:
            if (dataset, field_name) not in field_types:
                raise ChangeImpactFieldNotFoundError(
                    "entry field is not declared in metadata: "
                    f"{(dataset, field_name)!r}"
                )
        return {
            "dataset": dataset,
            "changeType": change_type,
            "fields": list(fields),
        }

    if keys != _RENAME_CHANGE_KEYS:
        missing = sorted(_RENAME_CHANGE_KEYS - keys)
        if missing:
            raise _input_error(f"change is missing keys: {missing}")
        unknown = sorted(keys - _RENAME_CHANGE_KEYS)
        raise _input_error(f"change has unsupported keys: {unknown}")
    old_field = change["oldField"]
    new_field = change["newField"]
    if not _is_non_empty_string(old_field):
        raise _input_error("change.oldField must be a non-empty string")
    if not _is_non_empty_string(new_field):
        raise _input_error("change.newField must be a non-empty string")

    if dataset not in declared_datasets:
        raise ChangeImpactDatasetNotFoundError(
            f"entry dataset is not declared in metadata: {dataset!r}"
        )
    if old_field == new_field:
        raise ChangeImpactInvalidRenameError(
            "rename oldField and newField must differ: "
            f"{old_field!r}"
        )
    for field_name in (old_field, new_field):
        if (dataset, field_name) not in field_types:
            raise ChangeImpactFieldNotFoundError(
                "entry field is not declared in metadata: "
                f"{(dataset, field_name)!r}"
            )

    return {
        "dataset": dataset,
        "changeType": change_type,
        "oldField": old_field,
        "newField": new_field,
    }


def _entry_fields(normalized: Dict[str, Any]) -> List[FieldId]:
    """The entry seed fields, in request order (rename seeds old, new)."""
    dataset = normalized["dataset"]
    if normalized["changeType"] == "rename":
        return [
            (dataset, normalized["oldField"]),
            (dataset, normalized["newField"]),
        ]
    return [(dataset, field_name) for field_name in normalized["fields"]]


def _build_field_adjacency(
    column_edges: Set[Tuple[FieldId, FieldId]],
) -> Tuple[Dict[FieldId, List[FieldId]], Dict[FieldId, List[FieldId]]]:
    outgoing_sets: Dict[FieldId, Set[FieldId]] = {}
    incoming_sets: Dict[FieldId, Set[FieldId]] = {}
    for source, target in column_edges:
        outgoing_sets.setdefault(source, set()).add(target)
        incoming_sets.setdefault(target, set()).add(source)
    outgoing = {
        node: sorted(neighbors) for node, neighbors in outgoing_sets.items()
    }
    incoming = {
        node: sorted(neighbors) for node, neighbors in incoming_sets.items()
    }
    return outgoing, incoming


def _build_dataset_adjacency(
    dataset_edges: Set[Tuple[str, str]],
    column_edges: Set[Tuple[FieldId, FieldId]],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Dataset graph: explicit dataset edges plus pairs induced by every
    column-level edge."""
    pairs: Set[Tuple[str, str]] = set(dataset_edges)
    for (source_dataset, _), (target_dataset, _) in column_edges:
        pairs.add((source_dataset, target_dataset))
    outgoing_sets: Dict[str, Set[str]] = {}
    incoming_sets: Dict[str, Set[str]] = {}
    for source, target in pairs:
        outgoing_sets.setdefault(source, set()).add(target)
        incoming_sets.setdefault(target, set()).add(source)
    outgoing = {
        node: sorted(neighbors) for node, neighbors in outgoing_sets.items()
    }
    incoming = {
        node: sorted(neighbors) for node, neighbors in incoming_sets.items()
    }
    return outgoing, incoming


def _lex_shortest_field_paths(
    start: FieldId,
    outgoing: Dict[FieldId, List[FieldId]],
    incoming: Dict[FieldId, List[FieldId]],
) -> Dict[FieldId, Tuple[FieldId, ...]]:
    """Shortest field path from ``start`` to every reachable field.

    BFS fixes the shortest edge count; the lexicographically smallest
    full-name sequence of that length is then a level-by-level dynamic
    program, each field extending the smallest path of a predecessor one
    edge closer.
    """
    dist: Dict[FieldId, int] = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in outgoing.get(current, ()):
            if nxt not in dist:
                dist[nxt] = dist[current] + 1
                queue.append(nxt)

    best: Dict[FieldId, Tuple[FieldId, ...]] = {start: (start,)}
    for level in range(1, max(dist.values(), default=0) + 1):
        for field_id in (node for node, d in dist.items() if d == level):
            candidates = [
                best[parent] + (field_id,)
                for parent in incoming.get(field_id, ())
                if dist.get(parent) == level - 1
            ]
            best[field_id] = min(candidates, key=_name_path)
    return best


def _lex_shortest_dataset_paths(
    start: str,
    outgoing: Dict[str, List[str]],
    incoming: Dict[str, List[str]],
) -> Dict[str, Tuple[str, ...]]:
    """Shortest dataset-id path from ``start`` to every reachable dataset.

    Among equal-length paths the lexicographically smallest node-id
    sequence wins. Cycles cannot trap the BFS (first visit wins) and the
    level dynamic program only extends predecessors one edge closer.
    """
    dist: Dict[str, int] = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in outgoing.get(current, ()):
            if nxt not in dist:
                dist[nxt] = dist[current] + 1
                queue.append(nxt)

    best: Dict[str, Tuple[str, ...]] = {start: (start,)}
    for level in range(1, max(dist.values(), default=0) + 1):
        for dataset in (node for node, d in dist.items() if d == level):
            candidates = [
                best[parent] + (dataset,)
                for parent in incoming.get(dataset, ())
                if dist.get(parent) == level - 1
            ]
            best[dataset] = min(candidates)
    return best


def _field_ref(field_id: FieldId) -> Dict[str, str]:
    return {"dataset": field_id[0], "field": field_id[1]}


def _change_summary(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """Echo the single entry change summary."""
    if normalized["changeType"] == "rename":
        return {
            "dataset": normalized["dataset"],
            "changeType": normalized["changeType"],
            "oldField": normalized["oldField"],
            "newField": normalized["newField"],
        }
    return {
        "dataset": normalized["dataset"],
        "changeType": normalized["changeType"],
        "fields": list(normalized["fields"]),
    }


def analyze_change_impact(payload: Any) -> Dict[str, Any]:
    """Analyze the potential downstream impact of one field change.

    :param payload: object with exactly the keys ``metadata`` and
        ``change``. ``metadata`` has exactly ``datasets`` and
        ``lineageEdges``: ``datasets`` maps non-empty dataset ids to lists
        of field declarations, each a non-empty field id string or an
        object with exactly ``field`` and ``type`` (the raw metadata type
        value is preserved verbatim); ``lineageEdges`` lists directed
        dependency edges from the upstream object to the dependent one,
        either nested (``{"source": {"dataset"}, "target": {"dataset"}}``
        or with ``{"dataset", "field"}`` endpoints) or flat
        (``{"sourceDataset", "targetDataset"}`` or the four-key
        ``sourceField``/``targetField`` shape); the two endpoints of an
        edge must share granularity. ``change`` is
        ``{"dataset", "changeType", "fields"}`` for ``delete`` /
        ``type_change`` or ``{"dataset", "changeType", "oldField",
        "newField"}`` for ``rename``; identifiers are case-sensitive.
    :returns: ``{"status": "ok", "change": <entry summary>,
        "affectedDatasets": [...]}``. Datasets sort by lineage distance
        ascending and then by dataset id; each entry has ``dataset``,
        ``distance``, ``path`` (the stable shortest dataset-id path from
        the entry dataset, ties broken by the lexicographically smallest
        node sequence), ``fieldImpact`` (``"known"`` / ``"unknown"``) and
        ``affectedFields``. Every affected field has ``field``,
        ``sources`` (one ``{"dataset", "field"}`` entry ref per source)
        and ``paths`` (one shortest column-proven field path per source,
        in the same order); a type-change hit only survives a raw target
        type that differs from the source field's raw type. A dataset
        reachable only at dataset level has empty ``affectedFields`` and
        ``fieldImpact == "unknown"``. With no downstream dataset the list
        is empty while the entry summary is retained.
    :raises ValueError: :class:`ChangeImpactInputError` on malformed
        payloads and the five :class:`ChangeImpactError` subclasses with
        codes ``DATASET_NOT_FOUND``, ``FIELD_NOT_FOUND``,
        ``INVALID_RENAME``, ``DUPLICATE_FIELD`` and
        ``INVALID_FIELDS``; a failure never returns a partial result and
        the input is never mutated.
    """
    if not isinstance(payload, dict):
        raise _input_error("payload must be an object")
    keys = set(payload)
    if keys != _PAYLOAD_KEYS:
        missing = sorted(_PAYLOAD_KEYS - keys)
        if missing:
            raise _input_error(f"payload is missing keys: {missing}")
        unknown = sorted(keys - _PAYLOAD_KEYS)
        raise _input_error(f"payload has unsupported keys: {unknown}")

    metadata = payload["metadata"]
    if not isinstance(metadata, dict):
        raise _input_error("metadata must be an object")
    meta_keys = set(metadata)
    if meta_keys != _METADATA_KEYS:
        missing = sorted(_METADATA_KEYS - meta_keys)
        if missing:
            raise _input_error(f"metadata is missing keys: {missing}")
        unknown = sorted(meta_keys - _METADATA_KEYS)
        raise _input_error(f"metadata has unsupported keys: {unknown}")

    declared_datasets, field_types = _validate_datasets(metadata["datasets"])
    dataset_edges, column_edges = _validate_edges(
        metadata["lineageEdges"], declared_datasets, set(field_types)
    )
    normalized = _validate_change(
        payload["change"], declared_datasets, field_types
    )

    field_outgoing, field_incoming = _build_field_adjacency(column_edges)
    dataset_outgoing, dataset_incoming = _build_dataset_adjacency(
        dataset_edges, column_edges
    )

    entry_dataset = normalized["dataset"]
    seeds = _entry_fields(normalized)
    seed_set = set(seeds)
    change_type = normalized["changeType"]

    # hits[target field][entry source] = shortest proven field path.
    # column_reached datasets lie on at least one structural column chain
    # from an entry field, independent of the type-change filter.
    hits: Dict[FieldId, Dict[FieldId, Tuple[FieldId, ...]]] = {}
    column_reached: Set[str] = set()
    for seed in seeds:
        best = _lex_shortest_field_paths(
            seed, field_outgoing, field_incoming
        )
        source_type = field_types.get(seed)
        for target, path in best.items():
            if target in seed_set:
                # An entry field is changing, never an "affected" field;
                # this also discards self-loops and cycles back to a seed.
                continue
            column_reached.add(target[0])
            if change_type == "type_change":
                # Compare raw metadata values verbatim; equal raw types
                # (including two fields without a declared type) prove no
                # type impact along this source chain.
                if field_types.get(target) == source_type:
                    continue
            previous = hits.setdefault(target, {})
            if seed not in previous or _name_path(path) < _name_path(
                previous[seed]
            ):
                previous[seed] = path

    dataset_paths = _lex_shortest_dataset_paths(
        entry_dataset, dataset_outgoing, dataset_incoming
    )

    # A dataset warrants ``unknown`` only when some path from a changed
    # seed field crosses a registered dataset-level edge: leave the
    # column-proven region (entry plus every dataset lying on a structural
    # column chain from a seed) along an explicit dataset edge, then keep
    # going downstream over edges of either kind. A dataset reached
    # exclusively through column chains is not unknown; when its hits were
    # all filtered (equal raw type on a type change) it is simply not
    # affected.
    column_region: Set[str] = {entry_dataset} | set(column_reached)

    unknown_frontier: Set[str] = set()
    for source, target in dataset_edges:
        if source in column_region:
            unknown_frontier.add(target)
    unknown_region: Set[str] = set(unknown_frontier)
    unknown_queue = deque(sorted(unknown_frontier))
    while unknown_queue:
        current = unknown_queue.popleft()
        for nxt in dataset_outgoing.get(current, ()):
            if nxt not in unknown_region:
                unknown_region.add(nxt)
                unknown_queue.append(nxt)

    known_fields: Dict[str, List[Dict[str, Any]]] = {}
    for target in sorted(hits, key=lambda field_id: (field_id[0], field_id[1])):
        target_dataset, target_field = target
        source_paths = hits[target]
        sources_sorted = sorted(source_paths)
        known_fields.setdefault(target_dataset, []).append(
            {
                "field": target_field,
                "sources": [_field_ref(ref) for ref in sources_sorted],
                "paths": [
                    [_field_ref(node) for node in source_paths[ref]]
                    for ref in sources_sorted
                ],
            }
        )

    affected: List[Dict[str, Any]] = []

    # ``known`` datasets: at least one column-proven field hit. The entry
    # dataset itself participates (distance 0) when an intra-dataset edge
    # reaches another of its fields.
    for dataset in sorted(known_fields):
        path = dataset_paths.get(dataset, (dataset,))
        affected.append(
            {
                "dataset": dataset,
                "distance": len(path) - 1,
                "path": list(path),
                "fieldImpact": "known",
                "affectedFields": known_fields[dataset],
            }
        )

    # ``unknown`` datasets: reached only after crossing a genuine
    # dataset-level edge out of the column-proven region, with no
    # column-proven hit of their own. Datasets reached exclusively through
    # column chains (and filtered out, e.g. equal raw types) are not
    # listed at all.
    for dataset, path in dataset_paths.items():
        if len(path) < 2 or dataset in known_fields:
            continue
        if dataset not in unknown_region:
            continue
        affected.append(
            {
                "dataset": dataset,
                "distance": len(path) - 1,
                "path": list(path),
                "fieldImpact": "unknown",
                "affectedFields": [],
            }
        )

    affected.sort(key=lambda item: (item["distance"], item["dataset"]))

    return {
        "status": "ok",
        "change": _change_summary(normalized),
        "affectedDatasets": affected,
    }
