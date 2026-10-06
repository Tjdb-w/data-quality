"""Field profiling and rule-candidate generation for the data_quality package.

The public entry point is :func:`profile_records`. It profiles the same
``records`` payload consumed by :func:`data_quality.validate` (a list of
JSON objects) and returns field statistics together with suggested
single-field rule objects. Nothing is written to disk and the input
records are never modified.

Only top-level field values are profiled; nested object/array contents
are counted by their container type and compared structurally for
distinctness, using the same JSON equality semantics as ``validate``
(booleans are never equal to numbers).

Structural problems raise :class:`InvalidProfileInputError`, a
:class:`ValueError`; there is no partial profile.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .validator import _is_number, _json_equal

__all__ = [
    "profile_records",
    "InvalidProfileInputError",
]

# Fixed output order for the six JSON value types plus explicit null.
TYPE_ORDER = ("null", "boolean", "number", "string", "array", "object")

_MAX_ALLOWED_VALUES = 20


class InvalidProfileInputError(ValueError):
    """The records or fields argument of :func:`profile_records` is malformed."""

    code = "INVALID_PROFILE_INPUT"


def _error(message: str) -> InvalidProfileInputError:
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
    raise _error(f"value is not a JSON-compatible value: {value!r}")


def _validate_inputs(records: Any, fields: Any) -> List[str]:
    if not isinstance(records, list):
        raise _error("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise _error(f"records[{index}] must be a JSON object")

    if fields is None:
        ordered: List[str] = []
        seen = set()
        for record in records:
            for key in record:
                if key not in seen:
                    seen.add(key)
                    ordered.append(key)
        return ordered

    if not isinstance(fields, list):
        raise _error("fields must be a list")
    ordered_fields: List[str] = []
    seen_fields = set()
    for index, field in enumerate(fields):
        if not isinstance(field, str) or not field:
            raise _error(
                f"fields[{index}] must be a non-empty string"
            )
        if field in seen_fields:
            raise _error(f"fields[{index}] duplicates field {field!r}")
        seen_fields.add(field)
        ordered_fields.append(field)
    return ordered_fields


def _empty_profile(field: str) -> Dict[str, Any]:
    return {
        "field": field,
        "present_count": 0,
        "missing_count": 0,
        "null_count": 0,
        "non_null_count": 0,
        "distinct_non_null_count": 0,
        "type_counts": {type_name: 0 for type_name in TYPE_ORDER},
        "numeric_min": None,
        "numeric_max": None,
    }


def _profile_field(field: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    profile = _empty_profile(field)
    type_counts = profile["type_counts"]

    distinct_non_null: List[Any] = []
    numeric_values: List[Any] = []
    all_non_null_numeric = True

    for record in records:
        if field not in record:
            profile["missing_count"] += 1
            continue
        profile["present_count"] += 1
        value = record[field]
        if value is None:
            profile["null_count"] += 1
            type_counts["null"] += 1
            continue

        profile["non_null_count"] += 1
        type_counts[_json_type(value)] += 1
        if not any(_json_equal(value, earlier) for earlier in distinct_non_null):
            distinct_non_null.append(value)
        if _is_number(value):
            numeric_values.append(value)
        else:
            all_non_null_numeric = False

    profile["distinct_non_null_count"] = len(distinct_non_null)

    if numeric_values and all_non_null_numeric:
        profile["numeric_min"] = min(numeric_values)
        profile["numeric_max"] = max(numeric_values)

    return profile


def _rule_candidates(
    field: str, profile: Dict[str, Any], records: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []

    def rule(rule_type: str, options: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": f"profile:{field}:{rule_type}",
            "type": rule_type,
            "options": options,
        }

    # required: at least one record carries a present, non-null value.
    if profile["non_null_count"] >= 1:
        candidates.append(rule("required", {"field": field}))

    # unique: at least two pairwise distinct non-null values exist.
    if profile["distinct_non_null_count"] >= 2:
        candidates.append(rule("unique", {"field": field}))

    # range: no missing/null values, every value a non-boolean number,
    # and at least one value to derive the bounds from.
    if (
        profile["missing_count"] == 0
        and profile["null_count"] == 0
        and profile["non_null_count"] >= 1
        and profile["numeric_min"] is not None
    ):
        candidates.append(
            rule(
                "range",
                {
                    "field": field,
                    "min": profile["numeric_min"],
                    "max": profile["numeric_max"],
                },
            )
        )

    # allowed_values: no missing values and at most 20 distinct values
    # (explicit null included), enumerated in first-appearance order.
    if profile["missing_count"] == 0 and profile["present_count"] >= 1:
        values: List[Any] = []
        for record in records:
            value = record[field]
            if not any(_json_equal(value, earlier) for earlier in values):
                values.append(value)
        if len(values) <= _MAX_ALLOWED_VALUES:
            candidates.append(
                rule("allowed_values", {"field": field, "values": values})
            )

    return candidates


def profile_records(records: Any, fields: Any = None) -> Dict[str, Any]:
    """Profile top-level field values and suggest single-field rules.

    :param records: list of JSON objects (plain ``dict`` instances), the
        same shape accepted by :func:`data_quality.validate`.
    :param fields: optional list of unique, non-empty field names to
        profile, in output order. When omitted, fields are taken from the
        top-level record keys in first-appearance order. Only top-level
        keys are profiled.
    :returns: ``{"record_count": int, "fields": [...profiles...],
        "rule_candidates": [...rules...]}`` where each profile has
        ``field``, ``present_count``, ``missing_count``, ``null_count``,
        ``non_null_count``, ``distinct_non_null_count``, ``type_counts``
        (keys ``null``/``boolean``/``number``/``string``/``array``/
        ``object``; booleans are not numbers), ``numeric_min`` and
        ``numeric_max`` (``null`` unless every non-null value is a
        non-boolean number and at least one exists). Rule candidates use
        the validate rule shape with ``id`` ``profile:<field>:<type>``
        and are ordered per field as required, unique, range,
        allowed_values; fields follow the profile order.
    :raises InvalidProfileInputError: when ``records`` is not a list of
        JSON objects or ``fields`` is not a list of unique non-empty
        strings. No partial profile is produced.
    """
    ordered_fields = _validate_inputs(records, fields)

    profiles: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for field in ordered_fields:
        profile = _profile_field(field, records)
        profiles.append(profile)
        candidates.extend(_rule_candidates(field, profile, records))

    return {
        "record_count": len(records),
        "fields": profiles,
        "rule_candidates": candidates,
    }
