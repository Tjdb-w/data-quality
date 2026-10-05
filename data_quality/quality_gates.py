"""Dataset-level quality gates over the existing single-field rules.

The public entry point is :func:`evaluate_quality_gates`. It reuses the
current single-field rule definitions and verdicts
(:func:`data_quality.validate`) and aggregates, per gate, the ratio of
records that violate a referenced source rule:

* ``failed_count`` is the number of violating records for the gate's
  ``source_rule_id``;
* ``record_count`` is the number of records in the dataset;
* ``ratio`` is ``failed_count / record_count``.

A gate is ``PASSED`` when ``ratio <= max_failed_ratio`` and ``FAILED``
otherwise. Gates are aggregated in their given order and every violating
record is kept as a sample in record order. An empty dataset yields
``SKIPPED_EMPTY_DATASET`` with ``ratio`` of ``null`` and no samples; it
neither passes nor fails.

Each gate carries exactly the keys ``rule_id``, ``source_rule_id``,
``max_failed_ratio`` and ``severity``. ``rule_id`` must be a unique
non-empty string, ``source_rule_id`` a non-empty string referencing a
declared rule, ``severity`` one of ``error`` / ``warning`` / ``info`` and
``max_failed_ratio`` a JSON number in ``[0, 1]`` (booleans are not
numbers).

Malformed records raise :class:`~data_quality.validator.InvalidInputError`
and malformed rule definitions raise
:class:`~data_quality.validator.InvalidRuleError`; malformed gates,
duplicated gate ``rule_id`` values or illegal severity / ratio values
raise :class:`InvalidQualityGateRuleError`; a gate referencing an
unknown rule raises :class:`UnknownQualityGateSourceError`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .validator import (
    InvalidInputError,
    _check_rule,
    _is_number,
    _validate_and_compile_rules,
)

__all__ = [
    "evaluate_quality_gates",
    "InvalidQualityGateRuleError",
    "UnknownQualityGateSourceError",
    "GATE_SEVERITIES",
    "STATUS_PASSED",
    "STATUS_FAILED",
    "STATUS_SKIPPED_EMPTY_DATASET",
]

GATE_SEVERITIES = ("error", "warning", "info")

STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED_EMPTY_DATASET = "SKIPPED_EMPTY_DATASET"

_GATE_KEYS = frozenset(
    {"rule_id", "source_rule_id", "max_failed_ratio", "severity"}
)

_SAMPLE_KEYS = ("record_index", "record_id", "field", "value", "message")


class InvalidQualityGateRuleError(ValueError):
    """A quality gate is malformed, duplicated or carries bad options."""

    code = "INVALID_QUALITY_GATE_RULE"


class UnknownQualityGateSourceError(LookupError):
    """A quality gate references a rule_id that no rule declares."""

    code = "UNKNOWN_QUALITY_GATE_SOURCE"


def _gate_error(message: str) -> InvalidQualityGateRuleError:
    return InvalidQualityGateRuleError(message)


def _validate_gates(gates: Any) -> List[Dict[str, Any]]:
    if not isinstance(gates, list):
        raise _gate_error("gates must be a list of gate objects")

    seen_rule_ids: set = set()
    for index, gate in enumerate(gates):
        where = f"gate[{index}]"
        if not isinstance(gate, dict):
            raise _gate_error(f"{where} must be an object")

        keys = set(gate)
        if keys != _GATE_KEYS:
            missing = sorted(_GATE_KEYS - keys)
            if missing:
                raise _gate_error(f"{where} is missing keys: {missing}")
            unknown = sorted(keys - _GATE_KEYS)
            raise _gate_error(f"{where} has unsupported keys: {unknown}")

        rule_id = gate["rule_id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise _gate_error(
                f"{where} field 'rule_id' must be a non-empty string"
            )
        if rule_id in seen_rule_ids:
            raise _gate_error(f"duplicate gate rule_id: {rule_id!r}")
        seen_rule_ids.add(rule_id)

        source_rule_id = gate["source_rule_id"]
        if not isinstance(source_rule_id, str) or not source_rule_id:
            raise _gate_error(
                f"{where} field 'source_rule_id' must be a non-empty string"
            )

        severity = gate["severity"]
        if not isinstance(severity, str) or severity not in GATE_SEVERITIES:
            raise _gate_error(
                f"gate {rule_id!r}: severity must be one of "
                f"{list(GATE_SEVERITIES)}, got {severity!r}"
            )

        max_failed_ratio = gate["max_failed_ratio"]
        if not _is_number(max_failed_ratio):
            raise _gate_error(
                f"gate {rule_id!r}: max_failed_ratio must be a JSON number"
            )
        if not 0 <= max_failed_ratio <= 1:
            raise _gate_error(
                f"gate {rule_id!r}: max_failed_ratio must be between 0 and "
                f"1, got {max_failed_ratio!r}"
            )

    return gates


def _to_sample(violation: Dict[str, Any]) -> Dict[str, Any]:
    return {key: violation[key] for key in _SAMPLE_KEYS}


def evaluate_quality_gates(
    dataset: Any,
    records: Any,
    rules: Any,
    gates: Any,
) -> Dict[str, Any]:
    """Aggregate single-field rule failure ratios into dataset gates.

    :param dataset: dataset identifier echoed in the report (may be
        ``None``).
    :param records: list of JSON object records (plain ``dict``
        instances).
    :param rules: list of single-field rule objects as accepted by
        :func:`data_quality.validate`; their definitions and verdicts are
        unchanged.
    :param gates: list of gate objects with exactly the keys
        ``rule_id`` (unique non-empty string), ``source_rule_id``
        (references a declared rule), ``max_failed_ratio`` (JSON number
        in ``[0, 1]``, never boolean) and ``severity`` (one of
        ``error`` / ``warning`` / ``info``). Gates are aggregated in
        array order.
    :returns: ``{"dataset": dataset, "results": [...]}`` where each
        result carries ``rule_id``, ``source_rule_id``, ``status``,
        ``failed_count``, ``record_count``, ``ratio`` and ``samples``.
        ``status`` is ``PASSED`` when ``ratio <= max_failed_ratio`` and
        ``FAILED`` otherwise. Each sample keeps the violating record's
        ``record_index``, ``record_id``, ``field``, ``value`` and
        ``message`` in record order. An empty dataset yields
        ``SKIPPED_EMPTY_DATASET`` with ``record_count`` 0, ``ratio``
        ``None`` and empty samples and is neither passed nor failed.
    :raises ValueError: :class:`~data_quality.validator.InvalidInputError`
        on malformed records,
        :class:`~data_quality.validator.InvalidRuleError` on malformed
        rule definitions and :class:`InvalidQualityGateRuleError` on
        malformed gates, duplicated gate ids or illegal severity / ratio.
    :raises LookupError: :class:`UnknownQualityGateSourceError` when a
        gate names a rule that ``rules`` does not declare.
    """
    # Definitions are compiled and validated (rules first, then gates)
    # before any record is examined.
    compiled_rules = _validate_and_compile_rules(rules)
    validated_gates = _validate_gates(gates)

    rule_ids = {rule["id"] for rule in compiled_rules}
    for gate in validated_gates:
        if gate["source_rule_id"] not in rule_ids:
            raise UnknownQualityGateSourceError(
                f"gate {gate['rule_id']!r} references an unknown "
                f"source_rule_id: {gate['source_rule_id']!r}"
            )

    if not isinstance(records, list):
        raise InvalidInputError("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InvalidInputError(
                f"records[{index}] must be a JSON object"
            )

    record_count = len(records)

    violations_by_rule: Dict[str, List[Dict[str, Any]]] = {}
    if record_count:
        all_violations: List[Dict[str, Any]] = []
        for rule in compiled_rules:
            _check_rule(rule, records, all_violations)
        for violation in all_violations:
            violations_by_rule.setdefault(violation["rule_id"], []).append(
                violation
            )

    results: List[Dict[str, Any]] = []
    for gate in validated_gates:
        rule_violations = violations_by_rule.get(gate["source_rule_id"], [])
        samples = [_to_sample(violation) for violation in rule_violations]
        failed_count = len(samples)

        if record_count == 0:
            status = STATUS_SKIPPED_EMPTY_DATASET
            ratio = None
        else:
            ratio = failed_count / record_count
            if ratio <= gate["max_failed_ratio"]:
                status = STATUS_PASSED
            else:
                status = STATUS_FAILED

        results.append(
            {
                "rule_id": gate["rule_id"],
                "source_rule_id": gate["source_rule_id"],
                "status": status,
                "failed_count": failed_count,
                "record_count": record_count,
                "ratio": ratio,
                "samples": samples,
            }
        )

    return {"dataset": dataset, "results": results}
