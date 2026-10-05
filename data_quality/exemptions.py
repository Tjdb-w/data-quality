"""Violation exemptions over single-field validation results.

The public entry point is :func:`apply_violation_exemptions`. It takes the
result of :func:`data_quality.validate` (single-field violations only;
composite results are never included) plus a list of exemption objects and
splits the violations into still-active and waived ones. No rule is
re-evaluated, no verdict is recomputed and nothing is written to disk.

Each exemption carries exactly the keys ``exemption_id``, ``rule_id``,
``record_index``, ``record_id``, ``field`` and ``reason``:

* ``exemption_id``: unique non-empty string identifier;
* ``rule_id`` / ``record_index`` / ``record_id`` / ``field``: the match
  tuple — a string rule id, a non-negative integer record index (booleans
  are not integers), a string-or-null record id and a string field name;
* ``reason``: non-empty string kept as evidence on the waived violation.

An exemption matches exactly one violation by the four match keys.
Malformed exemptions, duplicated ``exemption_id`` values, two exemptions
matching the same violation (a conflict) or an exemption matching no
violation at all raise :class:`InvalidExemptionError`; no partial result
is returned.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

__all__ = [
    "apply_violation_exemptions",
    "InvalidExemptionError",
]

STATUS_OK = "ok"
STATUS_VIOLATIONS = "violations"

_EXEMPTION_KEYS = frozenset(
    {"exemption_id", "rule_id", "record_index", "record_id", "field", "reason"}
)

_MATCH_KEYS = ("rule_id", "record_index", "record_id", "field")


class InvalidExemptionError(ValueError):
    """An exemption is malformed, duplicated, conflicting or unmatched."""

    code = "INVALID_EXEMPTION_INPUT"


def _exemption_error(message: str) -> InvalidExemptionError:
    return InvalidExemptionError(message)


def _match_key_of(item: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    return (
        item["rule_id"],
        item["record_index"],
        item["record_id"],
        item["field"],
    )


def _validate_exemptions(exemptions: Any) -> List[Dict[str, Any]]:
    if not isinstance(exemptions, list):
        raise _exemption_error("exemptions must be a list of exemption objects")

    seen_ids: set = set()
    seen_keys: Dict[Tuple[Any, Any, Any, Any], str] = {}

    for index, exemption in enumerate(exemptions):
        where = f"exemptions[{index}]"
        if not isinstance(exemption, dict):
            raise _exemption_error(f"{where} must be an object")

        keys = set(exemption)
        if keys != _EXEMPTION_KEYS:
            missing = sorted(_EXEMPTION_KEYS - keys)
            if missing:
                raise _exemption_error(f"{where} is missing keys: {missing}")
            unknown = sorted(keys - _EXEMPTION_KEYS)
            raise _exemption_error(f"{where} has unsupported keys: {unknown}")

        exemption_id = exemption["exemption_id"]
        if not isinstance(exemption_id, str) or not exemption_id:
            raise _exemption_error(
                f"{where} field 'exemption_id' must be a non-empty string"
            )
        if exemption_id in seen_ids:
            raise _exemption_error(f"duplicate exemption_id: {exemption_id!r}")
        seen_ids.add(exemption_id)

        rule_id = exemption["rule_id"]
        if not isinstance(rule_id, str):
            raise _exemption_error(
                f"exemption {exemption_id!r}: rule_id must be a string"
            )

        record_index = exemption["record_index"]
        if (
            not isinstance(record_index, int)
            or isinstance(record_index, bool)
            or record_index < 0
        ):
            raise _exemption_error(
                f"exemption {exemption_id!r}: record_index must be a "
                f"non-negative integer"
            )

        record_id = exemption["record_id"]
        if record_id is not None and not isinstance(record_id, str):
            raise _exemption_error(
                f"exemption {exemption_id!r}: record_id must be a string "
                f"or null"
            )

        field = exemption["field"]
        if not isinstance(field, str):
            raise _exemption_error(
                f"exemption {exemption_id!r}: field must be a string"
            )

        reason = exemption["reason"]
        if not isinstance(reason, str) or not reason:
            raise _exemption_error(
                f"exemption {exemption_id!r}: reason must be a non-empty "
                f"string"
            )

        key = _match_key_of(exemption)
        if key in seen_keys:
            raise _exemption_error(
                f"exemption {exemption_id!r} conflicts with exemption "
                f"{seen_keys[key]!r}: both match the same violation"
            )
        seen_keys[key] = exemption_id

    return exemptions


def _validate_result_violations(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        raise _exemption_error("result must be a validate result object")
    violations = result.get("violations")
    if not isinstance(violations, list):
        raise _exemption_error(
            "result must be a validate result with a violations list"
        )
    for index, violation in enumerate(violations):
        where = f"result.violations[{index}]"
        if not isinstance(violation, dict):
            raise _exemption_error(f"{where} must be an object")
        missing = [key for key in _MATCH_KEYS if key not in violation]
        if missing:
            raise _exemption_error(f"{where} is missing keys: {missing}")
        if not isinstance(violation["rule_id"], str):
            raise _exemption_error(f"{where}: rule_id must be a string")
        record_index = violation["record_index"]
        if (
            not isinstance(record_index, int)
            or isinstance(record_index, bool)
            or record_index < 0
        ):
            raise _exemption_error(
                f"{where}: record_index must be a non-negative integer"
            )
        record_id = violation["record_id"]
        if record_id is not None and not isinstance(record_id, str):
            raise _exemption_error(
                f"{where}: record_id must be a string or null"
            )
        if not isinstance(violation["field"], str):
            raise _exemption_error(f"{where}: field must be a string")
    return violations


def _match_exemptions(
    violations: List[Dict[str, Any]], exemptions: Any
) -> Dict[Tuple[Any, Any, Any, Any], Dict[str, Any]]:
    """Validate ``exemptions`` and index them by violation match key.

    Every exemption must match exactly one of ``violations``; structural
    errors, duplicates, conflicts and unmatched exemptions raise
    :class:`InvalidExemptionError`.
    """
    validated = _validate_exemptions(exemptions)
    violation_keys = {_match_key_of(violation) for violation in violations}

    by_key: Dict[Tuple[Any, Any, Any, Any], Dict[str, Any]] = {}
    for exemption in validated:
        key = _match_key_of(exemption)
        if key not in violation_keys:
            raise _exemption_error(
                f"exemption {exemption['exemption_id']!r} does not match "
                f"any violation"
            )
        by_key[key] = exemption
    return by_key


def apply_violation_exemptions(result: Any, exemptions: Any) -> Dict[str, Any]:
    """Split a :func:`data_quality.validate` result by exemptions.

    :param result: a validate result object; only its single-field
        ``violations`` are considered and composite results are never
        included in the output.
    :param exemptions: list of exemption objects, each with exactly the
        keys ``exemption_id`` (unique non-empty string), ``rule_id``
        (string), ``record_index`` (non-negative integer), ``record_id``
        (string or ``None``), ``field`` (string) and ``reason``
        (non-empty string, kept as evidence).
    :returns: ``{"status": ..., "summary": {...}, "active_violations":
        [...], "waived_violations": [...]}``. Both lists keep the
        original violations in their original order; waived entries
        additionally carry ``exemption_id`` and ``reason``. ``summary``
        holds ``input_violation_count``, ``active_violation_count``,
        ``waived_violation_count`` and ``exemption_count`` with
        ``input_violation_count == active_violation_count +
        waived_violation_count``. ``status`` is ``"ok"`` exactly when no
        active violation remains, otherwise ``"violations"``.
    :raises InvalidExemptionError: on a malformed result, a malformed,
        duplicated or conflicting exemption, or an exemption that
        matches no violation.
    """
    violations = _validate_result_violations(result)
    exemption_by_key = _match_exemptions(violations, exemptions)

    active: List[Dict[str, Any]] = []
    waived: List[Dict[str, Any]] = []
    for violation in violations:
        exemption = exemption_by_key.get(_match_key_of(violation))
        if exemption is None:
            active.append(dict(violation))
        else:
            entry = dict(violation)
            entry["exemption_id"] = exemption["exemption_id"]
            entry["reason"] = exemption["reason"]
            waived.append(entry)

    return {
        "status": STATUS_OK if not active else STATUS_VIOLATIONS,
        "summary": {
            "input_violation_count": len(violations),
            "active_violation_count": len(active),
            "waived_violation_count": len(waived),
            "exemption_count": len(exemption_by_key),
        },
        "active_violations": active,
        "waived_violations": waived,
    }
