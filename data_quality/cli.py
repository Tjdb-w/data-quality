"""Command line interface: ``dq validate`` and ``dq lineage``.

Both commands read a UTF-8 JSON object from standard input and write the
result as UTF-8 JSON to standard output. ``dq validate`` expects
``{"records": [...], "rules": [...]}``; ``dq lineage`` expects
``{"nodes": [...], "edges": [...], "target": ...}`` with optional
``direction`` and ``max_depth``.

Error JSON has the shape ``{"error": {"code": ..., "message": ...}}`` and
the process exits with status 2:

* ``INVALID_JSON``           - stdin is not parseable UTF-8 JSON
* ``INVALID_INPUT``          - validate payload/records structure is malformed
* ``INVALID_RULE``           - a rule is malformed or has conflicting options
* ``INVALID_LINEAGE_INPUT``  - lineage nodes/edges structure is malformed
* ``INVALID_LINEAGE_QUERY``  - lineage target/direction/max_depth is malformed
* ``UNKNOWN_LINEAGE_TARGET`` - lineage target is not a declared node
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from .lineage import (
    InvalidLineageInputError,
    InvalidLineageQueryError,
    UnknownLineageTargetError,
    trace_lineage,
)
from .validator import (
    DataQualityError,
    InvalidInputError,
    InvalidRuleError,
    validate,
)

EXIT_OK = 0
EXIT_ERROR = 2


def _emit_error(code: str, message: str) -> int:
    payload = {"error": {"code": code, "message": message}}
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_ERROR


class _InvalidJson(Exception):
    """Internal: stdin is not parseable UTF-8 JSON."""


def _read_stdin_json() -> Any:
    """Read and parse stdin; returns the payload or raises _InvalidJson."""
    raw = sys.stdin.buffer.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _InvalidJson(f"input is not valid UTF-8: {exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _InvalidJson(f"input is not valid JSON: {exc.msg}")


def _run_validate() -> int:
    try:
        payload = _read_stdin_json()
    except _InvalidJson as exc:
        return _emit_error("INVALID_JSON", str(exc))

    if not isinstance(payload, dict):
        return _emit_error(
            "INVALID_INPUT", "input payload must be a JSON object"
        )
    if "records" not in payload:
        return _emit_error("INVALID_INPUT", "payload is missing 'records'")
    if "rules" not in payload:
        return _emit_error("INVALID_INPUT", "payload is missing 'rules'")

    try:
        result = validate(payload["records"], payload["rules"])
    except InvalidRuleError as exc:
        return _emit_error("INVALID_RULE", str(exc))
    except InvalidInputError as exc:
        return _emit_error("INVALID_INPUT", str(exc))
    except DataQualityError as exc:
        return _emit_error(exc.code, str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return EXIT_OK


def _run_lineage() -> int:
    try:
        payload = _read_stdin_json()
    except _InvalidJson as exc:
        return _emit_error("INVALID_JSON", str(exc))

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
            payload.get("max_depth"),
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
        help="trace lineage from a UTF-8 JSON object on standard input",
    )
    lineage_parser.set_defaults(handler=_run_lineage)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler()


if __name__ == "__main__":
    sys.exit(main())
