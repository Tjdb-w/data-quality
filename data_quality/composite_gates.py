"""Dataset-level quality gates over cross-field composite rules.

The public entry point is :func:`evaluate_composite_quality_gates`. It
reuses the existing composite rule definitions and per-record verdicts
(:func:`data_quality.register_composite_rules` /
:func:`data_quality.evaluate_composite_rules`) and aggregates, per gate,
the ratio of records whose multi-field consistency check fails:

* ``failed_count`` is the number of records where the gate's
  ``source_rule_id`` composite rule yields ``FAILED``;
* ``skipped_count`` is the number of records skipped because a
  participating field is missing (``SKIPPED_MISSING_FIELD``);
* ``evaluated_count`` is the number of records that could be judged
  (``record_count - skipped_count``);
* ``record_count`` is the number of records in the dataset;
* ``ratio`` is ``failed_count / evaluated_count`` when
  ``evaluated_count`` is greater than zero, otherwise ``null``.

A gate is ``PASSED`` when ``ratio <= max_failed_ratio`` and ``FAILED``
otherwise. An empty dataset or a dataset where every record is skipped
yields ``SKIPPED_NO_EVALUATED_RECORDS`` with ``ratio`` of ``null`` and no
samples; it neither passes nor fails. Gates are aggregated in their given
order and every ``FAILED`` record is kept as a sample in record order.

Each gate carries exactly the keys ``rule_id``, ``source_rule_id``,
``max_failed_ratio`` and ``severity`` with the same semantics as the
single-field quality gates (see :mod:`data_quality.quality_gates`).

Malformed records or a malformed/empty ``dataset`` raise
:class:`~data_quality.validator.InvalidInputError`; malformed composite
rules raise the composite rule errors from
:mod:`data_quality.composite`; malformed gates, duplicated gate
``rule_id`` values or illegal severity / ratio values raise
:class:`~data_quality.quality_gates.InvalidQualityGateRuleError`; a gate
referencing an undeclared composite rule raises
:class:`~data_quality.quality_gates.UnknownQualityGateSourceError`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .composite import (
    CONDITION_DATE_BEFORE,
    CONDITION_EQUAL,
    CONDITION_NOT_EQUAL,
    CONDITION_REQUIRED_WHEN,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SKIPPED_MISSING_FIELD,
    _evaluate_one,
    register_composite_rules,
)
from .quality_gates import (
    InvalidQualityGateRuleError,
    UnknownQualityGateSourceError,
    _validate_gates,
)
from .validator import InvalidInputError

__all__ = [
    "evaluate_composite_quality_gates",
    "InvalidQualityGateRuleError",
    "UnknownQualityGateSourceError",
    "STATUS_PASSED",
    "STATUS_FAILED",
    "STATUS_SKIPPED_NO_EVALUATED_RECORDS",
]

STATUS_SKIPPED_NO_EVALUATED_RECORDS = "SKIPPED_NO_EVALUATED_RECORDS"

# Field-reference keys reported per failed condition, by condition type.
_CONDITION_FIELD_KEYS = {
    CONDITION_EQUAL: ("left_field", "right_field"),
    CONDITION_NOT_EQUAL: ("left_field", "right_field"),
    CONDITION_DATE_BEFORE: ("earlier_field", "later_field"),
    CONDITION_REQUIRED_WHEN: ("when_field", "required_field"),
}


def _to_sample(
    evaluation: Dict[str, Any], rule: Dict[str, Any]
) -> Dict[str, Any]:
    failed_conditions: List[Dict[str, Any]] = []
    for condition, condition_result in zip(
        rule["conditions"], evaluation["context"]["conditions"]
    ):
        if condition_result["satisfied"] is not False:
            continue
        entry = {"type": condition["type"]}
        for key in _CONDITION_FIELD_KEYS[condition["type"]]:
            entry[key] = condition[key]
        failed_conditions.append(entry)

    field_values = evaluation["context"]["field_values"]
    return {
        "record_index": evaluation["record_index"],
        "record_id": evaluation["record_id"],
        "fields": list(rule["fields"]),
        # Snapshot the participating field values frozen in the rule's
        # declared field order.
        "field_values": {
            field: field_values.get(field) for field in rule["fields"]
        },
        "failed_conditions": failed_conditions,
    }


def evaluate_composite_quality_gates(
    dataset: Any,
    records: Any,
    rules: Any,
    gates: Any,
) -> Dict[str, Any]:
    """Aggregate composite-rule failure ratios into dataset gates.

    :param dataset: non-empty dataset identifier string; echoed into the
        report and into every per-record evaluation.
    :param records: list of JSON object records (plain ``dict``
        instances).
    :param rules: non-empty list of composite rule objects as accepted by
        :func:`data_quality.register_composite_rules`; their definitions
        and per-record verdicts are unchanged.
    :param gates: list of gate objects with exactly the keys
        ``rule_id`` (unique non-empty string), ``source_rule_id``
        (references a declared composite rule), ``max_failed_ratio``
        (JSON number in ``[0, 1]``, never boolean) and ``severity``
        (one of ``error`` / ``warning`` / ``info``). Gates are
        aggregated in array order.
    :returns: ``{"dataset": dataset, "results": [...]}`` where each
        result carries ``rule_id``, ``source_rule_id``, ``severity``,
        ``status``, ``failed_count``, ``skipped_count``,
        ``evaluated_count``, ``record_count``, ``ratio`` and ``samples``.
        ``status`` is ``PASSED`` when ``ratio <= max_failed_ratio`` and
        ``FAILED`` otherwise. Each sample keeps the failing record's
        ``record_index`` and ``record_id`` plus ``fields`` (the rule's
        declared field set), ``field_values`` (snapshot in declared
        field order) and ``failed_conditions`` (the unsatisfied
        conditions in definition order, each with its ``type`` and field
        references). An empty dataset or one where every record is
        skipped yields ``SKIPPED_NO_EVALUATED_RECORDS`` with ``ratio``
        ``None`` and empty samples and is neither passed nor failed.
    :raises ValueError: :class:`~data_quality.validator.InvalidInputError`
        on a malformed ``dataset`` or ``records``, the composite rule
        errors from :mod:`data_quality.composite` on malformed rules and
        :class:`~data_quality.quality_gates.InvalidQualityGateRuleError`
        on malformed gates, duplicated gate ids or illegal severity /
        ratio.
    :raises LookupError:
        :class:`~data_quality.quality_gates.UnknownQualityGateSourceError`
        when a gate names a composite rule that ``rules`` does not
        declare.
    """
    if not isinstance(dataset, str) or not dataset:
        raise InvalidInputError("dataset must be a non-empty string")

    # Definitions are compiled and validated (rules first, then gates)
    # before any record is examined.
    rule_set = register_composite_rules(dataset, rules)
    validated_gates = _validate_gates(gates)

    rules_by_id = {rule["rule_id"]: rule for rule in rule_set["rules"]}
    for gate in validated_gates:
        if gate["source_rule_id"] not in rules_by_id:
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

    results: List[Dict[str, Any]] = []
    for gate in validated_gates:
        rule = rules_by_id[gate["source_rule_id"]]
        failed_count = 0
        skipped_count = 0
        samples: List[Dict[str, Any]] = []
        for index, record in enumerate(records):
            evaluation = _evaluate_one(record, index, rule, dataset)
            status = evaluation["status"]
            if status == STATUS_SKIPPED_MISSING_FIELD:
                skipped_count += 1
            elif status == STATUS_FAILED:
                failed_count += 1
                samples.append(_to_sample(evaluation, rule))

        evaluated_count = record_count - skipped_count
        if evaluated_count == 0:
            status = STATUS_SKIPPED_NO_EVALUATED_RECORDS
            ratio = None
        else:
            ratio = failed_count / evaluated_count
            if ratio <= gate["max_failed_ratio"]:
                status = STATUS_PASSED
            else:
                status = STATUS_FAILED

        results.append(
            {
                "rule_id": gate["rule_id"],
                "source_rule_id": gate["source_rule_id"],
                "severity": gate["severity"],
                "status": status,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
                "evaluated_count": evaluated_count,
                "record_count": record_count,
                "ratio": ratio,
                "samples": samples,
            }
        )

    return {"dataset": dataset, "results": results}
