"""Cross-dataset reference integrity validation.

The public entry point is :func:`validate_references`. It takes several
named datasets (each a list of record objects) plus a list of reference
rules. Two rule shapes are supported.

Single-field rules declare that the non-null values of ``source_field``
in ``source_dataset`` must each appear among the ``target_field`` values
of ``target_dataset`` (compared with JSON equality: booleans never equal
numbers, containers compare structurally). They carry exactly the keys
``rule_id``, ``source_dataset``, ``source_field``, ``target_dataset``
and ``target_field``; all of them must be non-empty strings.

Composite rules declare that the combined business key made of
``source_fields`` in ``source_dataset`` must appear among the combined
``target_fields`` of ``target_dataset``. They carry exactly the keys
``rule_id``, ``source_dataset``, ``source_fields``, ``target_dataset``
and ``target_fields``. Both field arrays must be non-empty, equally
long, made of pairwise distinct non-empty strings and correspond
positionally. A single rule must not mix the two shapes and ``rule_id``
must be unique across the whole rule set.

Rules execute in array order and records are scanned in array order. A
source record whose ``source_field`` is missing or ``null`` is skipped
(and not counted as checked); every other source value that has no
JSON-equal target value yields one violation. For composite rules a
source record whose source fields are all missing or ``null`` is
skipped; a record with some (but not all) source fields missing or
``null`` yields an ``"has an incomplete composite reference"``
violation; a complete source key whose value tuple (taken in declared
order) has no JSON-equal tuple among the target records with all target
fields present and non-null yields a ``"has no matching target value"``
violation. Target records with missing or null target fields never
participate in matching.

Malformed dataset/rule structures or non-object records raise
:class:`InvalidReferenceInputError` (a :class:`ValueError`); rules with
missing/extra keys, empty fields, malformed field arrays or duplicated
rule ids raise :class:`InvalidReferenceRuleError` (a
:class:`ValueError`); rules naming an undeclared dataset raise
:class:`UnknownReferenceDatasetError` (a :class:`LookupError`).
"""

from __future__ import annotations

from typing import Any, Dict, List

from .correlation import _json_equal

__all__ = [
    "validate_references",
    "InvalidReferenceInputError",
    "InvalidReferenceRuleError",
    "UnknownReferenceDatasetError",
]

_RULE_KEYS = frozenset(
    {
        "rule_id",
        "source_dataset",
        "source_field",
        "target_dataset",
        "target_field",
    }
)

_COMPOSITE_RULE_KEYS = frozenset(
    {
        "rule_id",
        "source_dataset",
        "source_fields",
        "target_dataset",
        "target_fields",
    }
)

_COMPOSITE_ONLY_KEYS = frozenset({"source_fields", "target_fields"})


class InvalidReferenceInputError(ValueError):
    """The datasets/rules structure or the records are malformed."""

    code = "INVALID_REFERENCE_INPUT"


class InvalidReferenceRuleError(ValueError):
    """A reference rule is malformed or its rule_id is duplicated."""

    code = "INVALID_REFERENCE_RULE"


class UnknownReferenceDatasetError(LookupError):
    """A reference rule names a dataset that is not declared."""

    code = "UNKNOWN_REFERENCE_DATASET"


def _input_error(message: str) -> InvalidReferenceInputError:
    return InvalidReferenceInputError(message)


def _rule_error(message: str) -> InvalidReferenceRuleError:
    return InvalidReferenceRuleError(message)


def _validate_datasets(datasets: Any) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(datasets, dict):
        raise _input_error("datasets must be an object mapping dataset ids "
                           "to record arrays")
    for dataset_id, records in datasets.items():
        if not isinstance(records, list):
            raise _input_error(
                f"dataset {dataset_id!r} must be an array of records"
            )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise _input_error(
                    f"record {index} of dataset {dataset_id!r} "
                    "must be an object"
                )
    return datasets


def _validate_field_array(value: Any, where: str, key: str) -> None:
    if not isinstance(value, list) or not value:
        raise _rule_error(
            f"{where} field {key!r} must be a non-empty array of "
            "field names"
        )
    seen = set()
    for element in value:
        if not isinstance(element, str) or not element:
            raise _rule_error(
                f"{where} field {key!r} must contain only non-empty "
                "strings"
            )
        if element in seen:
            raise _rule_error(
                f"{where} field {key!r} contains a duplicate field "
                f"name: {element!r}"
            )
        seen.add(element)


def _validate_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _input_error("rules must be an array of rule objects")
    seen_rule_ids = set()
    for index, rule in enumerate(rules):
        where = f"rule {index}"
        if not isinstance(rule, dict):
            raise _rule_error(f"{where} must be an object")
        keys = set(rule)
        if keys == _RULE_KEYS:
            expected = _RULE_KEYS
        elif keys == _COMPOSITE_RULE_KEYS:
            expected = _COMPOSITE_RULE_KEYS
        else:
            expected = (
                _COMPOSITE_RULE_KEYS
                if keys & _COMPOSITE_ONLY_KEYS
                else _RULE_KEYS
            )
            missing = sorted(expected - keys)
            if missing:
                raise _rule_error(f"{where} is missing keys: {missing}")
            unknown = sorted(keys - expected)
            raise _rule_error(f"{where} has unsupported keys: {unknown}")
        for field in sorted(expected - _COMPOSITE_ONLY_KEYS):
            value = rule[field]
            if not isinstance(value, str) or not value:
                raise _rule_error(
                    f"{where} field {field!r} must be a non-empty string"
                )
        if expected is _COMPOSITE_RULE_KEYS:
            source_fields = rule["source_fields"]
            target_fields = rule["target_fields"]
            _validate_field_array(source_fields, where, "source_fields")
            _validate_field_array(target_fields, where, "target_fields")
            if len(source_fields) != len(target_fields):
                raise _rule_error(
                    f"{where} fields 'source_fields' and 'target_fields' "
                    "must have the same length"
                )
        rule_id = rule["rule_id"]
        if rule_id in seen_rule_ids:
            raise _rule_error(f"duplicate rule_id: {rule_id!r}")
        seen_rule_ids.add(rule_id)
    return rules


def _check_single_rule(
    rule: Dict[str, Any],
    datasets: Dict[str, List[Dict[str, Any]]],
    violations: List[Dict[str, Any]],
) -> int:
    """Check one single-field rule, returning the checked value count."""
    checked = 0
    target_records = datasets[rule["target_dataset"]]
    target_field = rule["target_field"]
    target_values = [
        record[target_field]
        for record in target_records
        if target_field in record
    ]

    source_field = rule["source_field"]
    for record_index, record in enumerate(datasets[rule["source_dataset"]]):
        if source_field not in record or record[source_field] is None:
            continue
        value = record[source_field]
        checked += 1
        if any(_json_equal(value, target) for target in target_values):
            continue
        violations.append(
            {
                "rule_id": rule["rule_id"],
                "source": {
                    "dataset": rule["source_dataset"],
                    "record_index": record_index,
                    "record_id": record.get("id"),
                    "field": source_field,
                    "value": value,
                },
                "target": {
                    "dataset": rule["target_dataset"],
                    "field": target_field,
                },
                "message": "has no matching target value",
            }
        )
    return checked


def _check_composite_rule(
    rule: Dict[str, Any],
    datasets: Dict[str, List[Dict[str, Any]]],
    violations: List[Dict[str, Any]],
) -> int:
    """Check one composite (multi-field) rule, returning the checked
    value count."""
    checked = 0
    source_fields = rule["source_fields"]
    target_fields = rule["target_fields"]
    target_tuples = [
        [record[field] for field in target_fields]
        for record in datasets[rule["target_dataset"]]
        if all(field in record and record[field] is not None
               for field in target_fields)
    ]

    for record_index, record in enumerate(datasets[rule["source_dataset"]]):
        values = [record.get(field) for field in source_fields]
        if all(value is None for value in values):
            continue
        checked += 1
        if any(value is None for value in values):
            message = "has an incomplete composite reference"
        elif any(
            all(_json_equal(value, target) for value, target in zip(values, candidate))
            for candidate in target_tuples
        ):
            continue
        else:
            message = "has no matching target value"
        violations.append(
            {
                "rule_id": rule["rule_id"],
                "source": {
                    "dataset": rule["source_dataset"],
                    "record_index": record_index,
                    "record_id": record.get("id"),
                    "field": source_fields,
                    "value": values,
                },
                "target": {
                    "dataset": rule["target_dataset"],
                    "field": target_fields,
                },
                "message": message,
            }
        )
    return checked


def validate_references(datasets: Any, rules: Any) -> Dict[str, Any]:
    """Validate cross-dataset reference integrity rules.

    :param datasets: object mapping each dataset id to its array of
        record objects.
    :param rules: array of rule objects. Single-field rules carry
        exactly the keys ``rule_id``, ``source_dataset``,
        ``source_field``, ``target_dataset`` and ``target_field`` (all
        non-empty strings). Composite rules carry exactly the keys
        ``rule_id``, ``source_dataset``, ``source_fields``,
        ``target_dataset`` and ``target_fields`` where both field
        arrays are non-empty, equally long and made of pairwise
        distinct non-empty strings. ``rule_id`` is unique across the
        whole rule set and the two shapes must not be mixed. Rules
        execute in array order.
    :returns: ``{"passed": bool, "summary": {...}, "violations": [...]}``.
        The summary carries ``dataset_count``, ``rule_count``,
        ``checked_value_count`` (source records actually checked: those
        with a non-null ``source_field``, resp. at least one non-null
        source field) and ``violation_count``. Each violation carries
        exactly ``rule_id``, ``source``, ``target`` and ``message``;
        ``source`` is ``{"dataset", "record_index", "record_id",
        "field", "value"}`` with ``record_index`` counting from 0 and
        ``record_id`` taken from the record's ``id`` (``null`` when
        absent), ``target`` is ``{"dataset", "field"}``. Single-field
        violations report the string field and scalar value and the
        message ``"has no matching target value"``; composite
        violations report the field arrays and the source values in
        declared order (missing entries as ``null``) with message
        ``"has an incomplete composite reference"`` or ``"has no
        matching target value"``.
    :raises ValueError: :class:`InvalidReferenceInputError` on malformed
        datasets/rules structures or non-object records,
        :class:`InvalidReferenceRuleError` on rules with missing/extra
        keys, empty fields, malformed field arrays or duplicated rule
        ids.
    :raises LookupError: :class:`UnknownReferenceDatasetError` when a
        rule names a dataset that is not declared.
    """
    datasets = _validate_datasets(datasets)
    rules = _validate_rules(rules)

    for rule in rules:
        for role in ("source_dataset", "target_dataset"):
            if rule[role] not in datasets:
                raise UnknownReferenceDatasetError(
                    f"rule {rule['rule_id']!r} references an unknown "
                    f"dataset: {rule[role]!r}"
                )

    checked_value_count = 0
    violations: List[Dict[str, Any]] = []
    for rule in rules:
        if "source_fields" in rule:
            checked_value_count += _check_composite_rule(
                rule, datasets, violations
            )
        else:
            checked_value_count += _check_single_rule(
                rule, datasets, violations
            )

    return {
        "passed": not violations,
        "summary": {
            "dataset_count": len(datasets),
            "rule_count": len(rules),
            "checked_value_count": checked_value_count,
            "violation_count": len(violations),
        },
        "violations": violations,
    }
