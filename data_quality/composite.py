"""Cross-field composite consistency rules.

The public entry points are:

* :func:`register_composite_rules` -- register (compile) a set of
  cross-field rules, rejecting every definition whose per-record result
  cannot be determined. Registration is all-or-nothing.
* :func:`evaluate_composite_rules` -- evaluate the registered rules over
  records, one conclusion per (record, rule) pair.
* :func:`query_composite_results` -- filter complete evaluation results by
  ``rule_id``, severity and record locator in stable order.
* :func:`composite_results_to_impact_inputs` -- adapt failed composite
  results to the existing field-impact / anomaly-sample-location input
  shape without changing that entry point. The participating
  ``{dataset, field}`` references can also be queried directly with
  :func:`data_quality.trace_field_lineage` for upstream/downstream inputs
  and outputs.

A composite rule relates several fields of the *same* record. Four
condition types are supported and may be combined with AND:

* ``field_equal``        -- two fields carry JSON-equal values
* ``field_not_equal``    -- two fields carry non-equal values
* ``date_before``        -- one date field is strictly before another;
                            an optional ``format`` declares the expected
                            date format (default/only ``YYYY-MM-DD``); an
                            unsupported format is rejected at registration
* ``required_when``      -- a field must be present and non-null while a
                            prerequisite field equals a given value

Every error subclasses :class:`~data_quality.validator.DataQualityError`
(itself a :class:`ValueError`) and carries a stable ``code`` plus the
offending ``rule_id`` / ``field_name`` where applicable.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

from .validator import DataQualityError

__all__ = [
    "register_composite_rules",
    "evaluate_composite_rules",
    "query_composite_results",
    "composite_results_to_impact_inputs",
    "sample_id_for_record",
    "CompositeRuleSetError",
    "DuplicateRuleIdError",
    "InvalidSeverityError",
    "UnsupportedCompositeConditionError",
    "InvalidCompositeRuleError",
    "InvalidRecordReferenceError",
    "SEVERITIES",
    "CONDITION_TYPES",
    "STATUS_PASSED",
    "STATUS_FAILED",
    "STATUS_SKIPPED_MISSING_FIELD",
]

SEVERITIES = ("error", "warning", "info")

CONDITION_EQUAL = "field_equal"
CONDITION_NOT_EQUAL = "field_not_equal"
CONDITION_DATE_BEFORE = "date_before"
CONDITION_REQUIRED_WHEN = "required_when"
CONDITION_TYPES = (
    CONDITION_EQUAL,
    CONDITION_NOT_EQUAL,
    CONDITION_DATE_BEFORE,
    CONDITION_REQUIRED_WHEN,
)

# The single supported calendar-date format: strict ``YYYY-MM-DD``.
SUPPORTED_DATE_FORMAT = "YYYY-MM-DD"

STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED_MISSING_FIELD = "SKIPPED_MISSING_FIELD"

_RULE_KEYS = frozenset({"rule_id", "fields", "conditions", "severity"})
_BINARY_FIELD_KEYS = ("left_field", "right_field")
_DATE_FIELD_KEYS = ("earlier_field", "later_field")


class CompositeRuleSetError(DataQualityError):
    """The composite rule set itself is empty or structurally unusable."""

    code = "INVALID_RULE_SET"

    def __init__(self, message: str, rule_id: Optional[str] = None):
        super().__init__(message)
        self.rule_id = rule_id
        self.field_name = None


class DuplicateRuleIdError(CompositeRuleSetError):
    """A ``rule_id`` appears more than once in the same rule set."""

    code = "DUPLICATE_RULE_ID"

    def __init__(self, rule_id: str):
        super().__init__(f"duplicate composite rule id: {rule_id!r}", rule_id)


class InvalidSeverityError(CompositeRuleSetError):
    """A rule carries a severity outside the supported vocabulary."""

    code = "INVALID_SEVERITY"

    def __init__(self, rule_id: str, severity: Any):
        super().__init__(
            f"rule {rule_id!r}: severity must be one of "
            f"{list(SEVERITIES)}, got {severity!r}",
            rule_id,
        )
        self.field_name = "severity"


class UnsupportedCompositeConditionError(CompositeRuleSetError):
    """A condition declares a ``type`` that is not supported."""

    code = "UNSUPPORTED_COMPOSITE_CONDITION"

    def __init__(self, rule_id: str, condition_type: Any):
        super().__init__(
            f"rule {rule_id!r}: unsupported composite condition type: "
            f"{condition_type!r}",
            rule_id,
        )
        self.field_name = "type"


class InvalidCompositeRuleError(DataQualityError):
    """A rule definition is valid-looking but can never be executed.

    Carries the offending ``rule_id`` and a locatable ``field_name`` (the
    offending field reference, or a structural key such as ``fields`` /
    ``conditions``).
    """

    code = "INVALID_COMPOSITE_RULE"

    def __init__(self, rule_id: Any, field_name: str, message: str):
        full_message = f"rule {rule_id!r}: {message}"
        super().__init__(full_message)
        self.rule_id = rule_id
        self.field_name = field_name


class InvalidRecordReferenceError(DataQualityError):
    """A ``record_refs`` entry cannot be resolved to an input record."""

    code = "INVALID_RECORD_REFERENCE"

    def __init__(self, message: str):
        super().__init__(message)
        self.rule_id = None
        self.field_name = None


def _json_equal(left: Any, right: Any) -> bool:
    """JSON value equality (booleans never equal numbers)."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[k], right[k]) for k in left
        )
    return left == right


def _parse_date(value: Any) -> Optional[date]:
    """Parse a strict ``YYYY-MM-DD`` calendar date; ``None`` if invalid."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    if value[4] != "-" or value[7] != "-":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_field_name(value: Any, rule_id: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidCompositeRuleError(
            rule_id, where, f"{where} must be a non-empty field name string"
        )
    return value


def _validate_condition(
    condition: Any,
    index: int,
    rule_id: Any,
    declared_fields: Set[str],
) -> Dict[str, Any]:
    """Validate one condition object and return its normalized form."""
    where = f"conditions[{index}]"
    if not isinstance(condition, dict):
        raise InvalidCompositeRuleError(
            rule_id, "conditions", f"{where} must be an object"
        )

    condition_type = condition.get("type")
    if "type" not in condition:
        raise InvalidCompositeRuleError(
            rule_id, "type", f"{where} is missing 'type'"
        )
    if not isinstance(condition_type, str):
        raise InvalidCompositeRuleError(
            rule_id, "type", f"{where}.type must be a string"
        )
    if condition_type not in CONDITION_TYPES:
        raise UnsupportedCompositeConditionError(rule_id, condition_type)

    def check_ref(key: str) -> str:
        if key not in condition:
            raise InvalidCompositeRuleError(
                rule_id, key, f"{where} is missing '{key}'"
            )
        field_name = _check_field_name(condition[key], rule_id, key)
        if field_name not in declared_fields:
            raise InvalidCompositeRuleError(
                rule_id,
                field_name,
                f"{where}.{key} references field {field_name!r} that is "
                f"not declared in the rule's field set",
            )
        return field_name

    if condition_type in (CONDITION_EQUAL, CONDITION_NOT_EQUAL):
        unknown = set(condition) - {"type", *_BINARY_FIELD_KEYS}
        if unknown:
            raise InvalidCompositeRuleError(
                rule_id,
                sorted(unknown)[0],
                f"{where} has unsupported keys: {sorted(unknown)}",
            )
        left_field = check_ref("left_field")
        right_field = check_ref("right_field")
        if left_field == right_field:
            raise InvalidCompositeRuleError(
                rule_id,
                right_field,
                f"{where} references the same field on both sides: "
                f"{left_field!r}",
            )
        return {
            "type": condition_type,
            "left_field": left_field,
            "right_field": right_field,
        }

    if condition_type == CONDITION_DATE_BEFORE:
        allowed = {"type", "format", *_DATE_FIELD_KEYS}
        unknown = set(condition) - allowed
        if unknown:
            raise InvalidCompositeRuleError(
                rule_id,
                sorted(unknown)[0],
                f"{where} has unsupported keys: {sorted(unknown)}",
            )
        earlier_field = check_ref("earlier_field")
        later_field = check_ref("later_field")
        if earlier_field == later_field:
            raise InvalidCompositeRuleError(
                rule_id,
                later_field,
                f"{where} references the same field on both sides: "
                f"{earlier_field!r}",
            )
        date_format = condition.get("format", SUPPORTED_DATE_FORMAT)
        if not isinstance(date_format, str) or date_format != SUPPORTED_DATE_FORMAT:
            # An unknown date format would make per-record results
            # undecidable, so the rule cannot be registered at all.
            raise InvalidCompositeRuleError(
                rule_id,
                "format",
                f"{where}.format must be the supported date format "
                f"{SUPPORTED_DATE_FORMAT!r}, got {date_format!r}",
            )
        return {
            "type": condition_type,
            "earlier_field": earlier_field,
            "later_field": later_field,
            "format": date_format,
        }

    # required_when
    allowed = {"type", "when_field", "equals", "required_field"}
    unknown = set(condition) - allowed
    if unknown:
        raise InvalidCompositeRuleError(
            rule_id,
            sorted(unknown)[0],
            f"{where} has unsupported keys: {sorted(unknown)}",
        )
    when_field = check_ref("when_field")
    required_field = check_ref("required_field")
    if when_field == required_field:
        raise InvalidCompositeRuleError(
            rule_id,
            required_field,
            f"{where}.required_field must differ from when_field: "
            f"{when_field!r}",
        )
    # ``equals`` is optional; default null. Any JSON value is allowed.
    equals = condition["equals"] if "equals" in condition else None
    return {
        "type": condition_type,
        "when_field": when_field,
        "equals": equals,
        "required_field": required_field,
    }


def _compile_rule(rule: Any, seen_ids: Set[str]) -> Dict[str, Any]:
    if not isinstance(rule, dict):
        raise CompositeRuleSetError("each composite rule must be an object")

    keys = set(rule)
    missing = sorted(_RULE_KEYS - keys)
    if missing:
        field_name = missing[0]
        raise InvalidCompositeRuleError(
            rule.get("rule_id"),
            field_name,
            f"rule is missing keys: {missing}",
        )
    unknown = sorted(keys - _RULE_KEYS)
    if unknown:
        raise InvalidCompositeRuleError(
            rule.get("rule_id"),
            unknown[0],
            f"rule has unsupported keys: {unknown}",
        )

    rule_id = rule["rule_id"]
    if not isinstance(rule_id, str) or not rule_id:
        raise InvalidCompositeRuleError(
            rule_id, "rule_id", "rule_id must be a non-empty string"
        )
    if rule_id in seen_ids:
        raise DuplicateRuleIdError(rule_id)

    raw_fields = rule["fields"]
    if not isinstance(raw_fields, list) or not raw_fields:
        raise InvalidCompositeRuleError(
            rule_id,
            "fields",
            "fields must be a non-empty list of field name strings",
        )
    fields: List[str] = []
    for index, field_name in enumerate(raw_fields):
        checked = _check_field_name(
            field_name, rule_id, f"fields[{index}]"
        )
        if checked in fields:
            raise InvalidCompositeRuleError(
                rule_id,
                checked,
                f"field {checked!r} is listed more than once in the "
                f"rule's field set",
            )
        fields.append(checked)

    severity = rule["severity"]
    if not isinstance(severity, str) or severity not in SEVERITIES:
        raise InvalidSeverityError(rule_id, severity)

    raw_conditions = rule["conditions"]
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise InvalidCompositeRuleError(
            rule_id,
            "conditions",
            "conditions must be a non-empty list of condition objects",
        )

    declared_fields = set(fields)
    conditions = [
        _validate_condition(condition, index, rule_id, declared_fields)
        for index, condition in enumerate(raw_conditions)
    ]

    return {
        "rule_id": rule_id,
        "fields": fields,
        "severity": severity,
        "conditions": conditions,
    }


def register_composite_rules(
    dataset_id: Any, rules: Any
) -> Dict[str, Any]:
    """Register and compile a composite rule set.

    Registration is all-or-nothing: the first rule that cannot be executed
    deterministically aborts registration and no rule from the set is
    retained.

    :param dataset_id: non-empty dataset identifier string; echoed into
        every result.
    :param rules: non-empty list of rule objects, each with exactly the
        keys ``rule_id`` (non-empty unique string), ``fields`` (non-empty
        list of distinct non-empty field names the rule relates),
        ``severity`` (one of ``error`` / ``warning`` / ``info``) and
        ``conditions`` (non-empty list; every condition must reference
        fields declared in ``fields``).
    :returns: ``{"dataset_id": ..., "rules": [compiled rules]}`` preserving
        the input registration order.
    :raises ValueError: :class:`CompositeRuleSetError`
        (``INVALID_RULE_SET``), :class:`DuplicateRuleIdError`
        (``DUPLICATE_RULE_ID``), :class:`InvalidSeverityError`
        (``INVALID_SEVERITY``), :class:`UnsupportedCompositeConditionError`
        (``UNSUPPORTED_COMPOSITE_CONDITION``) or
        :class:`InvalidCompositeRuleError` (``INVALID_COMPOSITE_RULE``).
    """
    if not isinstance(dataset_id, str) or not dataset_id:
        raise CompositeRuleSetError(
            "dataset_id must be a non-empty string when composite rules "
            "are registered"
        )
    if not isinstance(rules, list) or not rules:
        raise CompositeRuleSetError(
            "composite rules must be a non-empty list"
        )

    compiled: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for rule in rules:
        compiled_rule = _compile_rule(rule, seen_ids)
        seen_ids.add(compiled_rule["rule_id"])
        compiled.append(compiled_rule)

    return {"dataset_id": dataset_id, "rules": compiled}


def _condition_satisfied(condition: Dict[str, Any], record: Dict[str, Any]) -> bool:
    condition_type = condition["type"]

    if condition_type == CONDITION_EQUAL:
        return _json_equal(
            record[condition["left_field"]], record[condition["right_field"]]
        )

    if condition_type == CONDITION_NOT_EQUAL:
        return not _json_equal(
            record[condition["left_field"]], record[condition["right_field"]]
        )

    if condition_type == CONDITION_DATE_BEFORE:
        earlier = _parse_date(record[condition["earlier_field"]])
        later = _parse_date(record[condition["later_field"]])
        if earlier is None or later is None:
            # Illegal date values cannot satisfy a date-order constraint.
            return False
        return earlier < later

    # required_when: while the prerequisite holds, the required field
    # must be present (guaranteed by the field-set check) and non-null.
    if not _json_equal(record[condition["when_field"]], condition["equals"]):
        return True
    return record[condition["required_field"]] is not None


def _record_id(record: Dict[str, Any]) -> Any:
    return record["id"] if "id" in record else None


def _evaluate_one(
    record: Dict[str, Any],
    index: int,
    rule: Dict[str, Any],
    dataset_id: str,
) -> Dict[str, Any]:
    fields = rule["fields"]
    missing = [field for field in fields if field not in record]
    if missing:
        status = STATUS_SKIPPED_MISSING_FIELD
        condition_results = [
            {"type": condition["type"], "satisfied": None}
            for condition in rule["conditions"]
        ]
    else:
        condition_results = []
        satisfied_all = True
        for condition in rule["conditions"]:
            satisfied = _condition_satisfied(condition, record)
            condition_results.append(
                {"type": condition["type"], "satisfied": satisfied}
            )
            if not satisfied:
                satisfied_all = False
        status = STATUS_PASSED if satisfied_all else STATUS_FAILED

    return {
        "dataset_id": dataset_id,
        "record_index": index,
        "record_id": _record_id(record),
        "rule_id": rule["rule_id"],
        "fields": list(fields),
        "status": status,
        "severity": rule["severity"],
        "context": {
            # Snapshot only the participating fields, in registration
            # order, so downstream consumers see immutable evidence.
            "field_values": {
                field: record.get(field)
                for field in fields
                if field in record
            },
            "missing_fields": missing,
            "conditions": condition_results,
        },
    }


def _resolve_record_refs(
    record_refs: Any, records: List[Dict[str, Any]]
) -> List[Tuple[int, Dict[str, Any]]]:
    """Resolve record locators to ``(index, record)`` pairs.

    First occurrence wins (duplicate locators are deduplicated); the
    original reference order is preserved.
    """
    if not isinstance(record_refs, list):
        raise InvalidRecordReferenceError("record_refs must be a list")

    resolved: List[Tuple[int, Dict[str, Any]]] = []
    seen_indices: Set[int] = set()
    for ref_index, ref in enumerate(record_refs):
        where = f"record_refs[{ref_index}]"
        if not isinstance(ref, dict):
            raise InvalidRecordReferenceError(f"{where} must be an object")
        keys = set(ref)
        if not keys or not keys <= {"record_index", "record_id"}:
            raise InvalidRecordReferenceError(
                f"{where} may only contain 'record_index' and/or 'record_id'"
            )

        if "record_index" in ref:
            record_index = ref["record_index"]
            # bool is a subclass of int but JSON true/false are not indexes.
            if isinstance(record_index, bool) or not isinstance(record_index, int):
                raise InvalidRecordReferenceError(
                    f"{where}.record_index must be an integer"
                )
            if not 0 <= record_index < len(records):
                raise InvalidRecordReferenceError(
                    f"{where}.record_index is out of range: {record_index}"
                )
            index = record_index
        else:
            index = -1
            for candidate_index, candidate in enumerate(records):
                if "id" in candidate and candidate["id"] == ref["record_id"]:
                    index = candidate_index
                    break
            if index < 0:
                raise InvalidRecordReferenceError(
                    f"{where}.record_id does not match any record: "
                    f"{ref['record_id']!r}"
                )

        if "record_id" in ref:
            record_id = ref["record_id"]
            if not isinstance(record_id, str) or not record_id:
                raise InvalidRecordReferenceError(
                    f"{where}.record_id must be a non-empty string"
                )
            record = records[index]
            if record.get("id") != record_id or "id" not in record:
                raise InvalidRecordReferenceError(
                    f"{where} locates record index {index} but its record id "
                    f"is {record.get('id')!r}, not {record_id!r}"
                )

        if index not in seen_indices:
            seen_indices.add(index)
            resolved.append((index, records[index]))

    return resolved


def evaluate_composite_rules(
    records: Any,
    rule_set: Dict[str, Any],
    record_refs: Any = None,
) -> List[Dict[str, Any]]:
    """Evaluate registered composite rules over records.

    Records are read one by one; within one record every rule is evaluated
    in registration order. A record missing any field of a rule's target
    field set yields :data:`STATUS_SKIPPED_MISSING_FIELD` for that rule;
    the rule is neither passed nor failed for that record, and other rules
    keep executing.

    :param records: list of JSON object records.
    :param rule_set: output of :func:`register_composite_rules`.
    :param record_refs: optional list of locators (``{"record_index": i}``
        and/or ``{"record_id": ...}``) restricting evaluation to specific
        records; a locator that cannot be resolved raises
        :class:`InvalidRecordReferenceError`. When omitted every record is
        evaluated.
    :returns: complete result list, ordered by record order then by rule
        registration order.
    """
    if not isinstance(records, list):
        raise InvalidRecordReferenceError("records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InvalidRecordReferenceError(
                f"records[{index}] must be a JSON object"
            )

    dataset_id = rule_set["dataset_id"]
    rules = rule_set["rules"]

    if record_refs is None:
        selected: List[Tuple[int, Dict[str, Any]]] = list(enumerate(records))
    else:
        selected = _resolve_record_refs(record_refs, records)

    results: List[Dict[str, Any]] = []
    for index, record in selected:
        for rule in rules:
            results.append(_evaluate_one(record, index, rule, dataset_id))
    return results


def query_composite_results(
    results: Any,
    rule_id: Any = None,
    severity: Any = None,
    record_index: Any = None,
    record_id: Any = None,
) -> Dict[str, Any]:
    """Filter complete composite results without changing their order.

    Every supplied filter is AND-combined; omitted filters do not restrict.

    :param results: result list produced by
        :func:`evaluate_composite_rules` (or a previously filtered one).
    :param rule_id: keep results of this rule id.
    :param severity: keep results with this severity level.
    :param record_index: keep results located at this record index.
    :param record_id: keep results located at this record id.
    :returns: ``{"count": n, "results": [...]}`` in the original stable
        order.
    """
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    if rule_id is not None and not isinstance(rule_id, str):
        raise ValueError("rule_id filter must be a string")
    if severity is not None and not isinstance(severity, str):
        raise ValueError("severity filter must be a string")
    if record_index is not None and (
        isinstance(record_index, bool) or not isinstance(record_index, int)
    ):
        raise ValueError("record_index filter must be an integer")
    if record_id is not None and not isinstance(record_id, str):
        raise ValueError("record_id filter must be a string")

    filtered = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("each result must be an object")
        if rule_id is not None and result.get("rule_id") != rule_id:
            continue
        if severity is not None and result.get("severity") != severity:
            continue
        if (
            record_index is not None
            and result.get("record_index") != record_index
        ):
            continue
        if record_id is not None and result.get("record_id") != record_id:
            continue
        filtered.append(result)

    return {"count": len(filtered), "results": filtered}


def sample_id_for_record(result: Dict[str, Any]) -> str:
    """Derive the stable anomaly-sample id used by the downstream adapters.

    A record's ``id`` is used when it is a non-empty string, otherwise a
    deterministic ``"record:<index>"`` locator. Evaluation results carry
    the same ``record_index`` / ``record_id`` pair, so jumping from a
    correlated anomaly back to the validation result resolves to the same
    record and participating fields.
    """
    record_id = result.get("record_id")
    if isinstance(record_id, str) and record_id:
        return record_id
    return f"record:{result['record_index']}"


def composite_results_to_impact_inputs(
    results: Any,
) -> Dict[str, Any]:
    """Adapt FAILED composite results to ``analyze_field_impacts`` inputs.

    :returns: ``{"validationResults": [...], "anomalySamples": {...}}``.
        Each failed rule becomes one ``failed`` validation result touching
        every participating ``{dataset, field}``; every failed record
        becomes a locatable anomaly sample keyed by the same sample id the
        correlation adapter uses, carrying the participating field values.
        The caller supplies ``datasets``, ``lineageEdges`` and
        ``seedFields`` to complete the impact-analysis payload.
    """
    if not isinstance(results, list):
        raise ValueError("results must be a list")

    rule_fields: Dict[str, Set[Tuple[str, str]]] = {}
    rule_samples: Dict[str, Set[str]] = {}
    samples: Dict[str, Dict[str, Any]] = {}

    for result in results:
        if not isinstance(result, dict):
            raise ValueError("each result must be an object")
        if result.get("status") != STATUS_FAILED:
            continue

        rule_id = result["rule_id"]
        if rule_id not in rule_fields:
            rule_fields[rule_id] = set()
            rule_samples[rule_id] = set()
        dataset_id = result["dataset_id"]
        for field in result["fields"]:
            rule_fields[rule_id].add((dataset_id, field))

        sample_id = sample_id_for_record(result)
        rule_samples[rule_id].add(sample_id)
        # A record violating several rules contributes one anomaly sample;
        # merge every failed rule's participating field values into it so
        # the sample shows the complete evidence set.
        sample = samples.setdefault(
            sample_id,
            {"dataset": dataset_id, "fieldValues": {}},
        )
        sample["fieldValues"].update(
            result.get("context", {}).get("field_values", {})
        )

    validation_results = [
        {
            "ruleId": rule_id,
            "status": "failed",
            "fields": [
                {"dataset": dataset, "field": field}
                for dataset, field in sorted(rule_fields[rule_id])
            ],
            "failedSampleIds": sorted(rule_samples[rule_id]),
        }
        for rule_id in sorted(rule_fields)
    ]

    return {
        "validationResults": validation_results,
        "anomalySamples": samples,
    }
