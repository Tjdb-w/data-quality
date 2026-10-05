"""Cross-dataset reference integrity validation.

The public entry point is :func:`validate_references`. It takes several
named datasets (each a list of record objects) plus a list of reference
rules. Two rule shapes are supported:

* Single-field rules declare that the non-null values of
  ``source_field`` in ``source_dataset`` must each appear among the
  ``target_field`` values of ``target_dataset`` (compared with JSON
  equality: booleans never equal numbers, containers compare
  structurally).
* Composite rules declare ``source_fields`` / ``target_fields`` instead:
  equally long, non-empty arrays of mutually distinct non-empty field
  names, paired by position. The value tuple built from the source
  fields (in declared order) must appear among the value tuples of the
  target fields. A source record whose composite key is entirely
  missing/``null`` is skipped; a partially missing/``null`` key yields
  an ``"has an incomplete composite reference"`` violation. Target
  records with any missing/``null`` target field never match.

Single-field rules carry exactly the keys ``rule_id``,
``source_dataset``, ``source_field``, ``target_dataset`` and
``target_field``; composite rules carry exactly ``rule_id``,
``source_dataset``, ``source_fields``, ``target_dataset`` and
``target_fields``. The two shapes must not be mixed in one rule and
``rule_id`` must be unique across the whole rule set. Rules execute in
array order and records are scanned in array order. A source record
whose ``source_field`` is missing or ``null`` is skipped (and not
counted as checked); every other source value that has no JSON-equal
target value yields one violation.

Malformed dataset/rule structures or non-object records raise
:class:`InvalidReferenceInputError` (a :class:`ValueError`); rules with
missing/extra keys, mixed shapes, empty fields, malformed field arrays
or duplicated rule ids raise :class:`InvalidReferenceRuleError` (a
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
    for item in value:
        if not isinstance(item, str) or not item:
            raise _rule_error(
                f"{where} field {key!r} must contain only non-empty "
                "strings"
            )
        if item in seen:
            raise _rule_error(
                f"{where} field {key!r} has a duplicate entry: {item!r}"
            )
        seen.add(item)


def _validate_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _input_error("rules must be an array of rule objects")
    seen_rule_ids = set()
    for index, rule in enumerate(rules):
        where = f"rule {index}"
        if not isinstance(rule, dict):
            raise _rule_error(f"{where} must be an object")
        keys = set(rule)
        composite = "source_fields" in keys or "target_fields" in keys
        expected = _COMPOSITE_RULE_KEYS if composite else _RULE_KEYS
        if keys != expected:
            missing = sorted(expected - keys)
            if missing:
                raise _rule_error(f"{where} is missing keys: {missing}")
            unknown = sorted(keys - expected)
            raise _rule_error(f"{where} has unsupported keys: {unknown}")
        for field in ("rule_id", "source_dataset", "target_dataset"):
            value = rule[field]
            if not isinstance(value, str) or not value:
                raise _rule_error(
                    f"{where} field {field!r} must be a non-empty string"
                )
        if composite:
            for key in ("source_fields", "target_fields"):
                _validate_field_array(rule[key], where, key)
            if len(rule["source_fields"]) != len(rule["target_fields"]):
                raise _rule_error(
                    f"{where} fields 'source_fields' and 'target_fields' "
                    "must have the same length"
                )
        else:
            for field in ("source_field", "target_field"):
                value = rule[field]
                if not isinstance(value, str) or not value:
                    raise _rule_error(
                        f"{where} field {field!r} must be a non-empty "
                        "string"
                    )
        rule_id = rule["rule_id"]
        if rule_id in seen_rule_ids:
            raise _rule_error(f"duplicate rule_id: {rule_id!r}")
        seen_rule_ids.add(rule_id)
    return rules


def validate_references(datasets: Any, rules: Any) -> Dict[str, Any]:
    """Validate cross-dataset reference integrity rules.

    :param datasets: object mapping each dataset id to its array of
        record objects.
    :param rules: array of rule objects. Single-field rules carry
        exactly the keys ``rule_id``, ``source_dataset``,
        ``source_field``, ``target_dataset`` and ``target_field`` (all
        non-empty strings); composite rules carry exactly ``rule_id``,
        ``source_dataset``, ``source_fields``, ``target_dataset`` and
        ``target_fields`` where the field arrays are equally long,
        non-empty arrays of mutually distinct non-empty strings paired
        by position. ``rule_id`` is unique across the whole rule set and
        rules execute in array order.
    :returns: ``{"passed": bool, "summary": {...}, "violations": [...]}``.
        The summary carries ``dataset_count``, ``rule_count``,
        ``checked_value_count`` (non-null source values actually
        checked; composite rules count each source record with at least
        one non-null source field once) and ``violation_count``. Each
        violation carries exactly ``rule_id``, ``source``, ``target``
        and ``message``; ``source`` is ``{"dataset", "record_index",
        "record_id", "field", "value"}`` with ``record_index`` counting
        from 0 and ``record_id`` taken from the record's ``id``
        (``null`` when absent), ``target`` is ``{"dataset", "field"}``.
        Single-field violations report the string ``field`` and the
        scalar ``value`` with message ``"has no matching target
        value"``; composite violations report the ``source_fields`` /
        ``target_fields`` arrays as ``field`` and the declared-order
        value list (missing entries filled with ``null``) as ``value``,
        with message ``"has an incomplete composite reference"`` for
        partially missing/``null`` source keys and ``"has no matching
        target value"`` when no target record matches the complete key.
    :raises ValueError: :class:`InvalidReferenceInputError` on malformed
        datasets/rules structures or non-object records,
        :class:`InvalidReferenceRuleError` on rules with missing/extra
        keys, mixed shapes, empty fields, malformed field arrays or
        duplicated rule ids.
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
            checked_value_count += _run_composite_rule(
                datasets, rule, violations
            )
        else:
            checked_value_count += _run_single_field_rule(
                datasets, rule, violations
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


def _run_single_field_rule(
    datasets: Dict[str, List[Dict[str, Any]]],
    rule: Dict[str, Any],
    violations: List[Dict[str, Any]],
) -> int:
    target_records = datasets[rule["target_dataset"]]
    target_field = rule["target_field"]
    target_values = [
        record[target_field]
        for record in target_records
        if target_field in record
    ]

    checked_value_count = 0
    source_field = rule["source_field"]
    for record_index, record in enumerate(datasets[rule["source_dataset"]]):
        if source_field not in record or record[source_field] is None:
            continue
        value = record[source_field]
        checked_value_count += 1
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
    return checked_value_count


def _run_composite_rule(
    datasets: Dict[str, List[Dict[str, Any]]],
    rule: Dict[str, Any],
    violations: List[Dict[str, Any]],
) -> int:
    source_fields = rule["source_fields"]
    target_fields = rule["target_fields"]
    target_keys = [
        [record[field] for field in target_fields]
        for record in datasets[rule["target_dataset"]]
        if all(
            field in record and record[field] is not None
            for field in target_fields
        )
    ]

    checked_value_count = 0
    for record_index, record in enumerate(datasets[rule["source_dataset"]]):
        values = [record.get(field) for field in source_fields]
        if all(value is None for value in values):
            continue
        checked_value_count += 1
        if any(value is None for value in values):
            message = "has an incomplete composite reference"
        elif any(
            all(_json_equal(value, target) for value, target in zip(values, key))
            for key in target_keys
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
    return checked_value_count
