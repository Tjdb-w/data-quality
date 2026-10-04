"""Command line interface: ``dq validate``, ``dq lineage``,
``dq field-lineage``, ``dq lineage-paths``, ``dq field-lineage-paths``,
``dq correlate``, ``dq correlate-links``, ``dq query-results``.

All subcommands read a UTF-8 JSON object from standard input and write a
UTF-8 JSON result to standard output.

``dq validate`` reads ``{"records": [...], "rules": [...], "dataset": ...,
"composite_rules": [...], "record_refs": [...]}`` where ``dataset`` is
required when ``composite_rules`` is present and ``composite_rules`` /
``record_refs`` are optional.
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
``dq correlate`` reads ``{"results": [...], "lineage": {...}}``.
``dq correlate-links`` reads ``{"results": [...], "lineage": {...},
"sample_links": [...]}``.

Error JSON has the shape ``{"error": {"code": ..., "message": ...}}`` and
the process exits with status 2:

* ``INVALID_JSON``                    - stdin is not parseable UTF-8 JSON
* ``INVALID_INPUT``                   - validate payload/records is malformed
* ``INVALID_RULE``                    - a validate rule is malformed/conflicting
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
* ``INVALID_CORRELATION_INPUT``       - correlate results/lineage are malformed
* ``UNKNOWN_CORRELATION_REFERENCE``   - correlate result references an
                                        undeclared dataset or field
* ``INVALID_LINKED_CORRELATION_INPUT``    - correlate-links results/lineage/
                                            sample_links are malformed
* ``UNKNOWN_LINKED_CORRELATION_REFERENCE``- correlate-links sample link endpoint
                                            is not present in the results
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from .composite import query_composite_results
from .correlation import (
    InvalidCorrelationInputError,
    InvalidLinkedCorrelationInputError,
    UnknownCorrelationReferenceError,
    UnknownLinkedCorrelationReferenceError,
    correlate_linked_violations,
    correlate_violations,
)
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
    except (
        InvalidCorrelationInputError,
        InvalidLinkedCorrelationInputError,
    ) as exc:
        return _emit_error("INVALID_LINKED_CORRELATION_INPUT", str(exc))
    except (
        UnknownCorrelationReferenceError,
        UnknownLinkedCorrelationReferenceError,
    ) as exc:
        return _emit_error("UNKNOWN_LINKED_CORRELATION_REFERENCE", str(exc))

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

    correlate_parser = subparsers.add_parser(
        "correlate",
        help="group violation results into cross-rule correlation events "
        "from a UTF-8 JSON object on standard input",
    )
    correlate_parser.set_defaults(handler=_run_correlate)

    correlate_links_parser = subparsers.add_parser(
        "correlate-links",
        help="group violation results across linked samples into "
        "correlation events from a UTF-8 JSON object on standard input",
    )
    correlate_links_parser.set_defaults(handler=_run_correlate_links)

    query_results_parser = subparsers.add_parser(
        "query-results",
        help="filter complete composite rule results by rule id, severity "
        "or record locator from a UTF-8 JSON object on standard input",
    )
    query_results_parser.set_defaults(handler=_run_query_results)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler()


if __name__ == "__main__":
    sys.exit(main())
