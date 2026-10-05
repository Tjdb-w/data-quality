"""Origin and impact evidence analysis for violated check results.

The public entry point is :func:`analyze_violation_origins`. It reuses the
check-result batch and the typed field lineage graph of
:func:`data_quality.correlate_violations`, then answers, per queried target
``(dataset_id, field_id, sample_id)`` triple, two questions:

* **origin evidence** -- violations on the same sample at the target field
  itself or at fields upstream of it, each paired with the shortest field
  sequence running forward from the evidence field to the target;
* **impact evidence** -- violations on the same sample at fields reachable
  downstream of the target along forward edges, each paired with the
  shortest field sequence running forward from the target to the evidence
  field.

A target triple must match a ``violated: true`` check result; such a result
is always its own length-0 origin evidence. A self-loop never presents the
target as its own impact evidence.

Malformed check results, lineage graphs or targets raise
:class:`InvalidOriginInputError` (a :class:`ValueError`); check results
referencing datasets or fields not declared in the lineage graph raise
:class:`UnknownOriginReferenceError`, and a target triple without a
matching violated result raises :class:`UnknownOriginTargetError` (both
:class:`LookupError`).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set, Tuple

from . import correlation as _correlation

__all__ = [
    "analyze_violation_origins",
    "InvalidOriginInputError",
    "UnknownOriginReferenceError",
    "UnknownOriginTargetError",
]

_TARGET_KEYS = frozenset({"dataset_id", "field_id", "sample_id"})

# A field is identified by its (dataset, field) pair.
FieldId = Tuple[str, str]
# A target is identified by the (dataset, field, sample) triple.
TargetId = Tuple[str, str, str]


class InvalidOriginInputError(ValueError):
    """The check results, lineage graph or targets are malformed."""

    code = "INVALID_ORIGIN_INPUT"


class UnknownOriginReferenceError(LookupError):
    """A check result references an undeclared dataset or field."""

    code = "UNKNOWN_ORIGIN_REFERENCE"


class UnknownOriginTargetError(LookupError):
    """A target triple has no matching violated check result."""

    code = "UNKNOWN_ORIGIN_TARGET"


def _input_error(message: str) -> InvalidOriginInputError:
    return InvalidOriginInputError(message)


def _full_name(field_id: FieldId) -> str:
    return f"{field_id[0]}.{field_id[1]}"


def _name_path(path: Tuple[FieldId, ...]) -> Tuple[str, ...]:
    """Ordering view of a field sequence: full names ``dataset.field``."""
    return tuple(_full_name(node) for node in path)


def _field_path_refs(path: Tuple[FieldId, ...]) -> List[Dict[str, str]]:
    return [{"dataset_id": node[0], "field_id": node[1]} for node in path]


def _validate_targets(targets: Any) -> List[TargetId]:
    """Validate the target list, keeping its original order with
    duplicates, since each report corresponds to a target one-to-one."""
    if not isinstance(targets, list):
        raise _input_error("targets must be a list")

    ordered: List[TargetId] = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise _input_error(f"targets[{index}] must be an object")
        keys = set(target)
        if keys != _TARGET_KEYS:
            missing = sorted(_TARGET_KEYS - keys)
            if missing:
                raise _input_error(
                    f"targets[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _TARGET_KEYS)
            raise _input_error(
                f"targets[{index}] has unsupported keys: {unknown}"
            )
        for key in ("dataset_id", "field_id", "sample_id"):
            if not isinstance(target[key], str) or not target[key]:
                raise _input_error(
                    f"targets[{index}].{key} must be a non-empty string"
                )
        ordered.append(
            (target["dataset_id"], target["field_id"], target["sample_id"])
        )
    return ordered


def _bfs_distances(
    start: FieldId,
    neighbors: Dict[FieldId, Set[FieldId]],
) -> Dict[FieldId, int]:
    """Shortest edge distance from ``start`` to every reachable field."""
    distances = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in neighbors.get(current, ()):
            if nxt not in distances:
                distances[nxt] = distances[current] + 1
                queue.append(nxt)
    return distances


def _downstream_paths(
    start: FieldId,
    outgoing: Dict[FieldId, Set[FieldId]],
) -> Dict[FieldId, Tuple[FieldId, ...]]:
    """Shortest forward field sequence from ``start`` to every field
    reachable along one or more forward edges; ties on edge count break by
    the lexicographically smallest full-name field sequence.
    """
    distances = _bfs_distances(start, outgoing)

    # Predecessors let every field extend a route one edge shorter.
    incoming: Dict[FieldId, List[FieldId]] = {}
    for source, targets in outgoing.items():
        for target in targets:
            incoming.setdefault(target, []).append(source)

    best: Dict[FieldId, Tuple[FieldId, ...]] = {start: (start,)}
    max_depth = max(distances.values(), default=0)
    for depth in range(1, max_depth + 1):
        for field_id in (node for node, dist in distances.items() if dist == depth):
            candidates = [
                best[predecessor] + (field_id,)
                for predecessor in incoming.get(field_id, ())
                if distances.get(predecessor) == depth - 1
            ]
            best[field_id] = min(candidates, key=_name_path)

    return {node: best[node] for node in distances if node != start}


def _upstream_paths(
    start: FieldId,
    incoming: Dict[FieldId, Set[FieldId]],
    outgoing: Dict[FieldId, Set[FieldId]],
) -> Dict[FieldId, Tuple[FieldId, ...]]:
    """Shortest forward field sequence running from every upstream field
    to ``start``.

    Distances come from a reverse BFS (so ``distance[u]`` is the number of
    forward edges from ``u`` to ``start``); the lexicographically smallest
    *forward* sequence of that length is a dynamic program by increasing
    distance, each field prepending the smallest sequence of a forward
    successor one edge closer to ``start``.
    """
    distances = _bfs_distances(start, incoming)

    best: Dict[FieldId, Tuple[FieldId, ...]] = {start: (start,)}
    max_depth = max(distances.values(), default=0)
    for depth in range(1, max_depth + 1):
        for field_id in (node for node, dist in distances.items() if dist == depth):
            candidates = [
                (field_id,) + best[successor]
                for successor in outgoing.get(field_id, ())
                if distances.get(successor) == depth - 1
            ]
            best[field_id] = min(candidates, key=_name_path)

    return {node: best[node] for node in distances if node != start}


def _evidence(record: Dict[str, Any], path: Tuple[FieldId, ...]) -> Dict[str, Any]:
    return {
        "rule_id": record["rule_id"],
        "dataset_id": record["dataset_id"],
        "field_id": record["field_id"],
        "sample_id": record["sample_id"],
        "violated": record["violated"],
        "value": record["value"],
        "path": _field_path_refs(path),
    }


def _evidence_order(evidence: Dict[str, Any]) -> Tuple[int, str, str, str]:
    """Sort view: path edge count, then field, then rule id."""
    return (
        len(evidence["path"]) - 1,
        evidence["dataset_id"],
        evidence["field_id"],
        evidence["rule_id"],
    )


def analyze_violation_origins(
    results: Any, lineage: Any, targets: Any
) -> Dict[str, Any]:
    """Analyze origin and impact evidence behind violated target results.

    :param results: list of check result objects in exactly the shape
        accepted by :func:`correlate_violations`: each has exactly the keys
        ``rule_id``, ``dataset_id``, ``field_id``, ``sample_id``
        (non-empty strings), ``violated`` (boolean) and ``value`` (any
        JSON value). Identical duplicate results are kept once;
        conflicting repetitions are rejected.
    :param lineage: typed field lineage graph in exactly the shape accepted
        by :func:`correlate_violations` (``datasets`` / ``fields`` /
        ``edges`` with ``"upstream"`` / ``"downstream"`` edge types).
    :param targets: list of target objects, each with exactly the keys
        ``dataset_id``, ``field_id`` and ``sample_id`` (non-empty strings).
        One report is produced per target in list order, duplicates
        included.
    :returns: ``{"reports": [...]}``; an empty ``targets`` list yields
        ``{"reports": []}``. Each report is ``{"target", "originEvidence",
        "impactEvidence"}``. ``target`` echoes the target triple.
        ``originEvidence`` holds the target's own violation (a length-0
        path) plus violations on the same sample at upstream fields, each
        ``path`` running forward from the evidence field to the target.
        ``impactEvidence`` holds violations on the same sample at fields
        reachable from the target along forward edges (never the target
        itself, so a self-loop does not self-impact), each ``path``
        running forward from the target to the evidence field. Every
        evidence object carries ``rule_id``, ``dataset_id``,
        ``field_id``, ``sample_id``, ``violated``, ``value`` and ``path``
        (a list of ``{"dataset_id", "field_id"}`` steps). Paths minimize
        edge count and then the lexicographic full-name field sequence;
        evidence sorts by ``(path edge count, dataset_id, field_id,
        rule_id)``.
    :raises ValueError: :class:`InvalidOriginInputError` on malformed
        results, lineage or targets.
    :raises LookupError: :class:`UnknownOriginReferenceError` when a check
        result references an undeclared dataset or field, and
        :class:`UnknownOriginTargetError` when a target triple has no
        ``violated: true`` result.
    """
    # Structural validation (lineage, results, targets) precedes reference
    # resolution, which precedes target existence -- no partial results. The
    # reused correlate validators raise correlation-specific exceptions, so
    # translate them into the origin equivalents while keeping messages.
    try:
        (
            declared_datasets,
            declared_fields,
            edges,
        ) = _correlation._validate_lineage(lineage)
        records = _correlation._validate_results(results)
    except _correlation.InvalidCorrelationInputError as exc:
        raise _input_error(str(exc)) from exc

    ordered_targets = _validate_targets(targets)

    try:
        _correlation._check_references(
            records, declared_datasets, declared_fields
        )
    except _correlation.UnknownCorrelationReferenceError as exc:
        raise UnknownOriginReferenceError(str(exc)) from exc

    # Canonical edges are (upstream field, downstream field); build both
    # forward (downstream) and reverse (upstream) adjacency.
    incoming: Dict[FieldId, Set[FieldId]] = {}
    outgoing: Dict[FieldId, Set[FieldId]] = {}
    for upstream, downstream in edges:
        outgoing.setdefault(upstream, set()).add(downstream)
        incoming.setdefault(downstream, set()).add(upstream)

    violated_by_triple: Dict[TargetId, List[Dict[str, Any]]] = {}
    for record in records:
        if record["violated"]:
            key = (
                record["dataset_id"],
                record["field_id"],
                record["sample_id"],
            )
            violated_by_triple.setdefault(key, []).append(record)

    reports: List[Dict[str, Any]] = []
    for dataset_id, field_id, sample_id in ordered_targets:
        triple = (dataset_id, field_id, sample_id)
        if triple not in violated_by_triple:
            raise UnknownOriginTargetError(
                f"no violated result for target "
                f"dataset_id={dataset_id!r}, field_id={field_id!r}, "
                f"sample_id={sample_id!r}"
            )

        target_field: FieldId = (dataset_id, field_id)
        origins_to_target = _upstream_paths(
            target_field, incoming, outgoing
        )
        impacts_from_target = _downstream_paths(target_field, outgoing)

        # Same-sample violations indexed by field.
        violated_at_field: Dict[FieldId, List[Dict[str, Any]]] = {}
        for record in records:
            if record["violated"] and record["sample_id"] == sample_id:
                violated_at_field.setdefault(
                    (record["dataset_id"], record["field_id"]), []
                ).append(record)

        origin_evidence: List[Dict[str, Any]] = []
        # The target's own violation is its length-0 origin evidence.
        for record in violated_at_field.get(target_field, []):
            origin_evidence.append(_evidence(record, (target_field,)))
        for evidence_field, path in origins_to_target.items():
            for record in violated_at_field.get(evidence_field, []):
                origin_evidence.append(_evidence(record, path))

        impact_evidence: List[Dict[str, Any]] = []
        for evidence_field, path in impacts_from_target.items():
            for record in violated_at_field.get(evidence_field, []):
                impact_evidence.append(_evidence(record, path))

        origin_evidence.sort(key=_evidence_order)
        impact_evidence.sort(key=_evidence_order)

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
