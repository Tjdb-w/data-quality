"""Dataset-level quality gates over the existing single-field rules.

The public entry point is :func:`evaluate_quality_gates`. It validates
``records`` against ``rules`` with the unchanged single-field engine
(:func:`data_quality.validate`) and then aggregates, per gate, the
violations of one source rule into a failed-record ratio. A gate passes
when ``failed_count / record_count`` is no greater than its
``max_failed_ratio`` and fails otherwise; the offending violations are
returned as anomaly samples.

Each gate carries exactly the keys ``rule_id``, ``source_rule_id``,
``max_failed_ratio`` and ``severity``:

* ``rule_id`` is a non-empty string unique across the gate list.
* ``source_rule_id`` must reference the ``id`` of one of ``rules``.
* ``max_failed_ratio`` is a JSON number from 0 to 1 inclusive; booleans
  are not numbers.
* ``severity`` is one of ``error``, ``warning`` or ``info``.

An empty record list never runs the ratio check: every gate is reported
as ``SKIPPED_EMPTY_DATASET`` with ``ratio`` null and empty samples, and
the outcome counts neither as passed nor as failed.

Malformed gate structures, duplicated gate ``rule_id`` values, illegal
severities or ratios raise :class:`InvalidQualityGateRuleError`
(a :class:`ValueError`); a gate naming an unknown source rule raises
:class:`UnknownQualityGateSourceError` (a :class:`LookupError`).
Record structures raise :class:`~data_quality.validator.InvalidInputError`
and rule definitions raise :class:`~data_quality.validator.InvalidRuleError`,
both produced by the underlying single-field engine.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .validator import InvalidInputError, InvalidRuleError, validate

__all__ = [
    "evaluate_quality_gates",
    "InvalidQualityGateRuleError",
    "UnknownQualityGateSourceError",
]

_GATE_KEYS = frozenset(
    {"rule_id", "source_rule_id", "max_failed_ratio", "severity"}
)

_SEVERITIES = frozenset({"error", "warning", "info"})

_STATUS_PASSED = "PASSED"
_STATUS_FAILED = "FAILED"
_STATUS_SKIPPED_EMPTY = "SKIPPED_EMPTY_DATASET"

_SAMPLE_KEYS = ("record_index", "record_id", "field", "value", "message")


class InvalidQualityGateRuleError(ValueError):
    """A quality gate is malformed, duplicated or carries illegal values."""

    code = "INVALID_QUALITY_GATE_RULE"


class UnknownQualityGateSourceError(LookupError):
    """A quality gate names a source_rule_id that no rule defines."""

    code = "UNKNOWN_QUALITY_GATE_SOURCE"


def _gate_error(message: str) -> InvalidQualityGateRuleError:
    return InvalidQualityGateRuleError(message)


def _is_number(value: Any) -> bool:
    # bool is a subclass of int, but JSON true/false are not numbers.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_gates(gates: Any) -> List[Dict[str, Any]]:
    if not isinstance(gates, list):
        raise _gate_error("gates must be a list of gate objects")

    seen_rule_ids: set = set()
    validated: List[Dict[str, Any]] = []

    for index, gate in enumerate(gates):
        where = f"gate {index}"
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
                f"gate {rule_id!r} field 'source_rule_id' must be a "
                "non-empty string"
            )

        severity = gate["severity"]
        if severity not in _SEVERITIES:
            raise _gate_error(
                f"gate {rule_id!r} has invalid severity: {severity!r} "
                "(expected one of 'error', 'warning', 'info')"
            )

        max_ratio = gate["max_failed_ratio"]
        if not _is_number(max_ratio):
            raise _gate_error(
                f"gate {rule_id!r} field 'max_failed_ratio' must be a "
                "JSON number"
            )
        if not 0 <= max_ratio <= 1:
            raise _gate_error(
                f"gate {rule_id!r} field 'max_failed_ratio' must be between "
                f"0 and 1, got {max_ratio}"
            )

        validated.append(gate)

    return validated


def _to_sample(violation: Dict[str, Any]) -> Dict[str, Any]:
    return {key: violation[key] for key in _SAMPLE_KEYS}


def evaluate_quality_gates(
    dataset: Any,
    records: Any,
    rules: Any,
    gates: Any,
) -> Dict[str, Any]:
    """Evaluate dataset-level ratio quality gates.

    The single-field ``rules`` keep their current definitions and
    judgements: they are compiled and applied to ``records`` exactly as by
    :func:`data_quality.validate`, so malformed records raise
    :class:`~data_quality.validator.InvalidInputError` and malformed rules
    raise :class:`~data_quality.validator.InvalidRuleError`. Gates are
    fully validated (structure, unique ``rule_id``, severity and ratio)
    before any source reference is resolved.

    :param dataset: dataset identifier echoed in the report; not
        interpreted or validated by this entry point.
    :param records: list of JSON objects (plain ``dict`` instances).
    :param rules: list of single-field rule objects with ``id``, ``type``
        and ``options``.
    :param gates: list of gate objects with exactly the keys
        ``rule_id``, ``source_rule_id``, ``max_failed_ratio`` and
        ``severity``; evaluated in array order.
    :returns: ``{"dataset": ..., "record_count": n, "results": [...]}``.
        Each result carries exactly ``rule_id``, ``source_rule_id``,
        ``status``, ``failed_count``, ``record_count``, ``ratio`` and
        ``samples``. ``status`` is ``PASSED`` when
        ``failed_count / record_count <= max_failed_ratio`` and
        ``FAILED`` otherwise. An empty record list yields
        ``SKIPPED_EMPTY_DATASET`` with ``ratio`` null and empty
        ``samples``. Each sample preserves the source violation's
        ``record_index``, ``record_id``, ``field``, ``value`` and
        ``message`` in record order.
    :raises ValueError: :class:`InvalidQualityGateRuleError` on
        malformed gates, duplicated gate ids, bad severity or ratio.
    :raises LookupError: :class:`UnknownQualityGateSourceError` when a
        gate references a rule id that ``rules`` does not define.
    """
    # Single-field rules are compiled before records are examined inside
    # validate(), preserving the existing rule-error precedence.
    validation = validate(records, rules)
    validated_gates = _validate_gates(gates)

    rule_ids = {rule["id"] for rule in rules}
    for gate in validated_gates:
        if gate["source_rule_id"] not in rule_ids:
            raise UnknownQualityGateSourceError(
                f"gate {gate['rule_id']!r} references an unknown "
                f"source_rule_id: {gate['source_rule_id']!r}"
            )

    record_count = validation["summary"]["record_count"]

    if record_count == 0:
        results = [
            {
                "rule_id": gate["rule_id"],
                "source_rule_id": gate["source_rule_id"],
                "status": _STATUS_SKIPPED_EMPTY,
                "failed_count": 0,
                "record_count": 0,
                "ratio": None,
                "samples": [],
            }
            for gate in validated_gates
        ]
        return {
            "dataset": dataset,
            "record_count": 0,
            "results": results,
        }

    # validate() emits violations rule by rule and records in order, so
    # grouping by rule_id keeps every gate's samples in record order.
    violations_by_rule: Dict[str, List[Dict[str, Any]]] = {}
    for violation in validation["violations"]:
        violations_by_rule.setdefault(violation["rule_id"], []).append(
            violation
        )

    results: List[Dict[str, Any]] = []
    for gate in validated_gates:
        samples = [
            _to_sample(violation)
            for violation in violations_by_rule.get(
                gate["source_rule_id"], []
            )
        ]
        failed_count = len(samples)
        ratio = failed_count / record_count
        status = (
            _STATUS_PASSED
            if ratio <= gate["max_failed_ratio"]
            else _STATUS_FAILED
        )
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

    return {
        "dataset": dataset,
        "record_count": record_count,
        "results": results,
    }
