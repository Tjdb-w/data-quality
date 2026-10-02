"""dq 命令行入口：``dq validate`` 从标准输入读取 JSON 并输出校验结果。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .validator import DataQualityError, validate


def _emit_error(message: str, code: str) -> int:
    """向 stdout 输出错误 JSON，返回退出码 2。"""
    payload: Dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 2


def run_validate() -> int:
    """执行 ``dq validate``，返回进程退出码。"""
    # 显式 UTF-8，避免依赖平台默认编码。
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _emit_error(f"input is not valid UTF-8 JSON: {exc}", "INVALID_JSON")

    if not isinstance(payload, dict) or "records" not in payload or "rules" not in payload:
        return _emit_error(
            "input must be a JSON object containing 'records' and 'rules'",
            "INVALID_INPUT",
        )

    try:
        result = validate(payload["records"], payload["rules"])
    except DataQualityError as exc:
        return _emit_error(str(exc), exc.code)
    except ValueError as exc:
        # 兜底：任何未细分的 ValueError 仍按结构错误处理。
        return _emit_error(str(exc), "INVALID_INPUT")

    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dq",
        description="数据质量规则校验工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="从标准输入读取含 records 和 rules 的 JSON 对象并校验",
    )
    validate_parser.set_defaults(handler=run_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler()


if __name__ == "__main__":
    sys.exit(main())
