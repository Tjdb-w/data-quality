"""Record profiling and validate rule candidate generation.

The public entry point is :func:`profile_records`. It profiles a list of
JSON object records (the same input accepted by
:func:`data_quality.validate`) and generates candidate single-field rule
objects in the validate rule shape. Nothing is modified and nothing is
written to disk.

Input constraints raise :class:`InvalidProfileInputError` (code
``INVALID_PROFILE_INPUT``); the run is all-or-nothing and no partial
profiles are produced.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .validator import _is_number, _json_equal

__all__ = [
    "profile_records",
    "InvalidProfileInputError",
]

# The six JSON types counted by ``type_counts``, in output order. Booleans
# count as ``boolean`` rather than ``number`` (JSON true/false are not 1/0).
TYPE_KEYS = ("null", "boolean", "number", "string", "array", "object")

_MAX_ALLOWED_VALUES = 20


class InvalidProfileInputError(ValueError):
    """The profile payload (records or fields) is malformed."""

    code = "INVALID_PROFILE_INPUT"


def _profile_error(message: str) -> InvalidProfileInputError:
    return InvalidProfileInputError(message)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    # Parsed JSON can only yield the types above; anything else cannot be
    # profiled unambiguously.
    raise _profile_error(f"unsupported value type: {type(value).__name__}")


def _validate_fields(fields: Any) -> List[str]:
    """Validate the explicit ``fields`` array.

    It must be a list of unique, non-empty strings.
    """
    if not isinstance(fields, list):
        raise _profile_error("fields must be a list of field names")

    validated: List[str] = []
    seen: set = set()
    for index, field in enumerate(fields):
        if not isinstance(field, str) or not field:
            raise _profile_error(
                f"fields[{index}] must be a non-empty string"
            )
        if field in seen:
            raise _profile_error(f"fields contains a duplicate field: {field!r}")
        seen.add(field)
        validated.append(field)
    return validated


def _resolve_fields(records: List[Dict[str, Any]], fields: Any) -> List[str]:
    """Return the fields to profile in their profiling order.

    Explicit ``fields`` keep their given order; when omitted the top-level
    keys of the records are used in first-appearance order. Only top-level
    keys are ever profiled.
    """
    if fields is not None:
        return _validate_fields(fields)

    ordered: List[str] = []
    seen: set = set()
    for record in records:
        for key in record:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def _empty_profile(field: str) -> Dict[str, Any]:
    return {
        "field": field,
        "present_count": 0,
        "missing_count": 0,
        "null_count": 0,
        "non_null_count": 0,
        "distinct_non_null_count": 0,
        "type_counts": {key: 0 for key in TYPE_KEYS},
        "numeric_min": None,
        "numeric_max": None,
    }


def _profile_field(
    field: str, records: List[Dict[str, Any]]
) -> Tuple[Dict[str, Any], List[Any]]:
    """Profile one field.

    Returns the profile dictionary and the distinct present values in
    first-appearance order (including ``None`` for an explicitly present
    null), used to build the ``allowed_values`` candidate.
    """
    profile = _empty_profile(field)
    type_counts = profile["type_counts"]

    present_count = 0
    null_count = 0
    all_numbers = True
    seen_non_null: List[Any] = []
    distinct_present: List[Any] = []
    numeric_min: Any = None
    numeric_max: Any = None

    for record in records:
        if field not in record:
            continue

        present_count += 1
        value = record[field]
        type_counts[_json_type(value)] += 1

        if not any(_json_equal(value, earlier) for earlier in distinct_present):
            distinct_present.append(value)

        if value is None:
            # A null value makes the field non-numeric overall, so no
            # numeric_min/numeric_max are reported.
            null_count += 1
            all_numbers = False
            continue

        if not any(_json_equal(value, earlier) for earlier in seen_non_null):
            seen_non_null.append(value)

        if _is_number(value):
            if numeric_min is None or value < numeric_min:
                numeric_min = value
            if numeric_max is None or value > numeric_max:
                numeric_max = value
        else:
            all_numbers = False

    record_count = len(records)
    profile["present_count"] = present_count
    profile["missing_count"] = record_count - present_count
    profile["null_count"] = null_count
    profile["non_null_count"] = present_count - null_count
    profile["distinct_non_null_count"] = len(seen_non_null)
    # numeric_min/numeric_max only when every non-null value is a
    # non-boolean number and there is at least one of them.
    if all_numbers and seen_non_null:
        profile["numeric_min"] = numeric_min
        profile["numeric_max"] = numeric_max

    return profile, distinct_present


def _rule(field: str, rule_type: str, options: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": f"profile:{field}:{rule_type}",
        "type": rule_type,
        "options": options,
    }


def _candidate_rules(
    field: str, profile: Dict[str, Any], distinct_present: List[Any]
) -> List[Dict[str, Any]]:
    """Generate candidate validate rules for one field, in fixed order.

    The order is ``required``, ``unique``, ``range``, ``allowed_values``;
    a rule is only included when its condition is certainly satisfied.
    """
    candidates: List[Dict[str, Any]] = []
    record_count = profile["present_count"] + profile["missing_count"]
    has_records = record_count > 0
    no_missing = profile["missing_count"] == 0
    no_null = profile["null_count"] == 0

    # required: at least one record and the field is present and non-null
    # in every record.
    if has_records and no_missing and no_null:
        candidates.append(_rule(field, "required", {"field": field}))

    # unique: at least two non-null values and every pair differs under
    # validate JSON equality.
    if (
        profile["non_null_count"] >= 2
        and profile["distinct_non_null_count"] == profile["non_null_count"]
    ):
        candidates.append(_rule(field, "unique", {"field": field}))

    # range: the field is present and non-null in every record and every
    # value is a non-boolean JSON number; numeric_min/numeric_max are then
    # populated.
    if (
        has_records
        and no_missing
        and no_null
        and profile["numeric_min"] is not None
    ):
        candidates.append(
            _rule(
                field,
                "range",
                {
                    "field": field,
                    "min": profile["numeric_min"],
                    "max": profile["numeric_max"],
                },
            )
        )

    # allowed_values: the field is present in every record and the set of
    # distinct present values (null included, in first-appearance order)
    # has at most 20 members.
    if has_records and no_missing and len(distinct_present) <= _MAX_ALLOWED_VALUES:
        candidates.append(
            _rule(
                field,
                "allowed_values",
                {"field": field, "values": distinct_present},
            )
        )

    return candidates


def profile_records(records: Any, fields: Any = None) -> Dict[str, Any]:
    """Profile ``records`` and generate validate rule candidates.

    :param records: list of JSON objects (plain ``dict`` instances), the
        same shape accepted by :func:`data_quality.validate`.
    :param fields: optional list of field names to profile. Elements must
        be unique, non-empty strings and the given order is preserved.
        When omitted, the top-level keys of the records are profiled in
        first-appearance order. Only top-level keys are ever profiled.
    :returns: ``{"record_count": int, "fields": [...],
        "rule_candidates": [...]}`` where ``fields`` holds one profile per
        profiled field and ``rule_candidates`` holds candidate single-field
        rule objects in the validate rule shape (``id`` / ``type`` /
        ``options``), grouped by profiled field and ordered
        ``required``, ``unique``, ``range``, ``allowed_values`` within a
        field.
    :raises InvalidProfileInputError: when ``records`` is not a list of
        JSON objects or ``fields`` is not a list of unique non-empty
        strings.
    """
    if not isinstance(records, list):
        raise _profile_error("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise _profile_error(f"records[{index}] must be a JSON object")

    ordered_fields = _resolve_fields(records, fields)

    profiles: List[Dict[str, Any]] = []
    rule_candidates: List[Dict[str, Any]] = []
    for field in ordered_fields:
        profile, distinct_present = _profile_field(field, records)
        profiles.append(profile)
        rule_candidates.extend(_candidate_rules(field, profile, distinct_present))

    return {
        "record_count": len(records),
        "fields": profiles,
        "rule_candidates": rule_candidates,
    }
