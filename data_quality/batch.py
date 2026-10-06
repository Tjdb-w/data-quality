"""Batch cross-record validation rules.

The public entry point is :func:`evaluate_batch_rules`. It takes a list
of record objects plus a list of batch rules and returns an in-memory
report with ``passed`` / ``summary`` / ``violations``. Records are never
modified, nothing is written to disk and no external service is
accessed. Two rule types are supported:

* ``unique_key`` declares ``options.fields``: a non-empty array of
  mutually distinct non-empty field names forming a composite unique
  key. A missing field reads as ``null``. A record whose key is
  entirely ``null`` is skipped; a partially ``null`` key yields an
  ``"has an incomplete unique key"`` violation for that record.
  Complete keys are compared with JSON equality (booleans never equal
  numbers, containers compare structurally) and only later records
  carrying an already seen key yield an ``"is a duplicate unique
  key"`` violation.
* ``group_ratio`` declares exactly ``options.group_fields``,
  ``options.value_field``, ``options.allowed_values`` and
  ``options.min_ratio``. Records are grouped by the ``group_fields``
  key values (missing reads as ``null``, keys compared with JSON
  equality). Inside a group a record is matched when its
  ``value_field`` value (missing reads as ``null``) is JSON-equal to
  one of the non-empty ``allowed_values``. A group whose
  ``matched_count / record_count`` ratio falls below ``min_ratio``
  (a JSON number in ``[0, 1]``; equality passes) yields one violation
  carrying the group key, the counts, the ratio and one sample per
  group record in record order.

Each rule carries exactly the keys ``id``, ``type`` and ``options``;
``id`` is a unique non-empty string and rules execute in array order.
Every violation carries ``rule_id``, ``record_index`` and ``record_id``
(the record's ``id``, ``null`` when absent; both locators are ``null``
on group-level violations). ``unique_key`` violations additionally
carry ``fields`` and ``value`` (both in declared field order) plus
``message`` and are emitted in record order. ``group_ratio``
violations additionally carry ``group`` (keys in declared field
order), ``record_count``, ``matched_count``, ``ratio`` and ``samples``
and are emitted ordered by the group key values in JSON ascending
order (``null`` < booleans < numbers < strings < arrays < objects).

The summary carries ``record_count``, ``rule_count``,
``checked_group_count`` (the number of actual groups examined by
``group_ratio`` rules; ``unique_key`` rules add nothing) and
``violation_count``. Empty records yield no violations and empty rules
pass.

Malformed rules raise :class:`InvalidBatchRuleError` (a
:class:`ValueError`); malformed records or a malformed enclosing
payload raise :class:`InvalidBatchInputError` (a :class:`ValueError`).
Rules are validated before any record is examined and no partial
report is produced.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .validator import _is_number, _json_equal

__all__ = [
    "evaluate_batch_rules",
    "InvalidBatchInputError",
    "InvalidBatchRuleError",
    "SUPPORTED_BATCH_TYPES",
]

SUPPORTED_BATCH_TYPES = ("unique_key", "group_ratio")

_RULE_KEYS = frozenset({"id", "type", "options"})
_UNIQUE_KEY_OPTION_KEYS = frozenset({"fields"})
_GROUP_RATIO_OPTION_KEYS = frozenset(
    {"group_fields", "value_field", "allowed_values", "min_ratio"}
)


class InvalidBatchInputError(ValueError):
    """The records argument or the enclosing payload is malformed."""

    code = "INVALID_BATCH_INPUT"


class InvalidBatchRuleError(ValueError):
    """A batch rule is malformed, unsupported or has a duplicated id."""

    code = "INVALID_BATCH_RULE"


def _input_error(message: str) -> InvalidBatchInputError:
    return InvalidBatchInputError(message)


def _rule_error(message: str) -> InvalidBatchRuleError:
    return InvalidBatchRuleError(message)


def _validate_field_names(value: Any, where: str, key: str) -> None:
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


def _validate_options_keys(rule_id: str, options: Any, expected: frozenset) -> None:
    if not isinstance(options, dict):
        raise _rule_error(f"rule {rule_id!r} must have an options object")
    keys = set(options)
    if keys != expected:
        missing = sorted(expected - keys)
        if missing:
            raise _rule_error(
                f"rule {rule_id!r}: options is missing keys: {missing}"
            )
        unknown = sorted(keys - expected)
        raise _rule_error(
            f"rule {rule_id!r}: options has unsupported keys: {unknown}"
        )


def _validate_unique_key_options(rule_id: str, options: Any) -> None:
    _validate_options_keys(rule_id, options, _UNIQUE_KEY_OPTION_KEYS)
    _validate_field_names(options["fields"], f"rule {rule_id!r}", "fields")


def _validate_group_ratio_options(rule_id: str, options: Any) -> None:
    _validate_options_keys(rule_id, options, _GROUP_RATIO_OPTION_KEYS)
    where = f"rule {rule_id!r}"
    _validate_field_names(options["group_fields"], where, "group_fields")
    value_field = options["value_field"]
    if not isinstance(value_field, str) or not value_field:
        raise _rule_error(
            f"{where} field 'value_field' must be a non-empty string"
        )
    allowed_values = options["allowed_values"]
    if not isinstance(allowed_values, list) or not allowed_values:
        raise _rule_error(
            f"{where} field 'allowed_values' must be a non-empty array"
        )
    min_ratio = options["min_ratio"]
    if not _is_number(min_ratio):
        raise _rule_error(
            f"{where} field 'min_ratio' must be a JSON number"
        )
    if not 0 <= min_ratio <= 1:
        raise _rule_error(
            f"{where} field 'min_ratio' must be between 0 and 1, got "
            f"{min_ratio!r}"
        )


def _validate_rules(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        raise _rule_error("rules must be a list of rule objects")
    seen_ids = set()
    for index, rule in enumerate(rules):
        where = f"rule[{index}]"
        if not isinstance(rule, dict):
            raise _rule_error(f"{where} must be an object")
        keys = set(rule)
        if keys != _RULE_KEYS:
            missing = sorted(_RULE_KEYS - keys)
            if missing:
                raise _rule_error(f"{where} is missing keys: {missing}")
            unknown = sorted(keys - _RULE_KEYS)
            raise _rule_error(f"{where} has unsupported keys: {unknown}")
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise _rule_error(
                f"{where} field 'id' must be a non-empty string"
            )
        if rule_id in seen_ids:
            raise _rule_error(f"duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)
        rule_type = rule["type"]
        if rule_type not in SUPPORTED_BATCH_TYPES:
            raise _rule_error(
                f"rule {rule_id!r} has unsupported type: {rule_type!r}"
            )
        if rule_type == "unique_key":
            _validate_unique_key_options(rule_id, rule["options"])
        else:
            _validate_group_ratio_options(rule_id, rule["options"])
    return rules


def _validate_records(records: Any) -> List[Dict[str, Any]]:
    if not isinstance(records, list):
        raise _input_error("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise _input_error(f"records[{index}] must be a JSON object")
    return records


def _freeze(value: Any) -> Any:
    """Hashable canonical form consistent with JSON equality."""
    if isinstance(value, dict):
        return (
            "dict",
            tuple(sorted((key, _freeze(item)) for key, item in value.items())),
        )
    if isinstance(value, list):
        return ("list", tuple(_freeze(item) for item in value))
    if isinstance(value, bool):
        return ("bool", value)
    if value is None:
        return ("null",)
    if isinstance(value, (int, float)):
        # 1 and 1.0 hash and compare equal, matching JSON equality.
        return ("number", value)
    return ("string", value)


def _json_sort_key(value: Any) -> Any:
    """Total ordering key: JSON ascending across and within types."""
    if value is None:
        return (0, None)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    if isinstance(value, list):
        return (4, tuple(_json_sort_key(item) for item in value))
    return (
        5,
        tuple(sorted((key, _json_sort_key(item)) for key, item in value.items())),
    )


def _run_unique_key_rule(
    rule: Dict[str, Any],
    records: List[Dict[str, Any]],
    violations: List[Dict[str, Any]],
) -> None:
    fields = rule["options"]["fields"]
    seen_keys = set()
    for record_index, record in enumerate(records):
        values = [record.get(field) for field in fields]
        if all(value is None for value in values):
            continue
        if any(value is None for value in values):
            message = "has an incomplete unique key"
        else:
            frozen = tuple(_freeze(value) for value in values)
            if frozen not in seen_keys:
                seen_keys.add(frozen)
                continue
            message = "is a duplicate unique key"
        violations.append(
            {
                "rule_id": rule["id"],
                "record_index": record_index,
                "record_id": record.get("id"),
                "fields": fields,
                "value": values,
                "message": message,
            }
        )


def _run_group_ratio_rule(
    rule: Dict[str, Any],
    records: List[Dict[str, Any]],
    violations: List[Dict[str, Any]],
) -> int:
    options = rule["options"]
    group_fields = options["group_fields"]
    value_field = options["value_field"]
    allowed_values = options["allowed_values"]
    min_ratio = options["min_ratio"]

    groups: Dict[Any, Dict[str, Any]] = {}
    for record_index, record in enumerate(records):
        key_values = [record.get(field) for field in group_fields]
        frozen = tuple(_freeze(value) for value in key_values)
        value = record.get(value_field)
        matched = any(
            _json_equal(value, allowed) for allowed in allowed_values
        )
        entry = groups.setdefault(
            frozen, {"key_values": key_values, "members": []}
        )
        entry["members"].append(
            (record_index, record.get("id"), value, matched)
        )

    ordered = sorted(
        groups.values(),
        key=lambda entry: tuple(
            _json_sort_key(value) for value in entry["key_values"]
        ),
    )
    for entry in ordered:
        members = entry["members"]
        record_count = len(members)
        matched_count = sum(1 for member in members if member[3])
        ratio = matched_count / record_count
        if ratio >= min_ratio:
            continue
        violations.append(
            {
                "rule_id": rule["id"],
                "record_index": None,
                "record_id": None,
                "group": dict(zip(group_fields, entry["key_values"])),
                "record_count": record_count,
                "matched_count": matched_count,
                "ratio": ratio,
                "samples": [
                    {
                        "record_index": record_index,
                        "record_id": record_id,
                        "value": value,
                        "matched": matched,
                    }
                    for record_index, record_id, value, matched in members
                ],
            }
        )
    return len(groups)


def evaluate_batch_rules(records: Any, rules: Any) -> Dict[str, Any]:
    """Evaluate batch cross-record rules over a list of records.

    :param records: list of JSON object records (plain ``dict``
        instances); never modified.
    :param rules: list of rule objects, each with exactly the keys
        ``id`` (unique non-empty string), ``type`` (``unique_key`` or
        ``group_ratio``) and ``options``. Rules are validated before
        any record is examined and execute in array order.
    :returns: ``{"passed": bool, "summary": {...}, "violations": [...]}``.
        The summary carries ``record_count``, ``rule_count``,
        ``checked_group_count`` (actual groups examined by
        ``group_ratio`` rules; ``unique_key`` rules add nothing) and
        ``violation_count``. Every violation carries ``rule_id``,
        ``record_index`` and ``record_id`` (the record's ``id`` or
        ``null``; both locators are ``null`` on group-level
        violations). ``unique_key`` violations additionally carry
        ``fields``, ``value`` (both in declared field order) and
        ``message`` (``"has an incomplete unique key"`` for partially
        ``null`` keys, ``"is a duplicate unique key"`` for later
        records repeating a complete key) and appear in record order.
        ``group_ratio`` violations additionally carry ``group`` (keys
        in declared field order), ``record_count``, ``matched_count``,
        ``ratio`` (``matched_count / record_count``) and ``samples``
        (one per group record in record order, each with
        ``record_index``, ``record_id``, ``value`` and ``matched``)
        and appear ordered by the group key values in JSON ascending
        order. Empty records yield no violations; empty rules pass.
    :raises ValueError: :class:`InvalidBatchRuleError` on malformed,
        unsupported or duplicated rules, :class:`InvalidBatchInputError`
        on malformed records. No partial report is produced.
    """
    rules = _validate_rules(rules)
    records = _validate_records(records)

    checked_group_count = 0
    violations: List[Dict[str, Any]] = []
    for rule in rules:
        if rule["type"] == "unique_key":
            _run_unique_key_rule(rule, records, violations)
        else:
            checked_group_count += _run_group_ratio_rule(
                rule, records, violations
            )

    return {
        "passed": not violations,
        "summary": {
            "record_count": len(records),
            "rule_count": len(rules),
            "checked_group_count": checked_group_count,
            "violation_count": len(violations),
        },
        "violations": violations,
    }
