"""Command line interface: ``dq validate``.

Reads a UTF-8 JSON object ``{"records": [...], "rules": [...]}`` from
standard input and writes the validation result as UTF-8 JSON to standard
output.

Error JSON has the shape ``{"error": {"code": ..., "message": ...}}`` and
the process exits with status 2:

* ``INVALID_JSON``  - stdin is not parseable UTF-8 JSON
* ``INVALID_INPUT`` - payload/records structure is malformed
* ``INVALID_RULE``  - a rule is malformed or has conflicting options
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

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


def _run_validate() -> int:
    raw = sys.stdin.buffer.read()

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _emit_error("INVALID_JSON", f"input is not valid UTF-8: {exc}")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _emit_error(
            "INVALID_JSON", f"input is not valid JSON: {exc.msg}"
        )

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dq",
        description="Data quality rule validation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate records from a UTF-8 JSON object on standard input",
    )
    validate_parser.set_defaults(handler=_run_validate)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler()


if __name__ == "__main__":
    sys.exit(main())
