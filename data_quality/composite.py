"""Cross-field (composite) consistency rules.

The three public entry points mirror the existing rule lifecycle:

* :func:`register_composite_rules` validates rule definitions against the
  schema of a dataset and returns compiled rules (registration).
* :func:`evaluate_composite_rules` evaluates the compiled rules record by
  record and keeps one complete result per rule/record pair (execution).
* :func:`query_composite_results` filters those results by ``rule_id``,
  severity and record locator (result query).

:func:`composite_impact_inputs` adapts the failed results to the
``validationResults`` / ``anomalySamples`` fragments consumed by
:func:`data_quality.analyze_field_impacts`, so anomalous samples can jump
straight back to their record and participating fields and keep using the
existing upstream/downstream lineage semantics. The participating field
coordinates (``{"dataset", "field"}``) are also the same node coordinates
the field lineage tracers use.

Definition problems raise :class:`CompositeRuleError` (a
:class:`ValueError`) with one of the dedicated ``code`` values; records that
cannot be located raise :class:`InvalidRecordReferenceError` (a
:class:`LookupError`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "register_composite_rules",
    "evaluate_composite_rules",
    "query_composite_results",
    "composite_impact_inputs",
    "composite_sample_id",
    "CompositeRuleError",
    "InvalidRecordReferenceError",
    "PASSED",
    "FAILED",
    "SKIPPED_MISSING_FIELD",
    "SEVERITIES",
]

PASSED = "PASSED"
FAILED = "FAILED"
SKIPPED_MISSING_FIELD = "SKIPPED_MISSING_FIELD"

# Registration outcomes are ordered the same way the checks run so a single
# batch can never be ambiguous.
SEVERITIES = ("error", "warning", "info")
_CONDITION_TYPES = (
    "field_equals",
    "field_not_equals",
    "date_order",
    "required_if",
)
_RULE_KEYS = frozenset({"rule_id", "fields", "severity", "conditions"})
_CONDITION_KEYS = frozenset({"type", "left", "right", "when", "field", "format"})

# Recognised strptime/strftime directive characters (Python 3.8+). Anything
# else after a ``%`` makes the format undeterminable and is rejected at
# registration.
_DATE_DIRECTIVES = frozenset("aAbBcdfGHIjMmPSUuVWwXxYyYzZ%")
_DATE_FORMAT_FLAGS = frozenset("-_0^# ")
_RESULT_KEYS = frozenset(
    {
        "dataset_id",
        "record_index",
        "record_id",
        "rule_id",
        "fields",
        "conclusion",
        "severity",
        "context",
    }
)
_QUERY_KEYS = frozenset({"rule_id", "severity", "record_index", "record_id"})


class CompositeRuleError(ValueError):
    """A composite rule set or rule definition cannot be accepted.

    The dedicated ``code`` (``INVALID_RULE_SET``, ``DUPLICATE_RULE_ID``,
    ``INVALID_SEVERITY``, ``UNSUPPORTED_COMPOSITE_CONDITION`` or
    ``INVALID_COMPOSITE_RULE``) is also emitted by the CLI, and ``rule_id``
    / ``field`` pinpoint the offending definition when one exists.
    """

    code = "INVALID_COMPOSITE_RULE"

    def __init__(
        self,
        message: str,
        code: str = "INVALID_COMPOSITE_RULE",
        rule_id: Optional[str] = None,
        field: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.rule_id = rule_id
        self.field = field


class InvalidRecordReferenceError(LookupError):
    """Execution input cannot be located to a concrete record."""

    code = "INVALID_RECORD_REFERENCE"


def _error(
    message: str,
    code: str = "INVALID_COMPOSITE_RULE",
    rule_id: Any = None,
    field: Any = None,
) -> CompositeRuleError:
    return CompositeRuleError(
        message,
        code=code,
        rule_id=rule_id if isinstance(rule_id, str) else None,
        field=field if isinstance(field, str) else None,
    )


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


def _schema_field_names(schema: Any) -> List[str]:
    """Return the dataset field names in declaration order."""
    if isinstance(schema, dict):
        names = list(schema)
    elif isinstance(schema, (list, tuple)):
        names = list(schema)
    else:
        raise _error(
            "dataset schema must be a field-name list or an object mapping "
            "field names to types"
        )
    for index, name in enumerate(names):
        if not isinstance(name, str) or not name:
            raise _error(
                f"dataset schema field at index {index} must be a non-empty "
                "field name string"
            )
    return names


def _require_field_name(value: Any, rule_id: str, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(
            f"rule {rule_id!r}: {where} must reference a non-empty field name",
            rule_id=rule_id,
        )
    return value


def _check_declared(
    field_name: Any, declared: Sequence[str], rule_id: str, where: str
) -> str:
    name = _require_field_name(field_name, rule_id, where)
    if name not in declared:
        raise _error(
            f"rule {rule_id!r}: {where} references an undeclared field: "
            f"{name!r}",
            rule_id=rule_id,
            field=name,
        )
    return name


def _validate_conditions(
    raw_conditions: Any,
    declared: Sequence[str],
    field_types: Dict[str, str],
    rule_id: str,
    fields: List[str],
) -> List[Dict[str, Any]]:
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise _error(
            f"rule {rule_id!r}: conditions must be a non-empty list",
            rule_id=rule_id,
        )

    compiled: List[Dict[str, Any]] = []
    used: set = set()
    for index, condition in enumerate(raw_conditions):
        where = f"conditions[{index}]"
        if not isinstance(condition, dict):
            raise _error(
                f"rule {rule_id!r}: {where} must be an object",
                rule_id=rule_id,
            )

        unknown = set(condition) - _CONDITION_KEYS
        if unknown:
            raise _error(
                f"rule {rule_id!r}: {where} has unsupported keys: "
                f"{sorted(unknown)}",
                rule_id=rule_id,
            )

        condition_type = condition.get("type")
        if not isinstance(condition_type, str):
            raise _error(
                f"rule {rule_id!r}: {where}.type must be a string",
                rule_id=rule_id,
            )
        if condition_type not in _CONDITION_TYPES:
            raise _error(
                f"rule {rule_id!r}: unsupported composite condition: "
                f"{condition_type!r}",
                code="UNSUPPORTED_COMPOSITE_CONDITION",
                rule_id=rule_id,
            )

        if condition_type in ("field_equals", "field_not_equals", "date_order"):
            comparison_keys = {"type", "left", "right"}
            if condition_type == "date_order":
                comparison_keys.add("format")
            extra = set(condition) - comparison_keys
            if extra:
                raise _error(
                    f"rule {rule_id!r}: {where} has unsupported keys for "
                    f"{condition_type!r}: {sorted(extra)}",
                    rule_id=rule_id,
                )
            left = _check_declared(
                condition.get("left"), declared, rule_id, f"{where}.left"
            )
            right = _check_declared(
                condition.get("right"), declared, rule_id, f"{where}.right"
            )
            if left == right:
                raise _error(
                    f"rule {rule_id!r}: {where} must reference two distinct "
                    f"fields, got {left!r} on both sides",
                    rule_id=rule_id,
                    field=left,
                )
            entry: Dict[str, Any] = {"type": condition_type, "left": left,
                                     "right": right}
            used.update((left, right))

            if condition_type == "date_order":
                # Date fields must be usable as dates; types that can never
                # produce a deterministic result are refused at registration.
                for side_name in (left, right):
                    field_type = field_types.get(side_name)
                    if field_type is not None and not _is_date_type(field_type):
                        raise _error(
                            f"rule {rule_id!r}: {where} field {side_name!r} "
                            f"is not a date field (declared type "
                            f"{field_type!r})",
                            rule_id=rule_id,
                            field=side_name,
                        )
                date_format: Optional[str] = None
                if "format" in condition:
                    date_format = condition["format"]
                    if not isinstance(date_format, str) or not date_format:
                        raise _error(
                            f"rule {rule_id!r}: {where}.format must be a "
                            "non-empty strptime format string",
                            rule_id=rule_id,
                            field=left,
                        )
                    _validate_date_format(date_format, rule_id, where, left)
                entry["format"] = date_format

        else:  # required_if
            extra = set(condition) - {"type", "when", "field"}
            if extra:
                raise _error(
                    f"rule {rule_id!r}: {where} has unsupported keys for "
                    f"{condition_type!r}: {sorted(extra)}",
                    rule_id=rule_id,
                )
            when = _check_declared(
                condition.get("when"), declared, rule_id, f"{where}.when"
            )
            target = _check_declared(
                condition.get("field"), declared, rule_id, f"{where}.field"
            )
            if when == target:
                raise _error(
                    f"rule {rule_id!r}: {where} cannot require a field based "
                    f"on itself: {target!r}",
                    rule_id=rule_id,
                    field=target,
                )
            entry = {"type": condition_type, "when": when, "field": target}
            used.update((when, target))

        compiled.append(entry)

    # The declared target field set must be exactly the fields the
    # conditions actually relate; otherwise the result could name fields
    # the rule never evaluates.
    if set(fields) != used:
        unused = sorted(set(fields) - used)
        unlisted = sorted(used - set(fields))
        if unused:
            detail = f"declared but never used: {unused}"
            pinpoint = unused[0]
        else:
            detail = f"used by conditions but not declared: {unlisted}"
            pinpoint = unlisted[0]
        raise _error(
            f"rule {rule_id!r}: target fields must be exactly the fields "
            f"referenced by its conditions ({detail})",
            rule_id=rule_id,
            field=pinpoint,
        )

    return compiled


def _validate_date_format(
    date_format: str, rule_id: str, where: str, field_name: str
) -> None:
    """Reject strptime formats that could never determine a result."""
    index = 0
    while index < len(date_format):
        char = date_format[index]
        if char != "%":
            index += 1
            continue
        index += 1
        # A trailing "%" or an unrecognised directive makes the format
        # unusable: execution could never decide a date order.
        if index >= len(date_format):
            raise _error(
                f"rule {rule_id!r}: {where}.format ends with an incomplete "
                "directive",
                rule_id=rule_id,
                field=field_name,
            )
        directive = date_format[index]
        if directive in _DATE_FORMAT_FLAGS:
            index += 1
            if index >= len(date_format):
                raise _error(
                    f"rule {rule_id!r}: {where}.format ends with an "
                    "incomplete directive",
                    rule_id=rule_id,
                    field=field_name,
                )
            directive = date_format[index]
        if directive not in _DATE_DIRECTIVES:
            raise _error(
                f"rule {rule_id!r}: {where}.format is not a valid strptime "
                f"format: unknown directive %{directive}",
                rule_id=rule_id,
                field=field_name,
            )
        index += 1


def _is_date_type(field_type: str) -> bool:
    return field_type in ("date", "datetime")


def register_composite_rules(dataset: Any, rules: Any) -> List[Dict[str, Any]]:
    """Validate and compile cross-field consistency rules for one dataset.

    :param dataset: ``{"dataset_id": non-empty string, "fields": [...]}``.
        ``fields`` is either a list of non-empty field names or an object
        mapping field names to type strings (``"date"`` / ``"datetime"``
        mark date fields). Field order is preserved.
    :param rules: non-empty list of rule objects, each with exactly the
        keys ``rule_id`` (non-empty string, unique within the batch),
        ``fields`` (non-empty list of declared, distinct field names),
        ``severity`` (one of :data:`SEVERITIES`) and ``conditions``
        (non-empty list, combined with AND). Each condition is one of:

        * ``{"type": "field_equals", "left", "right"}``
        * ``{"type": "field_not_equals", "left", "right"}``
        * ``{"type": "date_order", "left", "right", "format"?}`` (left
          must denote a date not later than right; ``format`` is an
          optional strptime format validated at registration)
        * ``{"type": "required_if", "when", "field"}`` (when ``when`` is
          present and non-null, ``field`` must be present and non-null)

        The two sides of a comparison must be distinct declared fields;
        ``required_if`` cannot reference the same field twice; the target
        ``fields`` must be exactly the fields referenced by the
        conditions. Definitions are rejected wholesale -- nothing is
        partially registered.
    :returns: compiled rules in registration order; pass them unchanged to
        :func:`evaluate_composite_rules`.
    :raises CompositeRuleError: with code ``INVALID_RULE_SET`` (not a
        non-empty list), ``DUPLICATE_RULE_ID``, ``INVALID_SEVERITY``,
        ``UNSUPPORTED_COMPOSITE_CONDITION`` or ``INVALID_COMPOSITE_RULE``
        (any other defect, including undeclared fields, self references
        and non-date fields used in ``date_order``).
    """
    if not isinstance(dataset, dict):
        raise _error("dataset must be an object")
    dataset_id = dataset.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise _error("dataset.dataset_id must be a non-empty string")
    raw_schema = dataset.get("fields")
    if isinstance(raw_schema, dict):
        names = _schema_field_names(raw_schema)
        field_types: Dict[str, str] = {}
        for index, name in enumerate(names):
            field_type = raw_schema[name]
            if not isinstance(field_type, str) or not field_type:
                raise _error(
                    f"dataset field type for {name!r} at index {index} must "
                    "be a non-empty type string"
                )
            field_types[name] = field_type
    else:
        names = _schema_field_names(raw_schema)
        field_types = {}

    declared = list(names)

    if not isinstance(rules, list) or not rules:
        raise CompositeRuleError(
            "composite rules must be a non-empty list",
            code="INVALID_RULE_SET",
        )

    compiled: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise _error(f"rules[{index}] must be an object")
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            raise _error(
                f"rules[{index}] has unsupported keys: {sorted(unknown)}"
            )

        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            raise _error(
                f"rules[{index}] must have a non-empty string rule_id"
            )
        if rule_id in seen_ids:
            raise CompositeRuleError(
                f"duplicate composite rule id: {rule_id!r}",
                code="DUPLICATE_RULE_ID",
                rule_id=rule_id,
            )

        severity = rule.get("severity")
        if not isinstance(severity, str) or severity not in SEVERITIES:
            raise CompositeRuleError(
                f"rule {rule_id!r}: severity must be one of "
                f"{list(SEVERITIES)}, got {severity!r}",
                code="INVALID_SEVERITY",
                rule_id=rule_id,
            )

        raw_fields = rule.get("fields")
        if (
            not isinstance(raw_fields, list)
            or not raw_fields
            or not all(isinstance(name, str) for name in raw_fields)
        ):
            raise _error(
                f"rule {rule_id!r}: fields must be a non-empty list of "
                "field name strings",
                rule_id=rule_id,
            )
        fields: List[str] = []
        seen_fields: set = set()
        for pos, name in enumerate(raw_fields):
            checked = _check_declared(
                name, declared, rule_id, f"fields[{pos}]"
            )
            if checked in seen_fields:
                raise _error(
                    f"rule {rule_id!r}: field {checked!r} is listed more "
                    "than once",
                    rule_id=rule_id,
                    field=checked,
                )
            seen_fields.add(checked)
            fields.append(checked)

        conditions = _validate_conditions(
            rule.get("conditions"), declared, field_types, rule_id, fields
        )

        seen_ids.add(rule_id)
        compiled.append(
            {
                "rule_id": rule_id,
                "dataset_id": dataset_id,
                "fields": fields,
                "severity": severity,
                "conditions": conditions,
            }
        )

    return compiled


def _parse_date(value: Any, date_format: Optional[str] = None) -> Optional[date]:
    """Parse a JSON date value.

    ``date``/``datetime`` instances pass through. Strings are parsed with
    the rule-level ``date_format`` (strptime) when one was registered, or
    as ISO ``YYYY-MM-DD`` / full ISO 8601 date-times by default. Values
    that are not dates (including ``None``) return ``None``; malformed
    date strings raise so bad data is never silently compared.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if date_format is not None:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError as exc:
            raise ValueError(f"does not match format {date_format!r}: {value!r}") from exc
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError(f"not a valid ISO date: {value!r}") from exc


def _evaluate_condition(
    condition: Dict[str, Any], record: Dict[str, Any]
) -> bool:
    condition_type = condition["type"]
    if condition_type == "required_if":
        when_name = condition["when"]
        target = condition["field"]
        precondition = when_name in record and record[when_name] is not None
        if not precondition:
            return True
        return target in record and record[target] is not None

    left_name = condition["left"]
    right_name = condition["right"]
    left = record[left_name]
    right = record[right_name]

    if condition_type == "field_equals":
        return _json_equal(left, right)
    if condition_type == "field_not_equals":
        return not _json_equal(left, right)

    # date_order: left must be on or before right. Missing pieces are the
    # caller's responsibility (it skips the rule); present-but-unparseable
    # values are a hard failure of the condition.
    date_format = condition.get("format")
    try:
        left_date = _parse_date(left, date_format)
        right_date = _parse_date(right, date_format)
    except ValueError:
        return False
    if left_date is None or right_date is None:
        return False
    return left_date <= right_date


def _result(
    rule: Dict[str, Any],
    index: int,
    record_id: Any,
    conclusion: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "dataset_id": rule["dataset_id"],
        "record_index": index,
        "record_id": record_id,
        "rule_id": rule["rule_id"],
        "fields": list(rule["fields"]),
        "conclusion": conclusion,
        "severity": rule["severity"],
        "context": context,
    }


def evaluate_composite_rules(
    records: Any,
    compiled_rules: Any,
    dataset_id: Any = None,
) -> Dict[str, Any]:
    """Evaluate compiled composite rules record by record.

    Rules run in their registration order; within a rule the records are
    read in order. Every rule/record pair produces one complete result:

    * :data:`PASSED` -- every condition (AND) holds and all participating
      fields are present;
    * :data:`FAILED` -- the fields are present but the conditions do not
      all hold;
    * :data:`SKIPPED_MISSING_FIELD` -- at least one field the rule
      references is absent from the record. This is neither a pass nor a
      failure and never stops other rules from running.

    A failing record keeps producing results for every later rule, and a
    record violating several rules keeps one FAILED result per rule.

    :param records: list of JSON object records. Each record may carry an
      ``id`` field used as ``record_id`` (``null`` when absent).
    :param compiled_rules: the return value of
        :func:`register_composite_rules`.
    :param dataset_id: optional dataset id the records belong to; when
        given it must match the id the rules were registered for.
    :returns: ``{"dataset_id", "results", "summary"}`` where ``results`` is
        ordered by rule registration order and then record order, and
        ``summary`` counts ``record_count``, ``rule_count`` and the three
        conclusions.
    :raises InvalidRecordReferenceError: ``records`` is not a list of
        objects, ``compiled_rules`` is not a compiled rule list, or
        ``dataset_id`` does not match the registered dataset.
    """
    if not isinstance(compiled_rules, list) or not compiled_rules:
        raise InvalidRecordReferenceError(
            "compiled_rules must be a non-empty list returned by "
            "register_composite_rules"
        )
    expected_dataset: Any = None
    for pos, rule in enumerate(compiled_rules):
        if not isinstance(rule, dict) or "rule_id" not in rule:
            raise InvalidRecordReferenceError(
                f"compiled_rules[{pos}] is not a compiled composite rule"
            )
        if expected_dataset is None:
            expected_dataset = rule.get("dataset_id")
        elif rule.get("dataset_id") != expected_dataset:
            raise InvalidRecordReferenceError(
                "compiled_rules mix datasets "
                f"{expected_dataset!r} and {rule.get('dataset_id')!r}"
            )
    if dataset_id is not None and dataset_id != expected_dataset:
        raise InvalidRecordReferenceError(
            f"records belong to dataset {dataset_id!r} but the rules were "
            f"registered for {expected_dataset!r}"
        )

    if not isinstance(records, list):
        raise InvalidRecordReferenceError("records must be a list")

    results: List[Dict[str, Any]] = []
    counts = {PASSED: 0, FAILED: 0, SKIPPED_MISSING_FIELD: 0}

    for rule in compiled_rules:
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise InvalidRecordReferenceError(
                    f"records[{index}] must be a JSON object"
                )
            record_id = record["id"] if "id" in record else None

            missing = [
                name
                for name in rule["fields"]
                if name not in record
            ]
            if missing:
                conclusion = SKIPPED_MISSING_FIELD
                context = {
                    "conditions": [],
                    "missing_fields": missing,
                    "values": None,
                }
            else:
                values = {name: record[name] for name in rule["fields"]}
                condition_results = [
                    {
                        "type": condition["type"],
                        "fields": _condition_fields(condition),
                        "satisfied": _evaluate_condition(condition, record),
                    }
                    for condition in rule["conditions"]
                ]
                satisfied = all(item["satisfied"] for item in condition_results)
                conclusion = PASSED if satisfied else FAILED
                context = {
                    "conditions": condition_results,
                    "missing_fields": [],
                    "values": values,
                }

            counts[conclusion] += 1
            results.append(_result(rule, index, record_id, conclusion, context))

    return {
        "dataset_id": expected_dataset,
        "results": results,
        "summary": {
            "dataset_id": expected_dataset,
            "record_count": len(records),
            "rule_count": len(compiled_rules),
            "passed_count": counts[PASSED],
            "failed_count": counts[FAILED],
            "skipped_count": counts[SKIPPED_MISSING_FIELD],
        },
    }


def _condition_fields(condition: Dict[str, Any]) -> List[str]:
    if condition["type"] == "required_if":
        return [condition["when"], condition["field"]]
    return [condition["left"], condition["right"]]


def query_composite_results(results: Any, filters: Any = None) -> List[Dict[str, Any]]:
    """Filter composite evaluation results, preserving the stable order.

    :param results: either the ``{"results": [...]}`` dictionary returned
        by :func:`evaluate_composite_rules` or the bare result list.
    :param filters: optional object with any of ``rule_id`` (string or list
        of strings), ``severity`` (string or list of strings from
        :data:`SEVERITIES`) and record locators ``record_index`` (int) /
        ``record_id`` (string). Unknown filter keys, non-string severities
        or non-integer indices raise :class:`InvalidRecordReferenceError`.
    :returns: the matching complete result objects in their original
        (registration order, record order) sequence. The query never
        reshapes historical single-field results -- it only reads the
        composite result stream.
    """
    if isinstance(results, dict) and "results" in results:
        bare = results["results"]
    else:
        bare = results
    if not isinstance(bare, list):
        raise InvalidRecordReferenceError(
            "results must be a list or an evaluation output dictionary"
        )
    for pos, item in enumerate(bare):
        if not isinstance(item, dict) or not _RESULT_KEYS <= set(item):
            raise InvalidRecordReferenceError(
                f"results[{pos}] is not a composite evaluation result"
            )

    filters = filters or {}
    if not isinstance(filters, dict):
        raise InvalidRecordReferenceError("filters must be an object")
    unknown = set(filters) - _QUERY_KEYS
    if unknown:
        raise InvalidRecordReferenceError(
            f"unsupported result query filters: {sorted(unknown)}"
        )

    rule_ids = _as_filter_set(filters.get("rule_id"), "rule_id")
    severities = _as_filter_set(filters.get("severity"), "severity")
    if severities and any(level not in SEVERITIES for level in severities):
        raise InvalidRecordReferenceError(
            f"severity filter must be one of {list(SEVERITIES)}"
        )

    record_index = filters.get("record_index")
    if record_index is not None:
        if _is_bool(record_index) or not isinstance(record_index, int) or record_index < 0:
            raise InvalidRecordReferenceError(
                "record_index filter must be a non-negative integer"
            )
    record_id = filters.get("record_id")
    if record_id is not None and not isinstance(record_id, str):
        raise InvalidRecordReferenceError("record_id filter must be a string")

    selected: List[Dict[str, Any]] = []
    for item in bare:
        if rule_ids and item["rule_id"] not in rule_ids:
            continue
        if severities and item["severity"] not in severities:
            continue
        if record_index is not None and item["record_index"] != record_index:
            continue
        if record_id is not None and item["record_id"] != record_id:
            continue
        selected.append(item)
    return selected


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _as_filter_set(value: Any, name: str) -> set:
    if value is None:
        return set()
    if isinstance(value, list):
        if not all(isinstance(item, str) and item for item in value):
            raise InvalidRecordReferenceError(
                f"{name} filter entries must be non-empty strings"
            )
        return set(value)
    if not isinstance(value, str) or not value:
        raise InvalidRecordReferenceError(
            f"{name} filter must be a non-empty string or a list of strings"
        )
    return {value}


def composite_sample_id(result: Any) -> str:
    """Return the stable anomaly sample id for one composite result.

    The record's own ``id`` is used when present; otherwise the locator is
    the literal ``record:<record_index>``. Result locator and anomaly
    sample therefore always agree.
    """
    if not isinstance(result, dict) or "record_index" not in result:
        raise InvalidRecordReferenceError(
            "result must be a composite evaluation result"
        )
    if result.get("record_id") is not None:
        return result["record_id"]
    index = result["record_index"]
    if _is_bool(index) or not isinstance(index, int) or index < 0:
        raise InvalidRecordReferenceError(
            "result record_index must be a non-negative integer"
        )
    return f"record:{index}"


def _iter_results(results: Any, dataset_id: Any = None):
    if isinstance(results, dict) and "results" in results:
        return results["results"], results.get("dataset_id", dataset_id)
    if not isinstance(results, list):
        raise InvalidRecordReferenceError(
            "results must be a list or an evaluation output dictionary"
        )
    return results, dataset_id


def composite_impact_inputs(
    results: Any,
    dataset_id: Any = None,
) -> Dict[str, Any]:
    """Turn FAILED composite results into impact-analysis fragments.

    The returned object has the ``validationResults`` and
    ``anomalySamples`` pieces that :func:`data_quality.analyze_field_impacts`
    consumes; spreading them into an impact payload alongside the existing
    ``datasets`` / ``lineageEdges`` / ``seedFields`` lets the anomaly sample
    locator jump from a cross-field violation straight back to the same
    record (the sample id is the record locator) and participating fields
    (``{"dataset", "field"}`` refs, the same coordinates the field lineage
    tracers use), and the existing downstream lineage semantics then apply
    unchanged.

    Only FAILED results are anomalies. Rules keep registration order; the
    failed sample ids within a rule are sorted ascending; sample
    ``fieldValues`` merge the participating fields of every failed rule on
    the same record.
    """
    bare, resolved_dataset = _iter_results(results, dataset_id)

    rule_entries: Dict[str, Dict[str, Any]] = {}
    rule_order: List[str] = []
    samples: Dict[str, Dict[str, Any]] = {}

    for item in bare:
        if not isinstance(item, dict) or item.get("conclusion") != FAILED:
            continue
        rule_id = item["rule_id"]
        target_dataset = item.get("dataset_id", resolved_dataset)
        sample_id = composite_sample_id(item)

        if rule_id not in rule_entries:
            rule_entries[rule_id] = {
                "ruleId": rule_id,
                "status": "failed",
                "fields": [
                    {"dataset": target_dataset, "field": name}
                    for name in item["fields"]
                ],
                "_sample_ids": set(),
            }
            rule_order.append(rule_id)
        rule_entries[rule_id]["_sample_ids"].add(sample_id)

        values = (item.get("context") or {}).get("values") or {}
        sample = samples.setdefault(
            sample_id,
            {"dataset": target_dataset, "fieldValues": {}},
        )
        for name in item["fields"]:
            sample["fieldValues"][name] = values.get(name)

    validation_results = [
        {
            "ruleId": rule_id,
            "status": "failed",
            "fields": rule_entries[rule_id]["fields"],
            "failedSampleIds": sorted(rule_entries[rule_id]["_sample_ids"]),
        }
        for rule_id in rule_order
    ]
    return {
        "validationResults": validation_results,
        "anomalySamples": samples,
    }
