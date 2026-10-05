"""Manual exemptions for single-field rule violations.

The public entry point is :func:`apply_violation_exemptions`. It takes a
result produced by :func:`data_quality.validate` (only its single-field
``violations`` are considered; composite results are never exempted) and a
list of exemption objects, and partitions the violations into the still
active ones and the waived ones.

Each exemption carries exactly the six keys ``exemption_id``, ``rule_id``,
``record_index``, ``record_id``, ``field`` and ``reason`` and matches a
violation precisely on ``rule_id`` / ``record_index`` / ``record_id`` /
``field``. Nothing is written to disk and the original violations are left
unchanged; waived violations simply gain ``exemption_id`` and ``reason``
evidence.

Invalid exemption input, duplicated or conflicting exemptions and
exemptions that match no reported violation all raise
:class:`InvalidExemptionError` (code ``INVALID_EXEMPTION_INPUT``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .validator import DataQualityError

__all__ = [
    "apply_violation_exemptions",
    "InvalidExemptionError",
]

STATUS_OK = "ok"

_EXEMPTION_KEYS = frozenset(
    {"exemption_id", "rule_id", "record_index", "record_id", "field", "reason"}
)

# Keys used to locate the violation an exemption waives.
_TARGET_KEYS = ("rule_id", "record_index", "record_id", "field")

# Validate results carry violations with these identifying keys.
_VIOLATION_KEYS = frozenset(
    {"rule_id", "record_index", "record_id", "field"}
)


class InvalidExemptionError(DataQualityError):
    """An exemption (or the validate result) is malformed or unusable."""

    code = "INVALID_EXEMPTION_INPUT"


def _exemption_error(message: str) -> InvalidExemptionError:
    return InvalidExemptionError(message)


def _is_non_negative_int(value: Any) -> bool:
    # bool is a subclass of int, but JSON true/false are not integers.
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _target_of(exemption: Dict[str, Any]) -> Tuple[Any, ...]:
    return tuple(exemption[key] for key in _TARGET_KEYS)


def _validate_exemptions(
    exemptions: Any,
    violations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Validate exemptions structurally and against the violations.

    Returns the validated exemption list unchanged. Raises
    :class:`InvalidExemptionError` on malformed input, duplicated or
    conflicting exemptions, or exemptions that match no violation.
    """
    if not isinstance(exemptions, list):
        raise _exemption_error("exemptions must be a list of exemption objects")

    validated: List[Dict[str, Any]] = []
    seen_ids: set = set()
    # Map a matched target quadruple to the exemption id / reason that
    # claimed it, so repeated targets can be classified as duplicate or
    # conflicting.
    seen_targets: Dict[Tuple[Any, ...], Tuple[str, str]] = {}

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
            raise _exemption_error(
                f"{where} duplicates exemption_id: {exemption_id!r}"
            )
        seen_ids.add(exemption_id)

        rule_id = exemption["rule_id"]
        if not isinstance(rule_id, str):
            raise _exemption_error(
                f"{where} field 'rule_id' must be a string"
            )

        record_index = exemption["record_index"]
        if not _is_non_negative_int(record_index):
            raise _exemption_error(
                f"{where} field 'record_index' must be a non-negative integer"
            )

        record_id = exemption["record_id"]
        if record_id is not None and not isinstance(record_id, str):
            raise _exemption_error(
                f"{where} field 'record_id' must be a string or null"
            )

        field = exemption["field"]
        if not isinstance(field, str):
            raise _exemption_error(
                f"{where} field 'field' must be a string"
            )

        reason = exemption["reason"]
        if not isinstance(reason, str) or not reason:
            raise _exemption_error(
                f"{where} field 'reason' must be a non-empty string"
            )

        target = _target_of(exemption)
        if target in seen_targets:
            earlier_id, earlier_reason = seen_targets[target]
            if earlier_reason == reason:
                raise _exemption_error(
                    f"{where} duplicates exemption {earlier_id!r} for rule "
                    f"{rule_id!r}, record_index {record_index}, "
                    f"record_id {record_id!r}, field {field!r}"
                )
            raise _exemption_error(
                f"{where} conflicts with exemption {earlier_id!r}: the same "
                f"violation (rule {rule_id!r}, record_index {record_index}, "
                f"record_id {record_id!r}, field {field!r}) is waived with "
                f"different reasons"
            )
        seen_targets[target] = (exemption_id, reason)

        validated.append(exemption)

    # Every exemption must match a reported violation. Identifying keys of
    # a validate violation cannot legitimately be missing, so a violation
    # without them is reported as malformed input rather than silently
    # left unmatchable.
    violation_targets = set()
    for violation in violations:
        if not isinstance(violation, dict) or not _VIOLATION_KEYS <= set(
            violation
        ):
            raise _exemption_error(
                "result must be a validate result containing violations "
                "with rule_id, record_index, record_id and field"
            )
        violation_targets.add(
            tuple(violation[key] for key in _TARGET_KEYS)
        )

    for exemption in validated:
        target = _target_of(exemption)
        if target not in violation_targets:
            raise _exemption_error(
                f"exemption {exemption['exemption_id']!r} matches no "
                f"reported violation (rule_id {exemption['rule_id']!r}, "
                f"record_index {exemption['record_index']}, "
                f"record_id {exemption['record_id']!r}, "
                f"field {exemption['field']!r})"
            )

    return validated


def apply_violation_exemptions(
    result: Any,
    exemptions: Any,
) -> Dict[str, Any]:
    """Partition a validate result's violations into active and waived.

    Only single-field violations are considered; ``composite_results`` in
    ``result`` (if present) are ignored and never appear in the output.

    :param result: a result dictionary as returned by
        :func:`data_quality.validate`; its ``violations`` list is the
        input partitioned by the exemptions.
    :param exemptions: list of exemption objects, each with exactly the
        keys ``exemption_id`` (non-empty string, unique), ``rule_id``
        (string), ``record_index`` (non-negative integer, never boolean),
        ``record_id`` (string or null), ``field`` (string) and ``reason``
        (non-empty string). An exemption matches a violation when all of
        ``rule_id``, ``record_index``, ``record_id`` and ``field`` are
        equal.
    :returns: ``{"status": "ok", "summary": {...},
        "active_violations": [...], "waived_violations": [...]}``. Both
        lists keep the original violation dictionaries in their original
        order; waived violations are copied and additionally carry
        ``exemption_id`` and ``reason``. The summary carries
        ``input_violation_count``, ``active_violation_count``,
        ``waived_violation_count`` and ``exemption_count``, where
        ``input_violation_count`` equals the active plus waived counts.
        The run is "passed" exactly when ``active_violations`` is empty.
    :raises InvalidExemptionError: when ``result`` is not a validate
        result, an exemption is malformed, exemption ids or targets are
        duplicated, two exemptions waive the same violation with
        different reasons, or an exemption matches no reported violation.
    """
    if not isinstance(result, dict) or not isinstance(
        result.get("violations"), list
    ):
        raise _exemption_error(
            "result must be a validate result object with a 'violations' list"
        )
    violations = result["violations"]

    validated = _validate_exemptions(exemptions, violations)
    waiver_by_target = {
        _target_of(exemption): (
            exemption["exemption_id"],
            exemption["reason"],
        )
        for exemption in validated
    }

    active_violations: List[Dict[str, Any]] = []
    waived_violations: List[Dict[str, Any]] = []
    for violation in violations:
        target = tuple(violation[key] for key in _TARGET_KEYS)
        waiver = waiver_by_target.get(target)
        if waiver is None:
            active_violations.append(violation)
        else:
            waived = dict(violation)
            waived["exemption_id"] = waiver[0]
            waived["reason"] = waiver[1]
            waived_violations.append(waived)

    return {
        "status": STATUS_OK,
        "summary": {
            "input_violation_count": len(violations),
            "active_violation_count": len(active_violations),
            "waived_violation_count": len(waived_violations),
            "exemption_count": len(validated),
        },
        "active_violations": active_violations,
        "waived_violations": waived_violations,
    }
