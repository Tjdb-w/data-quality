"""Cross-dataset reference integrity validation.

The public entry point is :func:`validate_references`. It checks foreign-key
style references from fields in one dataset against values in another dataset:
each non-null source value must have a JSON-equal counterpart among the
target field values, otherwise a violation is reported.

``datasets`` maps a dataset id to its list of JSON object records and
``rules`` is an ordered list of
``{rule_id, source_dataset, source_field, target_dataset, target_field}``
rule objects. Rules are fully validated before any value is compared and
are then evaluated in their given order; within a rule, the source records
are checked in order.

Structural problems with ``datasets`` / ``rules`` or a non-object record
raise :class:`InvalidReferenceInputError`; a rule with missing/extra keys,
empty fields or a duplicate ``rule_id`` raises
:class:`InvalidReferenceRuleError`; a rule referencing a dataset not present
in ``datasets`` raises :class:`UnknownReferenceDatasetError`.
"""

from __future__ import annotations

from typing import Any, Dict, List

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

_MESSAGE = "has no matching target value"


class InvalidReferenceInputError(ValueError):
    """The datasets argument or the records inside it are malformed."""

    code = "INVALID_REFERENCE_INPUT"


class InvalidReferenceRuleError(ValueError):
    """A reference rule is malformed or shares its rule_id with another."""

    code = "INVALID_REFERENCE_RULE"


class UnknownReferenceDatasetError(LookupError):
    """A rule references a dataset id not present in ``datasets``."""

    code = "UNKNOWN_REFERENCE_DATASET"


def _input_error(message: str) -> InvalidReferenceInputError:
    return InvalidReferenceInputError(message)


def _rule_error(message: str) -> InvalidReferenceRuleError:
    return InvalidReferenceRuleError(message)


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


def _validate_datasets(datasets: Any) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(datasets, dict):
        raise _input_error("datasets must be an object")
    for dataset_id, records in datasets.items():
        if not isinstance(dataset_id, str) or not dataset_id:
            raise _input_error(
                f"datasets key must be a non-empty dataset id string: "
                f"{dataset_id!r}"
            )
        if not isinstance(records, list):
            raise _input_error(
                f"datasets[{dataset_id!r}] must be a list of records"
            )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise _input_error(
                    f"datasets[{dataset_id!r}][{index}] must be a JSON object"
                )
    return datasets


def _validate_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _rule_error("rules must be a list")

    compiled: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise _rule_error(f"rules[{index}] must be an object")

        keys = set(rule)
        if keys != _RULE_KEYS:
            missing = sorted(_RULE_KEYS - keys)
            if missing:
                raise _rule_error(
                    f"rules[{index}] is missing keys: {missing}"
                )
            unknown = sorted(keys - _RULE_KEYS)
            raise _rule_error(
                f"rules[{index}] has unsupported keys: {unknown}"
            )

        rule_id = rule["rule_id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise _rule_error(
                f"rules[{index}].rule_id must be a non-empty string"
            )
        if rule_id in seen_ids:
            raise _rule_error(f"duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        for key in (
            "source_dataset",
            "source_field",
            "target_dataset",
            "target_field",
        ):
            value = rule[key]
            if not isinstance(value, str) or not value:
                raise _rule_error(
                    f"rule {rule_id!r}: {key} must be a non-empty string"
                )

        compiled.append(rule)

    return compiled


def _check_rule_datasets(
    rules: List[Dict[str, Any]], datasets: Dict[str, List[Dict[str, Any]]]
) -> None:
    for rule in rules:
        source = rule["source_dataset"]
        if source not in datasets:
            raise UnknownReferenceDatasetError(
                f"unknown source dataset id: {source!r}"
            )
        target = rule["target_dataset"]
        if target not in datasets:
            raise UnknownReferenceDatasetError(
                f"unknown target dataset id: {target!r}"
            )


def _violation(
    rule: Dict[str, Any],
    index: int,
    record: Dict[str, Any],
    value: Any,
) -> Dict[str, Any]:
    record_id = record["id"] if "id" in record else None
    return {
        "rule_id": rule["rule_id"],
        "source": {
            "dataset": rule["source_dataset"],
            "record_index": index,
            "record_id": record_id,
            "field": rule["source_field"],
            "value": value,
        },
        "target": {
            "dataset": rule["target_dataset"],
            "field": rule["target_field"],
        },
        "message": _MESSAGE,
    }


def validate_references(datasets: Any, rules: Any) -> Dict[str, Any]:
    """Validate cross-dataset references from source fields to target fields.

    Rules are fully validated (and their dataset references resolved)
    before any source value is compared. Rules are then evaluated in their
    given order; within a rule the source records are checked in order.

    For each source record the check skips records whose ``source_field``
    is missing or ``null``; every other source value must match at least
    one target record's ``target_field`` value under JSON equality
    (booleans never equal numbers, objects compare by key/value, arrays
    order-sensitively). An unmatched source value is a violation.

    :param datasets: object mapping dataset ids to lists of JSON object
        records.
    :param rules: list of rule objects, each containing exactly the keys
        ``rule_id``, ``source_dataset``, ``source_field``,
        ``target_dataset`` and ``target_field`` (all non-empty strings).
        ``rule_id`` must be unique within the list.
    :returns: ``{"passed": bool, "summary": {"dataset_count",
        "rule_count", "checked_value_count", "violation_count"},
        "violations": [...]}``. Each violation has exactly the keys
        ``rule_id``, ``source`` (``dataset``, ``record_index`` starting at
        0, ``record_id`` which is the record's ``id`` or ``null``,
        ``field`` and the original ``value``), ``target`` (``dataset`` and
        ``field``) and ``message`` ("has no matching target value").
        ``checked_value_count`` counts the non-null source values that
        were compared.
    :raises ValueError: :class:`InvalidReferenceInputError` on malformed
        ``datasets`` or non-object records and
        :class:`InvalidReferenceRuleError` on malformed rules or duplicate
        rule ids.
    :raises LookupError: :class:`UnknownReferenceDatasetError` when a rule
        references a dataset absent from ``datasets``.
    """
    validated_datasets = _validate_datasets(datasets)
    compiled_rules = _validate_rules(rules)
    _check_rule_datasets(compiled_rules, validated_datasets)

    violations: List[Dict[str, Any]] = []
    checked_value_count = 0

    for rule in compiled_rules:
        source_field = rule["source_field"]
        target_field = rule["target_field"]
        target_records = validated_datasets[rule["target_dataset"]]
        target_values = [
            record[target_field]
            for record in target_records
            if target_field in record
        ]

        for index, record in enumerate(
            validated_datasets[rule["source_dataset"]]
        ):
            if source_field not in record:
                continue
            value = record[source_field]
            if value is None:
                continue
            checked_value_count += 1
            if not any(_json_equal(value, target) for target in target_values):
                violations.append(_violation(rule, index, record, value))

    return {
        "passed": not violations,
        "summary": {
            "dataset_count": len(validated_datasets),
            "rule_count": len(compiled_rules),
            "checked_value_count": checked_value_count,
            "violation_count": len(violations),
        },
        "violations": violations,
    }
