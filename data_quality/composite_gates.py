"""Dataset-level quality gates over the existing cross-field composite rules.

The public entry point is :func:`evaluate_composite_quality_gates`. It
reuses the current composite rule definitions and per-record verdicts
(:func:`data_quality.register_composite_rules` /
:func:`data_quality.evaluate_composite_rules`) and aggregates, per gate,
the ratio of *decidable* records that violate a referenced composite
rule:

* ``failed_count`` is the number of records the source rule judges
  ``FAILED``;
* ``skipped_count`` is the number of records skipped
  (``SKIPPED_MISSING_FIELD``) because a participating field is absent;
* ``evaluated_count`` is the number of decidable records
  (``PASSED`` or ``FAILED``);
* ``record_count`` is the number of records in the dataset;
* ``ratio`` is ``failed_count / evaluated_count`` when
  ``evaluated_count > 0`` and ``None`` otherwise.

A gate is ``PASSED`` when ``ratio <= max_failed_ratio`` and ``FAILED``
otherwise. When no record is decidable -- an empty dataset, or a dataset
where every record is skipped -- the gate is
``SKIPPED_NO_EVALUATED_RECORDS`` with ``ratio`` of ``null`` and no
samples; it neither passes nor fails. Gates are aggregated in their
given order and every failed record is kept as a sample in record order.

Each gate carries exactly the keys ``rule_id``, ``source_rule_id``,
``max_failed_ratio`` and ``severity`` -- the same four-key shape as a
single-field gate. ``rule_id`` must be a unique non-empty string,
``source_rule_id`` a non-empty string referencing a declared composite
rule, ``severity`` one of ``error`` / ``warning`` / ``info`` and
``max_failed_ratio`` a JSON number in ``[0, 1]`` (booleans are not
numbers).

Malformed input (``dataset`` / ``records``) raises
:class:`InvalidCompositeGateInputError`; malformed composite rule
definitions raise the existing composite rule errors; malformed gates,
duplicated gate ``rule_id`` values or illegal severity / ratio values
raise :class:`InvalidCompositeGateRuleError`; a gate referencing an
unknown composite rule raises
:class:`UnknownCompositeGateSourceError`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .composite import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SKIPPED_MISSING_FIELD,
    evaluate_composite_rules,
    register_composite_rules,
)
from .validator import DataQualityError, _is_number

__all__ = [
    "evaluate_composite_quality_gates",
    "InvalidCompositeGateInputError",
    "InvalidCompositeGateRuleError",
    "UnknownCompositeGateSourceError",
    "GATE_SEVERITIES",
    "STATUS_PASSED",
    "STATUS_FAILED",
    "STATUS_SKIPPED_NO_EVALUATED_RECORDS",
]

GATE_SEVERITIES = ("error", "warning", "info")

STATUS_SKIPPED_NO_EVALUATED_RECORDS = "SKIPPED_NO_EVALUATED_RECORDS"

_GATE_KEYS = frozenset(
    {"rule_id", "source_rule_id", "max_failed_ratio", "severity"}
)


class InvalidCompositeGateInputError(DataQualityError):
    """The composite-gate payload (dataset/records) is malformed."""

    code = "INVALID_COMPOSITE_GATE_INPUT"


class InvalidCompositeGateRuleError(DataQualityError):
    """A composite gate is malformed, duplicated or carries bad options."""

    code = "INVALID_COMPOSITE_GATE_RULE"


class UnknownCompositeGateSourceError(LookupError):
    """A composite gate references a composite rule that is not declared."""

    code = "UNKNOWN_COMPOSITE_GATE_SOURCE"


def _validate_gates(gates: Any) -> List[Dict[str, Any]]:
    if not isinstance(gates, list):
        raise InvalidCompositeGateRuleError(
            "gates must be a list of gate objects"
        )

    seen_rule_ids: set = set()
    for index, gate in enumerate(gates):
        where = f"gate[{index}]"
        if not isinstance(gate, dict):
            raise InvalidCompositeGateRuleError(f"{where} must be an object")

        keys = set(gate)
        if keys != _GATE_KEYS:
            missing = sorted(_GATE_KEYS - keys)
            if missing:
                raise InvalidCompositeGateRuleError(
                    f"{where} is missing keys: {missing}"
                )
            unknown = sorted(keys - _GATE_KEYS)
            raise InvalidCompositeGateRuleError(
                f"{where} has unsupported keys: {unknown}"
            )

        rule_id = gate["rule_id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise InvalidCompositeGateRuleError(
                f"{where} field 'rule_id' must be a non-empty string"
            )
        if rule_id in seen_rule_ids:
            raise InvalidCompositeGateRuleError(
                f"duplicate gate rule_id: {rule_id!r}"
            )
        seen_rule_ids.add(rule_id)

        source_rule_id = gate["source_rule_id"]
        if not isinstance(source_rule_id, str) or not source_rule_id:
            raise InvalidCompositeGateRuleError(
                f"{where} field 'source_rule_id' must be a non-empty string"
            )

        severity = gate["severity"]
        if not isinstance(severity, str) or severity not in GATE_SEVERITIES:
            raise InvalidCompositeGateRuleError(
                f"gate {rule_id!r}: severity must be one of "
                f"{list(GATE_SEVERITIES)}, got {severity!r}"
            )

        max_failed_ratio = gate["max_failed_ratio"]
        if not _is_number(max_failed_ratio):
            raise InvalidCompositeGateRuleError(
                f"gate {rule_id!r}: max_failed_ratio must be a JSON number"
            )
        if not 0 <= max_failed_ratio <= 1:
            raise InvalidCompositeGateRuleError(
                f"gate {rule_id!r}: max_failed_ratio must be between 0 and "
                f"1, got {max_failed_ratio!r}"
            )

    return gates


def _to_sample(
    result: Dict[str, Any], rule: Dict[str, Any]
) -> Dict[str, Any]:
    """Build one FAILED sample from a composite per-record result.

    ``field_values`` is frozen in the rule's field order and only carries
    fields that are actually present (a failed record always carries the
    whole field set, but this keeps the snapshot independent of that
    invariant). ``failed_conditions`` lists the unsatisfied conditions in
    definition order, each with its ``type`` and the fields the compiled
    condition references; the per-record context only carries
    type/satisfied, so compiled conditions and context outcomes are paired
    by their definition index.
    """
    fields = rule["fields"]
    record_context = result["context"]
    field_values = {
        field: record_context["field_values"][field]
        for field in fields
        if field in record_context["field_values"]
    }

    failed_conditions: List[Dict[str, Any]] = []
    for compiled_condition, outcome in zip(
        rule["conditions"], record_context["conditions"]
    ):
        if outcome["satisfied"] is not False:
            continue
        entry: Dict[str, Any] = {"type": compiled_condition["type"]}
        # Expose the referenced fields in condition-definition order,
        # keeping only field references (skips ``format`` / ``equals``).
        for key, value in compiled_condition.items():
            if key != "type" and key.endswith("_field"):
                entry[key] = value
        failed_conditions.append(entry)

    return {
        "record_index": result["record_index"],
        "record_id": result["record_id"],
        "fields": list(fields),
        "field_values": field_values,
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
        report and used to register the composite rules.
    :param records: list of JSON object records (plain ``dict``
        instances).
    :param rules: non-empty list of composite rule objects as accepted by
        :func:`data_quality.register_composite_rules`; their definitions
        and per-record verdicts are unchanged.
    :param gates: list of gate objects with exactly the keys
        ``rule_id`` (unique non-empty string), ``source_rule_id``
        (references a declared composite rule), ``max_failed_ratio``
        (JSON number in ``[0, 1]``, never boolean) and ``severity`` (one
        of ``error`` / ``warning`` / ``info``). Gates are aggregated in
        array order.
    :returns: ``{"dataset": dataset, "results": [...]}`` where each
        result carries ``rule_id``, ``source_rule_id``, ``status``,
        ``severity``, ``failed_count``, ``skipped_count``,
        ``evaluated_count``, ``record_count``, ``ratio`` and ``samples``.
        ``status`` is ``PASSED`` when ``ratio <= max_failed_ratio`` and
        ``FAILED`` otherwise; when no record is decidable (empty dataset
        or every record skipped) it is ``SKIPPED_NO_EVALUATED_RECORDS``
        with ``ratio`` ``None``. Each sample keeps the failed record's
        ``record_index`` and ``record_id`` plus ``fields`` (declaration
        order), ``field_values`` (frozen in field order) and
        ``failed_conditions`` (unsatisfied conditions in definition
        order, each with ``type`` and the referenced fields).
    :raises ValueError: :class:`InvalidCompositeGateInputError` on a
        malformed ``dataset`` / ``records`` payload, the existing
        composite rule errors on malformed rule definitions and
        :class:`InvalidCompositeGateRuleError` on malformed gates,
        duplicated gate ids or illegal severity / ratio.
    :raises LookupError: :class:`UnknownCompositeGateSourceError` when a
        gate names a composite rule that ``rules`` does not declare.
    """
    # The payload's dataset is an input concern; rules reuse the composite
    # registration semantics and their stable error codes.
    if not isinstance(dataset, str) or not dataset:
        raise InvalidCompositeGateInputError(
            "dataset must be a non-empty string"
        )

    # Definitions are compiled and validated (composite rules first, then
    # gates) before any record is examined. Registration is
    # all-or-nothing and keeps the composite module's stable error codes.
    rule_set = register_composite_rules(dataset, rules)
    validated_gates = _validate_gates(gates)

    rule_ids = {rule["rule_id"] for rule in rule_set["rules"]}
    for gate in validated_gates:
        if gate["source_rule_id"] not in rule_ids:
            raise UnknownCompositeGateSourceError(
                f"gate {gate['rule_id']!r} references an unknown "
                f"source_rule_id: {gate['source_rule_id']!r}"
            )

    # Input validation mirrors evaluate_composite_rules: a non-list or a
    # non-object record is invalid input rather than a bad definition.
    if not isinstance(records, list):
        raise InvalidCompositeGateInputError("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InvalidCompositeGateInputError(
                f"records[{index}] must be a JSON object"
            )

    record_count = len(records)
    results_by_rule = evaluate_composite_rules(records, rule_set)

    # evaluate_composite_rules emits one result per (record, rule) in
    # record order then rule order; group them without changing order and
    # index the compiled rules so samples can recover each condition's
    # field references (the per-record context keeps only type/satisfied).
    rules_by_id = {rule["rule_id"]: rule for rule in rule_set["rules"]}
    per_rule: Dict[str, List[Dict[str, Any]]] = {}
    for result in results_by_rule:
        per_rule.setdefault(result["rule_id"], []).append(result)

    report_results: List[Dict[str, Any]] = []
    for gate in validated_gates:
        source_rule = rules_by_id[gate["source_rule_id"]]
        rule_results = per_rule.get(gate["source_rule_id"], [])

        failed_results = [
            result
            for result in rule_results
            if result["status"] == STATUS_FAILED
        ]
        skipped_count = sum(
            1
            for result in rule_results
            if result["status"] == STATUS_SKIPPED_MISSING_FIELD
        )
        evaluated_count = sum(
            1
            for result in rule_results
            if result["status"] in (STATUS_PASSED, STATUS_FAILED)
        )
        failed_count = len(failed_results)

        if evaluated_count == 0:
            status = STATUS_SKIPPED_NO_EVALUATED_RECORDS
            ratio = None
        else:
            ratio = failed_count / evaluated_count
            if ratio <= gate["max_failed_ratio"]:
                status = STATUS_PASSED
            else:
                status = STATUS_FAILED

        samples = [_to_sample(result, source_rule) for result in failed_results]

        report_results.append(
            {
                "rule_id": gate["rule_id"],
                "source_rule_id": gate["source_rule_id"],
                "status": status,
                "severity": gate["severity"],
                "failed_count": failed_count,
                "skipped_count": skipped_count,
                "evaluated_count": evaluated_count,
                "record_count": record_count,
                "ratio": ratio,
                "samples": samples,
            }
        )

    return {"dataset": dataset, "results": report_results}
