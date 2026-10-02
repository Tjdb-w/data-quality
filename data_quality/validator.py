"""Core validation logic for the data_quality package.

The public entry point is :func:`validate`. It validates a list of JSON
object records against a list of rules and returns a result dictionary with
``passed`` / ``summary`` / ``violations``.

Structural problems raise :class:`InvalidInputError` and rule configuration
problems raise :class:`InvalidRuleError`; both subclass :class:`ValueError`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

__all__ = [
    "validate",
    "DataQualityError",
    "InvalidInputError",
    "InvalidRuleError",
]

SUPPORTED_TYPES = ("required", "unique", "range", "regex", "allowed_values")

MESSAGES = {
    "required": "is required but is missing or null",
    "unique": "is a duplicate value",
    "range": "is out of the allowed range",
    "regex": "does not match the required pattern",
    "allowed_values": "is not in the list of allowed values",
}


class DataQualityError(ValueError):
    """Base class for validation errors. Carries an CLI error ``code``."""

    code = "INVALID_INPUT"


class InvalidInputError(DataQualityError):
    """The records argument (or the enclosing payload) is malformed."""

    code = "INVALID_INPUT"


class InvalidRuleError(DataQualityError):
    """A rule is malformed, unsupported or has conflicting options."""

    code = "INVALID_RULE"


def _is_number(value: Any) -> bool:
    # bool is a subclass of int, but JSON true/false are not numbers.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def _rule_error(message: str) -> InvalidRuleError:
    return InvalidRuleError(message)


def _validate_and_compile_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _rule_error("rules must be a list")

    compiled: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise _rule_error(f"rules[{index}] must be an object")

        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise _rule_error(f"rules[{index}] must have a non-empty string id")
        if rule_id in seen_ids:
            raise _rule_error(f"duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        rule_type = rule.get("type")
        if not isinstance(rule_type, str):
            raise _rule_error(f"rule {rule_id!r} must have a string type")
        if rule_type not in SUPPORTED_TYPES:
            raise _rule_error(
                f"rule {rule_id!r} has unsupported type: {rule_type!r}"
            )

        options = rule.get("options")
        if not isinstance(options, dict):
            raise _rule_error(f"rule {rule_id!r} must have an options object")

        field = options.get("field")
        if not isinstance(field, str):
            raise _rule_error(
                f"rule {rule_id!r}: options.field must be a string"
            )

        allowed_keys = {"field"}
        compiled_rule: Dict[str, Any] = {
            "id": rule_id,
            "type": rule_type,
            "field": field,
        }

        if rule_type == "range":
            allowed_keys |= {"min", "max"}
            minimum = options.get("min")
            maximum = options.get("max")
            if not _is_number(minimum):
                raise _rule_error(
                    f"rule {rule_id!r}: options.min must be a number"
                )
            if not _is_number(maximum):
                raise _rule_error(
                    f"rule {rule_id!r}: options.max must be a number"
                )
            if minimum > maximum:
                raise _rule_error(
                    f"rule {rule_id!r}: options.min ({minimum}) must not be "
                    f"greater than options.max ({maximum})"
                )
            compiled_rule["min"] = minimum
            compiled_rule["max"] = maximum

        elif rule_type == "regex":
            allowed_keys.add("pattern")
            pattern = options.get("pattern")
            if not isinstance(pattern, str):
                raise _rule_error(
                    f"rule {rule_id!r}: options.pattern must be a string"
                )
            try:
                compiled_rule["pattern"] = re.compile(pattern)
            except re.error as exc:
                raise _rule_error(
                    f"rule {rule_id!r}: options.pattern is not a valid "
                    f"regular expression: {exc}"
                ) from exc

        elif rule_type == "allowed_values":
            allowed_keys.add("values")
            values = options.get("values")
            if not isinstance(values, list):
                raise _rule_error(
                    f"rule {rule_id!r}: options.values must be a list"
                )
            compiled_rule["values"] = values

        unknown = set(options) - allowed_keys
        if unknown:
            raise _rule_error(
                f"rule {rule_id!r}: unsupported options for type "
                f"{rule_type!r}: {sorted(unknown)}"
            )

        compiled.append(compiled_rule)

    return compiled


def _violation(rule: Dict[str, Any], index: int, record: Dict[str, Any], value: Any):
    record_id = record["id"] if "id" in record else None
    return {
        "rule_id": rule["id"],
        "record_index": index,
        "record_id": record_id,
        "field": rule["field"],
        "value": value,
        "message": MESSAGES[rule["type"]],
    }


def _check_rule(
    rule: Dict[str, Any],
    records: List[Dict[str, Any]],
    violations: List[Dict[str, Any]],
) -> None:
    rule_type = rule["type"]
    field = rule["field"]

    if rule_type == "unique":
        seen: List[Any] = []

    for index, record in enumerate(records):
        present = field in record
        value = record.get(field)

        if rule_type == "required":
            if not present or value is None:
                violations.append(_violation(rule, index, record, value))

        elif rule_type == "unique":
            # Null values are excluded from uniqueness checks.
            if value is not None:
                if any(_json_equal(value, earlier) for earlier in seen):
                    violations.append(_violation(rule, index, record, value))
                else:
                    seen.append(value)

        elif rule_type == "range":
            if not _is_number(value) or not (
                rule["min"] <= value <= rule["max"]
            ):
                violations.append(_violation(rule, index, record, value))

        elif rule_type == "regex":
            if not isinstance(value, str) or rule["pattern"].fullmatch(value) is None:
                violations.append(_violation(rule, index, record, value))

        elif rule_type == "allowed_values":
            checked = value if present else None
            if not any(_json_equal(checked, allowed) for allowed in rule["values"]):
                violations.append(_violation(rule, index, record, checked))


def validate(records: Any, rules: Any) -> Dict[str, Any]:
    """Validate ``records`` against ``rules``.

    Rules are fully validated before any record is examined. Rules are then
    evaluated in their given order; within a rule, records are checked in
    order.

    :param records: list of JSON objects (plain ``dict`` instances).
    :param rules: list of rule objects with ``id``, ``type`` and ``options``.
    :returns: ``{"passed": bool, "summary": {...}, "violations": [...]}``.
    :raises ValueError: on malformed input or rule configuration.
    """
    compiled_rules = _validate_and_compile_rules(rules)

    if not isinstance(records, list):
        raise InvalidInputError("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InvalidInputError(f"records[{index}] must be a JSON object")

    violations: List[Dict[str, Any]] = []
    for rule in compiled_rules:
        _check_rule(rule, records, violations)

    return {
        "passed": not violations,
        "summary": {
            "record_count": len(records),
            "violation_count": len(violations),
            "checked_rule_count": len(compiled_rules),
        },
        "violations": violations,
    }
