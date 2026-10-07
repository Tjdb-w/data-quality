"""Command line interface: ``dq validate``, ``dq profile``,
``dq exemptions``,
``dq lineage``,
``dq field-lineage``, ``dq lineage-paths``, ``dq field-lineage-paths``,
``dq sample-lineage``,
``dq correlate``, ``dq correlate-links``, ``dq violation-origins``,
``dq field-impact``, ``dq query-results``, ``dq snapshot-diff``,
``dq lineage-diff``,
``dq reference-integrity``, ``dq quality-gates``.

All subcommands read a UTF-8 JSON object from standard input and write a
UTF-8 JSON result to standard output.

``dq validate`` reads ``{"records": [...], "rules": [...], "dataset": ...,
"composite_rules": [...], "record_refs": [...]}`` where ``dataset`` is
required when ``composite_rules`` is present and ``composite_rules`` /
``record_refs`` are optional.
``dq batch-validate`` reads ``{"records": [...], "rules": [...]}`` with
no other top-level keys. It evaluates batch-level cross-record rules
(``unique_key`` / ``group_ratio``) and emits the
:func:`data_quality.evaluate_batch_rules` report without modifying
records, writing anything to disk or contacting any service.
``dq profile`` reads ``{"records": [...], "fields": [...]}`` where
``fields`` is an optional list of unique non-empty field names. It uses
the same record input as ``dq validate`` but never executes rules: it
profiles top-level fields only and emits validate-shaped rule candidates
without modifying records or writing anything to disk.
``dq exemptions`` reads ``{"result": {...}, "exemptions": [...]}`` where
``result`` is a ``dq validate`` result and ``exemptions`` is the list of
six-key exemption objects; it partitions the result's single-field
violations into active and waived without writing anything to disk.
``dq query-results`` reads ``{"results": [...], "rule_id": ...,
"severity": ..., "record_index": ..., "record_id": ...}`` where every
filter is optional.
``dq lineage`` reads ``{"nodes": [...], "edges": [...], "target": ...,
"direction": ..., "max_depth": ...}`` where ``direction`` and ``max_depth``
are optional.
``dq field-lineage`` reads ``{"fields": {...}, "edges": [...], "target": ...,
"direction": ..., "max_depth": ...}`` where ``direction`` and ``max_depth``
are optional.
``dq lineage-paths`` reads the same payload as ``dq lineage`` and explains
the concrete upstream/downstream paths of the target.
``dq field-lineage-paths`` reads the same payload as ``dq field-lineage``
and explains the concrete field-level upstream/downstream paths.
``dq sample-lineage`` reads ``{"samples": [...], "fields": {...},
"edges": [...], "sample_links": [...], "target": ..., "direction": ...,
"max_depth": ...}`` where ``direction`` and ``max_depth`` are optional.
``dq correlate`` reads ``{"results": [...], "lineage": {...}}``.
``dq correlate-links`` reads ``{"results": [...], "lineage": {...},
"sample_links": [...]}``.
``dq violation-origins`` reads ``{"results": [...], "lineage": {...},
"targets": [...]}``.
``dq field-impact`` reads ``{"datasets": {...}, "lineageEdges": [...],
"validationResults": [...], "anomalySamples": {...}, "seedFields": [...]}``.
``dq snapshot-diff`` reads ``{"baseline": {"results": [...]},
"current": {"results": [...]}, "lineage": {...}}`` and consumes already
evaluated check results without re-running any rule.
``dq lineage-diff`` reads ``{"baseline": {"fields": {...}, "edges": [...]},
"current": {"fields": {...}, "edges": [...]}, "targets": [...]}`` and
compares two already captured field lineage snapshots for the queried
targets without modifying the input, writing anything to disk or
contacting any service.
``dq reference-integrity`` reads ``{"datasets": {...}, "rules": [...]}``
where ``datasets`` maps dataset ids to record arrays and each rule is
either ``{"rule_id", "source_dataset", "source_field", "target_dataset",
"target_field"}`` or, for composite business keys, ``{"rule_id",
"source_dataset", "source_fields", "target_dataset", "target_fields"}``
with equally long non-empty field arrays paired by position.
``dq quality-gates`` reads ``{"dataset": ..., "records": [...],
"rules": [...], "gates": [...], "exemptions": [...]}`` where each gate is
``{"rule_id", "source_rule_id", "max_failed_ratio", "severity"}`` and
``exemptions`` (optional, as for ``dq exemptions``) waives matching
single-field violations; records are checked with the existing
single-field rules and each gate aggregates the active failed-record
ratio of its ``source_rule_id``.

Error JSON has the shape ``{"error": {"code": ..., "message": ...}}`` and
the process exits with status 2:

* ``INVALID_JSON``                    - stdin is not parseable UTF-8 JSON
* ``INVALID_INPUT``                   - validate payload/records is malformed
* ``INVALID_PROFILE_INPUT``           - profile payload records/fields is
                                        malformed
* ``INVALID_RULE``                    - a validate rule is malformed/conflicting
* ``INVALID_BATCH_RULE``              - a batch-validate rule is malformed,
                                        unsupported, has conflicting options or
                                        a duplicated/empty id
* ``INVALID_BATCH_INPUT``             - batch-validate payload/records is
                                        malformed or carries extra top-level
                                        keys
* ``INVALID_RULE_SET``                - composite rule set is empty/unusable
* ``DUPLICATE_RULE_ID``               - two composite rules share a rule_id
* ``INVALID_SEVERITY``                - composite rule severity is unknown
* ``UNSUPPORTED_COMPOSITE_CONDITION`` - composite condition type unsupported
* ``INVALID_COMPOSITE_RULE``          - composite rule cannot be executed
                                        (unknown/self-referencing field, bad
                                        date field, malformed condition)
* ``INVALID_RECORD_REFERENCE``        - record locator cannot be resolved
* ``INVALID_LINEAGE_INPUT``           - lineage nodes/edges are missing/malformed
* ``INVALID_LINEAGE_QUERY``           - lineage target/direction/max_depth is bad
* ``UNKNOWN_LINEAGE_TARGET``          - lineage target is not declared in nodes
* ``INVALID_FIELD_LINEAGE_INPUT``     - field-lineage fields/edges are malformed
* ``INVALID_FIELD_LINEAGE_QUERY``     - field-lineage target/direction/max_depth
                                        is invalid
* ``UNKNOWN_FIELD_LINEAGE_TARGET``    - field-lineage target is not declared
* ``LINEAGE_NODE_NOT_FOUND``          - lineage-paths target is not declared
* ``INVALID_SAMPLE_LINEAGE_INPUT``    - sample-lineage samples/fields/edges/
                                        sample_links are malformed
* ``INVALID_SAMPLE_LINEAGE_QUERY``    - sample-lineage target/direction/
                                        max_depth is invalid
* ``UNKNOWN_SAMPLE_LINEAGE_TARGET``   - sample-lineage target is not declared
* ``UNKNOWN_SAMPLE_LINEAGE_REFERENCE`` - sample-lineage sample_links endpoint
                                         is not declared
* ``INVALID_CORRELATION_INPUT``       - correlate results/lineage are malformed
* ``UNKNOWN_CORRELATION_REFERENCE``   - correlate result references an
                                        undeclared dataset or field
* ``INVALID_LINKED_CORRELATION_INPUT`` - correlate-links sample_links are
                                         malformed
* ``UNKNOWN_LINKED_CORRELATION_REFERENCE`` - correlate-links sample link
                                            endpoint has no matching result
* ``INVALID_ORIGIN_INPUT``            - violation-origins results/lineage/
                                        targets are malformed
* ``UNKNOWN_ORIGIN_REFERENCE``        - violation-origins result references
                                        an undeclared dataset or field
* ``UNKNOWN_ORIGIN_TARGET``           - violation-origins target has no
                                        matching violated result
* ``INVALID_IMPACT_INPUT``            - field-impact payload structure, key
                                        set, references, rule statuses or
                                        sample mapping is malformed
* ``INVALID_SNAPSHOT_INPUT``          - snapshot-diff snapshots/results/
                                        lineage are malformed
* ``UNKNOWN_SNAPSHOT_REFERENCE``      - snapshot-diff result references an
                                        undeclared dataset or field
* ``INVALID_LINEAGE_SNAPSHOT``        - lineage-diff payload or either field
                                        lineage snapshot is malformed
* ``INVALID_LINEAGE_DIFF_QUERY``      - lineage-diff targets list is missing,
                                        empty, malformed or duplicated
* ``UNKNOWN_LINEAGE_DIFF_TARGET``     - lineage-diff target is declared on
                                        neither snapshot
* ``INVALID_REFERENCE_INPUT``         - reference-integrity datasets/rules
                                        structure or records are malformed
* ``INVALID_REFERENCE_RULE``          - reference-integrity rule keys are
                                        missing/extra, the two rule shapes
                                        are mixed, fields are empty, field
                                        arrays are malformed or rule_id is
                                        duplicated
* ``UNKNOWN_REFERENCE_DATASET``       - reference-integrity rule names an
                                        undeclared dataset
* ``INVALID_QUALITY_GATE_RULE``       - quality-gates gate is malformed,
                                        has a duplicated rule_id, an
                                        unknown severity or a
                                        max_failed_ratio that is not a
                                        JSON number in [0, 1]
* ``UNKNOWN_QUALITY_GATE_SOURCE``     - quality-gates gate references a
                                        rule that is not declared
* ``INVALID_EXEMPTION_INPUT``         - exemptions payload is not a
                                        validate result, an exemption is
                                        malformed, duplicated or
                                        conflicting, or it matches no
                                        reported violation
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from .batch_rules import (
    InvalidBatchInputError,
    InvalidBatchRuleError,
    evaluate_batch_rules,
)
from .composite import query_composite_results
from .correlation import (
    InvalidCorrelationInputError,
    InvalidLinkedCorrelationInputError,
    UnknownCorrelationReferenceError,
    UnknownLinkedCorrelationReferenceError,
    correlate_linked_violations,
    correlate_violations,
)
from .exemptions import (
    InvalidExemptionError,
    apply_violation_exemptions,
)
from .field_impact import ImpactInputError, analyze_field_impacts
from .field_lineage import (
    InvalidFieldLineageInputError,
    InvalidFieldLineageQueryError,
    UnknownFieldLineageTargetError,
    trace_field_lineage,
)
from .lineage import (
    InvalidLineageInputError,
    InvalidLineageQueryError,
    UnknownLineageTargetError,
    trace_lineage,
)
from .lineage_paths import (
    LineageNodeNotFoundError,
    explain_field_lineage_paths,
    explain_lineage_paths,
)
from .lineage_snapshot_diff import (
    InvalidLineageDiffQueryError,
    InvalidLineageSnapshotError,
    UnknownLineageDiffTargetError,
    compare_lineage_snapshots,
)
from .origin_analysis import (
    InvalidOriginInputError,
    UnknownOriginReferenceError,
    UnknownOriginTargetError,
    analyze_violation_origins,
)
from .profiling import InvalidProfileInputError, profile_records
from .quality_gates import (
    InvalidQualityGateRuleError,
    UnknownQualityGateSourceError,
    evaluate_quality_gates,
)
from .reference_integrity import (
    InvalidReferenceInputError,
    InvalidReferenceRuleError,
    UnknownReferenceDatasetError,
    validate_references,
)
from .sample_lineage import (
    InvalidSampleLineageInputError,
    InvalidSampleLineageQueryError,
    UnknownSampleLineageReferenceError,
    UnknownSampleLineageTargetError,
    trace_sample_lineage,
)
from .snapshot_diff import (
    InvalidSnapshotInputError,
    UnknownSnapshotReferenceError,
    compare_quality_snapshots,
)
from .validator import (
    DataQualityError,
    InvalidInputError,
    InvalidRuleError,
    validate,
)

EXIT_OK = 0
EXIT_ERROR = 2

# Sentinel distinguishing "error already emitted" from a parsed payload.
_PARSE_FAILED = object()


def _emit_error(code: str, message: str) -> int:
    payload = {"error": {"code": code, "message": message}}
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_ERROR


def _read_json_payload() -> Any:
    """Read stdin as a UTF-8 JSON value.

    Returns the parsed value, or ``_PARSE_FAILED`` after emitting an
    ``INVALID_JSON`` error.
    """
    raw = sys.stdin.buffer.read()

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _emit_error("INVALID_JSON", f"input is not valid UTF-8: {exc}")
        return _PARSE_FAILED

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        _emit_error("INVALID_JSON", f"input is not valid JSON: {exc.msg}")
        return _PARSE_FAILED


def _run_validate() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_INPUT", "input payload must be a JSON object"
        )
    if "records" not in payload:
        return _emit_error("INVALID_INPUT", "payload is missing 'records'")
    if "rules" not in payload:
        return _emit_error("INVALID_INPUT", "payload is missing 'rules'")

    try:
        result = validate(
            payload["records"],
            payload["rules"],
            dataset=payload.get("dataset"),
            composite_rules=payload.get("composite_rules"),
            record_refs=payload.get("record_refs"),
        )
    except InvalidRuleError as exc:
        return _emit_error("INVALID_RULE", str(exc))
    except InvalidInputError as exc:
        return _emit_error("INVALID_INPUT", str(exc))
    except DataQualityError as exc:
        return _emit_error(exc.code, str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_batch_validate() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_BATCH_INPUT", "input payload must be a JSON object"
        )
    if set(payload) != {"records", "rules"}:
        missing = sorted({"records", "rules"} - set(payload))
        if missing:
            return _emit_error(
                "INVALID_BATCH_INPUT",
                f"payload is missing keys: {missing}",
            )
        unknown = sorted(set(payload) - {"records", "rules"})
        return _emit_error(
            "INVALID_BATCH_INPUT",
            f"payload has unsupported keys: {unknown}",
        )

    try:
        result = evaluate_batch_rules(
            payload["records"],
            payload["rules"],
        )
    except InvalidBatchRuleError as exc:
        return _emit_error("INVALID_BATCH_RULE", str(exc))
    except InvalidBatchInputError as exc:
        return _emit_error("INVALID_BATCH_INPUT", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_profile() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_PROFILE_INPUT", "input payload must be a JSON object"
        )
    if "records" not in payload:
        return _emit_error(
            "INVALID_PROFILE_INPUT", "payload is missing 'records'"
        )

    try:
        result = profile_records(
            payload["records"],
            payload.get("fields"),
        )
    except InvalidProfileInputError as exc:
        return _emit_error("INVALID_PROFILE_INPUT", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_exemptions() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_EXEMPTION_INPUT", "input payload must be a JSON object"
        )
    if "result" not in payload:
        return _emit_error(
            "INVALID_EXEMPTION_INPUT", "payload is missing 'result'"
        )
    if "exemptions" not in payload:
        return _emit_error(
            "INVALID_EXEMPTION_INPUT", "payload is missing 'exemptions'"
        )

    try:
        result = apply_violation_exemptions(
            payload["result"],
            payload["exemptions"],
        )
    except InvalidExemptionError as exc:
        return _emit_error("INVALID_EXEMPTION_INPUT", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_query_results() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_INPUT", "input payload must be a JSON object"
        )
    if "results" not in payload:
        return _emit_error("INVALID_INPUT", "payload is missing 'results'")

    try:
        result = query_composite_results(
            payload["results"],
            rule_id=payload.get("rule_id"),
            severity=payload.get("severity"),
            record_index=payload.get("record_index"),
            record_id=payload.get("record_id"),
        )
    except DataQualityError as exc:
        return _emit_error(exc.code, str(exc))
    except ValueError as exc:
        return _emit_error("INVALID_INPUT", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_lineage() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_LINEAGE_INPUT", "input payload must be a JSON object"
        )

    try:
        result = trace_lineage(
            payload.get("nodes"),
            payload.get("edges"),
            payload.get("target"),
            payload.get("direction", "both"),
            payload.get("max_depth", None),
        )
    except InvalidLineageInputError as exc:
        return _emit_error("INVALID_LINEAGE_INPUT", str(exc))
    except InvalidLineageQueryError as exc:
        return _emit_error("INVALID_LINEAGE_QUERY", str(exc))
    except UnknownLineageTargetError as exc:
        return _emit_error("UNKNOWN_LINEAGE_TARGET", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_field_lineage() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_FIELD_LINEAGE_INPUT", "input payload must be a JSON object"
        )

    try:
        result = trace_field_lineage(
            payload.get("fields"),
            payload.get("edges"),
            payload.get("target"),
            payload.get("direction", "both"),
            payload.get("max_depth", None),
        )
    except InvalidFieldLineageInputError as exc:
        return _emit_error("INVALID_FIELD_LINEAGE_INPUT", str(exc))
    except InvalidFieldLineageQueryError as exc:
        return _emit_error("INVALID_FIELD_LINEAGE_QUERY", str(exc))
    except UnknownFieldLineageTargetError as exc:
        return _emit_error("UNKNOWN_FIELD_LINEAGE_TARGET", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_lineage_paths() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_LINEAGE_INPUT", "input payload must be a JSON object"
        )

    try:
        result = explain_lineage_paths(
            payload.get("nodes"),
            payload.get("edges"),
            payload.get("target"),
            payload.get("direction", "both"),
            payload.get("max_depth", None),
        )
    except InvalidLineageInputError as exc:
        return _emit_error("INVALID_LINEAGE_INPUT", str(exc))
    except InvalidLineageQueryError as exc:
        return _emit_error("INVALID_LINEAGE_QUERY", str(exc))
    except LineageNodeNotFoundError as exc:
        return _emit_error(exc.code, str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_field_lineage_paths() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_FIELD_LINEAGE_INPUT", "input payload must be a JSON object"
        )

    try:
        result = explain_field_lineage_paths(
            payload.get("fields"),
            payload.get("edges"),
            payload.get("target"),
            payload.get("direction", "both"),
            payload.get("max_depth", None),
        )
    except InvalidFieldLineageInputError as exc:
        return _emit_error("INVALID_FIELD_LINEAGE_INPUT", str(exc))
    except InvalidFieldLineageQueryError as exc:
        return _emit_error("INVALID_FIELD_LINEAGE_QUERY", str(exc))
    except LineageNodeNotFoundError as exc:
        return _emit_error(exc.code, str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_sample_lineage() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_SAMPLE_LINEAGE_INPUT",
            "input payload must be a JSON object",
        )

    try:
        result = trace_sample_lineage(
            payload.get("samples"),
            payload.get("fields"),
            payload.get("edges"),
            payload.get("sample_links"),
            payload.get("target"),
            payload.get("direction", "both"),
            payload.get("max_depth", None),
        )
    except InvalidSampleLineageInputError as exc:
        return _emit_error("INVALID_SAMPLE_LINEAGE_INPUT", str(exc))
    except InvalidSampleLineageQueryError as exc:
        return _emit_error("INVALID_SAMPLE_LINEAGE_QUERY", str(exc))
    except UnknownSampleLineageTargetError as exc:
        return _emit_error("UNKNOWN_SAMPLE_LINEAGE_TARGET", str(exc))
    except UnknownSampleLineageReferenceError as exc:
        return _emit_error("UNKNOWN_SAMPLE_LINEAGE_REFERENCE", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_correlate() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_CORRELATION_INPUT", "input payload must be a JSON object"
        )

    try:
        result = correlate_violations(
            payload.get("results"),
            payload.get("lineage"),
        )
    except InvalidCorrelationInputError as exc:
        return _emit_error("INVALID_CORRELATION_INPUT", str(exc))
    except UnknownCorrelationReferenceError as exc:
        return _emit_error("UNKNOWN_CORRELATION_REFERENCE", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_correlate_links() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_LINKED_CORRELATION_INPUT",
            "input payload must be a JSON object",
        )

    try:
        result = correlate_linked_violations(
            payload.get("results"),
            payload.get("lineage"),
            payload.get("sample_links"),
        )
    except InvalidCorrelationInputError as exc:
        return _emit_error("INVALID_CORRELATION_INPUT", str(exc))
    except UnknownCorrelationReferenceError as exc:
        return _emit_error("UNKNOWN_CORRELATION_REFERENCE", str(exc))
    except InvalidLinkedCorrelationInputError as exc:
        return _emit_error("INVALID_LINKED_CORRELATION_INPUT", str(exc))
    except UnknownLinkedCorrelationReferenceError as exc:
        return _emit_error("UNKNOWN_LINKED_CORRELATION_REFERENCE", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_violation_origins() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_ORIGIN_INPUT", "input payload must be a JSON object"
        )

    try:
        result = analyze_violation_origins(
            payload.get("results"),
            payload.get("lineage"),
            payload.get("targets"),
        )
    except InvalidOriginInputError as exc:
        return _emit_error("INVALID_ORIGIN_INPUT", str(exc))
    except UnknownOriginReferenceError as exc:
        return _emit_error("UNKNOWN_ORIGIN_REFERENCE", str(exc))
    except UnknownOriginTargetError as exc:
        return _emit_error("UNKNOWN_ORIGIN_TARGET", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_field_impact() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    try:
        result = analyze_field_impacts(payload)
    except ImpactInputError as exc:
        return _emit_error("INVALID_IMPACT_INPUT", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_snapshot_diff() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_SNAPSHOT_INPUT", "input payload must be a JSON object"
        )

    try:
        result = compare_quality_snapshots(payload)
    except InvalidSnapshotInputError as exc:
        return _emit_error("INVALID_SNAPSHOT_INPUT", str(exc))
    except UnknownSnapshotReferenceError as exc:
        return _emit_error("UNKNOWN_SNAPSHOT_REFERENCE", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_lineage_diff() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_LINEAGE_SNAPSHOT", "input payload must be a JSON object"
        )

    try:
        result = compare_lineage_snapshots(payload)
    except InvalidLineageSnapshotError as exc:
        return _emit_error("INVALID_LINEAGE_SNAPSHOT", str(exc))
    except InvalidLineageDiffQueryError as exc:
        return _emit_error("INVALID_LINEAGE_DIFF_QUERY", str(exc))
    except UnknownLineageDiffTargetError as exc:
        return _emit_error("UNKNOWN_LINEAGE_DIFF_TARGET", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_reference_integrity() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_REFERENCE_INPUT", "input payload must be a JSON object"
        )

    try:
        result = validate_references(
            payload.get("datasets"),
            payload.get("rules"),
        )
    except InvalidReferenceInputError as exc:
        return _emit_error("INVALID_REFERENCE_INPUT", str(exc))
    except InvalidReferenceRuleError as exc:
        return _emit_error("INVALID_REFERENCE_RULE", str(exc))
    except UnknownReferenceDatasetError as exc:
        return _emit_error("UNKNOWN_REFERENCE_DATASET", str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_quality_gates() -> int:
    payload = _read_json_payload()
    if payload is _PARSE_FAILED:
        return EXIT_ERROR

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_INPUT", "input payload must be a JSON object"
        )
    for key in ("records", "rules", "gates"):
        if key not in payload:
            return _emit_error(
                "INVALID_INPUT", f"payload is missing {key!r}"
            )

    try:
        result = evaluate_quality_gates(
            payload.get("dataset"),
            payload["records"],
            payload["rules"],
            payload["gates"],
            payload.get("exemptions"),
        )
    except InvalidRuleError as exc:
        return _emit_error("INVALID_RULE", str(exc))
    except InvalidInputError as exc:
        return _emit_error("INVALID_INPUT", str(exc))
    except InvalidQualityGateRuleError as exc:
        return _emit_error("INVALID_QUALITY_GATE_RULE", str(exc))
    except UnknownQualityGateSourceError as exc:
        return _emit_error("UNKNOWN_QUALITY_GATE_SOURCE", str(exc))
    except InvalidExemptionError as exc:
        return _emit_error("INVALID_EXEMPTION_INPUT", str(exc))
    except DataQualityError as exc:
        return _emit_error(exc.code, str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dq",
        description="Data quality rule validation and lineage tracing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate records from a UTF-8 JSON object on standard input",
    )
    validate_parser.set_defaults(handler=_run_validate)

    batch_validate_parser = subparsers.add_parser(
        "batch-validate",
        help="validate batch-level cross-record rules from a UTF-8 JSON "
        "object on standard input",
    )
    batch_validate_parser.set_defaults(handler=_run_batch_validate)

    profile_parser = subparsers.add_parser(
        "profile",
        help="profile records and generate validate rule candidates from "
        "a UTF-8 JSON object on standard input",
    )
    profile_parser.set_defaults(handler=_run_profile)

    exemptions_parser = subparsers.add_parser(
        "exemptions",
        help="apply manual exemptions to a dq validate result from a "
        "UTF-8 JSON object on standard input",
    )
    exemptions_parser.set_defaults(handler=_run_exemptions)

    lineage_parser = subparsers.add_parser(
        "lineage",
        help="trace upstream/downstream lineage from a UTF-8 JSON object "
        "on standard input",
    )
    lineage_parser.set_defaults(handler=_run_lineage)

    field_lineage_parser = subparsers.add_parser(
        "field-lineage",
        help="trace upstream/downstream field-level lineage from a UTF-8 "
        "JSON object on standard input",
    )
    field_lineage_parser.set_defaults(handler=_run_field_lineage)

    lineage_paths_parser = subparsers.add_parser(
        "lineage-paths",
        help="explain ordered upstream/downstream lineage paths from a "
        "UTF-8 JSON object on standard input",
    )
    lineage_paths_parser.set_defaults(handler=_run_lineage_paths)

    field_lineage_paths_parser = subparsers.add_parser(
        "field-lineage-paths",
        help="explain ordered upstream/downstream field-level lineage paths "
        "from a UTF-8 JSON object on standard input",
    )
    field_lineage_paths_parser.set_defaults(handler=_run_field_lineage_paths)

    sample_lineage_parser = subparsers.add_parser(
        "sample-lineage",
        help="trace upstream/downstream sample-level lineage from a UTF-8 "
        "JSON object on standard input",
    )
    sample_lineage_parser.set_defaults(handler=_run_sample_lineage)

    correlate_parser = subparsers.add_parser(
        "correlate",
        help="group violation results into cross-rule correlation events "
        "from a UTF-8 JSON object on standard input",
    )
    correlate_parser.set_defaults(handler=_run_correlate)

    correlate_links_parser = subparsers.add_parser(
        "correlate-links",
        help="group violation results across linked samples into "
        "cross-dataset correlation events from a UTF-8 JSON object on "
        "standard input",
    )
    correlate_links_parser.set_defaults(handler=_run_correlate_links)

    violation_origins_parser = subparsers.add_parser(
        "violation-origins",
        help="analyze upstream/self origin and downstream impact evidence "
        "of violated targets from a UTF-8 JSON object on standard input",
    )
    violation_origins_parser.set_defaults(handler=_run_violation_origins)

    field_impact_parser = subparsers.add_parser(
        "field-impact",
        help="analyze downstream field-level quality impact from a UTF-8 "
        "JSON object on standard input",
    )
    field_impact_parser.set_defaults(handler=_run_field_impact)

    query_results_parser = subparsers.add_parser(
        "query-results",
        help="filter complete composite rule results by rule id, severity "
        "or record locator from a UTF-8 JSON object on standard input",
    )
    query_results_parser.set_defaults(handler=_run_query_results)

    snapshot_diff_parser = subparsers.add_parser(
        "snapshot-diff",
        help="compare violated results of two quality check snapshots over "
        "the shared lineage graph from a UTF-8 JSON object on standard "
        "input",
    )
    snapshot_diff_parser.set_defaults(handler=_run_snapshot_diff)

    lineage_diff_parser = subparsers.add_parser(
        "lineage-diff",
        help="compare two field lineage snapshots for the queried targets "
        "from a UTF-8 JSON object on standard input",
    )
    lineage_diff_parser.set_defaults(handler=_run_lineage_diff)

    reference_integrity_parser = subparsers.add_parser(
        "reference-integrity",
        help="validate cross-dataset reference integrity rules from a "
        "UTF-8 JSON object on standard input",
    )
    reference_integrity_parser.set_defaults(handler=_run_reference_integrity)

    quality_gates_parser = subparsers.add_parser(
        "quality-gates",
        help="aggregate single-field rule failure ratios into dataset-level "
        "quality gates from a UTF-8 JSON object on standard input",
    )
    quality_gates_parser.set_defaults(handler=_run_quality_gates)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler()


if __name__ == "__main__":
    sys.exit(main())
