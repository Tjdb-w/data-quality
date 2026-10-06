"""Batch-level cross-record validation.

The public entry point is :func:`evaluate_batch_rules`. It validates a
list of JSON object records against a list of batch rules and returns an
in-memory report with ``passed`` / ``summary`` / ``violations``. Records
are never modified, nothing is written to disk and no external service
is contacted.

Two rule types are supported:

* ``unique_key`` (``options.fields``): the declared, mutually distinct
  fields form a composite key. A record whose key is entirely
  missing/``null`` is skipped; a record with a partially missing/``null``
  key violates ``"has an incomplete unique key"``; complete keys are
  compared with JSON equality (booleans never equal numbers, containers
  compare structurally) and only later duplicates violate
  ``"is a duplicate unique key"``.
* ``group_ratio`` (``options.group_fields``, ``options.value_field``,
  ``options.allowed_values``, ``options.min_ratio``): records are
  grouped by the JSON value tuple of the group fields (in declared
  order). Records with any missing/``null`` group field, and records
  whose ``value_field`` is missing/``null``, are excluded from every
  group. Every other group is checked exactly once; when the fraction of
  records whose value is JSON-equal to one of the allowed values is
  below ``min_ratio`` the group yields one violation. ``min_ratio`` is a
  JSON number in ``[0, 1]`` and equality passes the group.

Rules execute in their given order. Rule definitions are fully
validated before any record is examined (rules first, records then), so
a malformed rule or record aborts the whole run with no partial report.

Malformed rules raise :class:`InvalidBatchRuleError`; malformed records
or a malformed top-level argument raise
:class:`InvalidBatchInputError`. Both subclass :class:`ValueError`.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .correlation import _json_equal
from .validator import _is_number

__all__ = [
    "evaluate_batch_rules",
    "InvalidBatchRuleError",
    "InvalidBatchInputError",
]

SUPPORTED_TYPES = ("unique_key", "group_ratio")

UNIQUE_KEY_OPTIONS = frozenset({"fields"})
GROUP_RATIO_OPTIONS = frozenset(
    {"group_fields", "value_field", "allowed_values", "min_ratio"}
)

_INCOMPLETE_KEY_MESSAGE = "has an incomplete unique key"
_DUPLICATE_KEY_MESSAGE = "is a duplicate unique key"


class InvalidBatchRuleError(ValueError):
    """A batch rule is malformed, unsupported or has conflicting options."""

    code = "INVALID_BATCH_RULE"


class InvalidBatchInputError(ValueError):
    """The records argument or the enclosing payload is malformed."""

    code = "INVALID_BATCH_INPUT"


def _rule_error(message: str) -> InvalidBatchRuleError:
    return InvalidBatchRuleError(message)


def _input_error(message: str) -> InvalidBatchInputError:
    return InvalidBatchInputError(message)


def _validate_field_names(
    value: Any, where: str, key: str
) -> List[str]:
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
    return list(value)


def _validate_batch_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _rule_error("rules must be a list")

    compiled: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for index, rule in enumerate(rules):
        where = f"rules[{index}]"
        if not isinstance(rule, dict):
            raise _rule_error(f"{where} must be an object")

        keys = set(rule)
        if keys != {"id", "type", "options"}:
            missing = sorted({"id", "type", "options"} - keys)
            if missing:
                raise _rule_error(f"{where} is missing keys: {missing}")
            raise _rule_error(
                f"{where} has unsupported keys: {sorted(keys - {'id', 'type', 'options'})}"
            )

        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise _rule_error(
                f"{where} must have a non-empty string id"
            )
        if rule_id in seen_ids:
            raise _rule_error(f"duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        rule_type = rule["type"]
        if not isinstance(rule_type, str) or rule_type not in SUPPORTED_TYPES:
            raise _rule_error(
                f"rule {rule_id!r} has unsupported type: {rule_type!r}"
            )

        options = rule["options"]
        if not isinstance(options, dict):
            raise _rule_error(
                f"rule {rule_id!r} must have an options object"
            )

        compiled_rule: Dict[str, Any] = {
            "id": rule_id,
            "type": rule_type,
        }

        if rule_type == "unique_key":
            if set(options) != UNIQUE_KEY_OPTIONS:
                missing = sorted(UNIQUE_KEY_OPTIONS - set(options))
                if missing:
                    raise _rule_error(
                        f"rule {rule_id!r}: options is missing keys: {missing}"
                    )
                raise _rule_error(
                    f"rule {rule_id!r}: unsupported options for type "
                    f"{rule_type!r}: {sorted(set(options) - UNIQUE_KEY_OPTIONS)}"
                )
            compiled_rule["fields"] = _validate_field_names(
                options["fields"], f"rule {rule_id!r}", "fields"
            )

        else:  # group_ratio
            if set(options) != GROUP_RATIO_OPTIONS:
                missing = sorted(GROUP_RATIO_OPTIONS - set(options))
                if missing:
                    raise _rule_error(
                        f"rule {rule_id!r}: options is missing keys: {missing}"
                    )
                raise _rule_error(
                    f"rule {rule_id!r}: unsupported options for type "
                    f"{rule_type!r}: "
                    f"{sorted(set(options) - GROUP_RATIO_OPTIONS)}"
                )

            compiled_rule["group_fields"] = _validate_field_names(
                options["group_fields"], f"rule {rule_id!r}", "group_fields"
            )

            value_field = options["value_field"]
            if not isinstance(value_field, str) or not value_field:
                raise _rule_error(
                    f"rule {rule_id!r}: options.value_field must be a "
                    "non-empty string"
                )
            compiled_rule["value_field"] = value_field

            allowed_values = options["allowed_values"]
            if not isinstance(allowed_values, list):
                raise _rule_error(
                    f"rule {rule_id!r}: options.allowed_values must be a list"
                )
            compiled_rule["allowed_values"] = allowed_values

            min_ratio = options["min_ratio"]
            if not _is_number(min_ratio):
                raise _rule_error(
                    f"rule {rule_id!r}: options.min_ratio must be a "
                    "JSON number"
                )
            if not 0 <= min_ratio <= 1:
                raise _rule_error(
                    f"rule {rule_id!r}: options.min_ratio must be between "
                    f"0 and 1, got {min_ratio!r}"
                )
            compiled_rule["min_ratio"] = min_ratio

        compiled.append(compiled_rule)

    return compiled


def _validate_records(records: Any) -> List[Dict[str, Any]]:
    if not isinstance(records, list):
        raise _input_error("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise _input_error(
                f"records[{index}] must be a JSON object"
            )
    return records


def _unique_key_violation(
    rule: Dict[str, Any],
    index: int,
    record: Dict[str, Any],
    fields: List[str],
    key: List[Any],
    message: str,
) -> Dict[str, Any]:
    return {
        "rule_id": rule["id"],
        "record_index": index,
        "record_id": record.get("id"),
        "fields": fields,
        "value": key,
        "message": message,
    }


def _run_unique_key_rule(
    rule: Dict[str, Any],
    records: List[Dict[str, Any]],
    violations: List[Dict[str, Any]],
) -> None:
    fields = rule["fields"]
    seen_keys: List[List[Any]] = []
    for index, record in enumerate(records):
        key = [record.get(field) for field in fields]
        if all(value is None for value in key):
            # An entirely missing/null key participates in neither the
            # completeness nor the uniqueness check.
            continue
        if any(value is None for value in key):
            violations.append(
                _unique_key_violation(
                    rule,
                    index,
                    record,
                    fields,
                    key,
                    _INCOMPLETE_KEY_MESSAGE,
                )
            )
            continue
        if any(
            all(_json_equal(a, b) for a, b in zip(key, earlier))
            for earlier in seen_keys
        ):
            violations.append(
                _unique_key_violation(
                    rule,
                    index,
                    record,
                    fields,
                    key,
                    _DUPLICATE_KEY_MESSAGE,
                )
            )
        else:
            seen_keys.append(key)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _json_sort_token(value: Any) -> Tuple[int, Any]:
    """Build a deterministic, type-stable ordering token for JSON values.

    Group keys are ordered by their fixed field order; at each field
    JSON values sort with ``null`` first, then booleans, numbers and
    strings, and finally nested arrays/objects (serialized canonically).
    Values of different JSON types never compare against one another.
    """
    if value is None:
        return (0, 0)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    return (4, _canonical_json(value))


def _same_json_tuple(left: List[Any], right: List[Any]) -> bool:
    return len(left) == len(right) and all(
        _json_equal(a, b) for a, b in zip(left, right)
    )


def _run_group_ratio_rule(
    rule: Dict[str, Any],
    records: List[Dict[str, Any]],
    violations: List[Dict[str, Any]],
) -> int:
    group_fields = rule["group_fields"]
    value_field = rule["value_field"]
    allowed_values = rule["allowed_values"]
    min_ratio = rule["min_ratio"]

    def matches_allowed(value: Any) -> bool:
        return any(
            _json_equal(value, allowed) for allowed in allowed_values
        )

    # Groups are keyed with JSON equality (``1`` and ``1.0`` are the same
    # JSON number); each group keeps its representative key tuple and
    # ``(record_index, record)`` members. Enumeration guarantees correct
    # locators and record-order samples without Python ``list.index``.
    group_keys: List[List[Any]] = []
    group_members: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}

    for index, record in enumerate(records):
        group_values = [record.get(field) for field in group_fields]
        if any(value is None for value in group_values):
            continue
        if value_field not in record or record[value_field] is None:
            continue

        group_index = next(
            (
                position
                for position, key in enumerate(group_keys)
                if _same_json_tuple(group_values, key)
            ),
            None,
        )
        if group_index is None:
            group_index = len(group_keys)
            group_keys.append(group_values)
            group_members[group_index] = []
        group_members[group_index].append((index, record))

    # Groups report in fixed field order with JSON-ascending keys.
    ordered_positions = sorted(
        range(len(group_keys)),
        key=lambda position: tuple(
            _json_sort_token(value) for value in group_keys[position]
        ),
    )

    for position in ordered_positions:
        group_values = group_keys[position]
        members = group_members[position]
        record_count = len(members)
        matched_members = [
            (member_index, record)
            for member_index, record in members
            if matches_allowed(record[value_field])
        ]
        matched_count = len(matched_members)
        ratio = matched_count / record_count
        if ratio >= min_ratio:
            continue

        first_index, first_record = members[0]
        samples = [
            {
                "record_index": member_index,
                "record_id": record.get("id"),
                "value": record[value_field],
                "matched": matches_allowed(record[value_field]),
            }
            for member_index, record in members
        ]
        violations.append(
            {
                "rule_id": rule["id"],
                "record_index": first_index,
                "record_id": first_record.get("id"),
                "group": [
                    {"field": field, "value": value}
                    for field, value in zip(group_fields, group_values)
                ],
                "record_count": record_count,
                "matched_count": matched_count,
                "ratio": ratio,
                "samples": samples,
            }
        )

    return len(group_keys)


def evaluate_batch_rules(records: Any, rules: Any) -> Dict[str, Any]:
    """Evaluate batch-level cross-record rules over ``records``.

    Rules are fully validated before any record is examined; records are
    then validated and rules execute in their given order. The result is
    built entirely in memory: the input records are never mutated,
    nothing is persisted and no service is contacted.

    :param records: list of JSON objects (plain ``dict`` instances).
    :param rules: list of rule objects containing exactly ``id``,
        ``type`` and ``options``. ``id`` must be a unique non-empty
        string and ``type`` one of ``unique_key`` / ``group_ratio``.
        ``unique_key`` options contain exactly non-empty, mutually
        distinct ``fields``; ``group_ratio`` options contain exactly
        non-empty mutually distinct ``group_fields``, a non-empty string
        ``value_field``, an ``allowed_values`` list and ``min_ratio`` as
        a JSON number in ``[0, 1]``.
    :returns: ``{"passed": bool, "summary": {...}, "violations": [...]}``.
        The summary carries ``record_count``, ``rule_count``,
        ``checked_group_count`` (``unique_key`` rules add nothing;
        ``group_ratio`` rules add one per group actually formed from
        non-null key/value records) and ``violation_count``.
        ``unique_key`` violations carry ``rule_id``, ``record_index``,
        ``record_id`` (``id`` or ``null``), ``fields``, ``value`` and
        ``message``; within a rule they are ordered by record index.
        ``group_ratio`` violations carry the same three locator keys
        (pointing at the group's first record) plus ``group`` (one
        ``{"field", "value"}`` pair per group field), ``record_count``,
        ``matched_count``, ``ratio`` and ``samples``; every group member
        is a sample in record order with ``record_index``,
        ``record_id``, ``value`` and ``matched``.
    :raises ValueError: :class:`InvalidBatchRuleError` on malformed rules
        and :class:`InvalidBatchInputError` on malformed records.
    """
    compiled_rules = _validate_batch_rules(rules)
    validated_records = _validate_records(records)

    violations: List[Dict[str, Any]] = []
    checked_group_count = 0
    for rule in compiled_rules:
        if rule["type"] == "unique_key":
            _run_unique_key_rule(rule, validated_records, violations)
        else:
            checked_group_count += _run_group_ratio_rule(
                rule, validated_records, violations
            )

    return {
        "passed": not violations,
        "summary": {
            "record_count": len(validated_records),
            "rule_count": len(compiled_rules),
            "checked_group_count": checked_group_count,
            "violation_count": len(violations),
        },
        "violations": violations,
    }
