"""核心校验逻辑。

规则类型：required、unique、range、regex、allowed_values。
规则按输入顺序、记录按顺序检查（规则为外层循环）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

SUPPORTED_TYPES = ("required", "unique", "range", "regex", "allowed_values")

# 每类规则违反时输出的固定 message。
MESSAGES: Dict[str, str] = {
    "required": "field is required",
    "unique": "duplicate value",
    "range": "value out of range",
    "regex": "value does not match pattern",
    "allowed_values": "value is not allowed",
}


class DataQualityError(ValueError):
    """所有校验入参错误的基类（仍是 ValueError）。"""

    code = "INVALID_INPUT"


class InvalidInputError(DataQualityError):
    """records / 顶层结构等输入数据错误。"""

    code = "INVALID_INPUT"


class InvalidRuleError(DataQualityError):
    """规则定义错误：非法 id、未知类型、重复 id、options 不匹配等。"""

    code = "INVALID_RULE"


def _is_number(value: Any) -> bool:
    """JSON 意义下的数字：bool 不属于数字。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_key(value: Any) -> Any:
    """把 JSON 值转成可哈希、且遵循 JSON 相等语义的键。

    主要修正 Python 的两处偏差：
    - True/False 与 1/0 必须不相等；
    - list/dict 不可哈希，需递归规范化（dict 键顺序无关）。
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        # 1 与 1.0 在 JSON 中数值相等，Python 本身也判等且哈希一致。
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if value is None:
        return ("null",)
    if isinstance(value, list):
        return ("array", tuple(_json_key(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple(sorted((key, _json_key(val)) for key, val in value.items())),
        )
    # 来自 json.loads 的数据不会走到这里；直接使用 API 时兜底按原值处理。
    return ("raw", repr(value))


def _validate_rules(rules: Any) -> List[Dict[str, Any]]:
    """校验规则定义，返回规范化后的规则列表；任何问题抛 InvalidRuleError。"""
    if not isinstance(rules, list):
        raise InvalidInputError("rules must be a list")

    normalized: List[Dict[str, Any]] = []
    seen_ids = set()

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise InvalidRuleError(f"rule at index {index} must be an object")

        rule_id = rule.get("id", _MISSING)
        if rule_id is _MISSING:
            raise InvalidRuleError(f"rule at index {index} is missing id")
        if not isinstance(rule_id, str) or not rule_id:
            raise InvalidRuleError(
                f"rule at index {index} has an invalid id: id must be a non-empty string"
            )
        if rule_id in seen_ids:
            raise InvalidRuleError(f"duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        rule_type = rule.get("type")
        if not isinstance(rule_type, str) or rule_type not in SUPPORTED_TYPES:
            supported = ", ".join(SUPPORTED_TYPES)
            raise InvalidRuleError(
                f"rule {rule_id!r} has an unsupported type: {rule_type!r} "
                f"(supported: {supported})"
            )

        options = rule.get("options")
        if not isinstance(options, dict):
            raise InvalidRuleError(f"rule {rule_id!r} requires an options object")

        field = options.get("field")
        if not isinstance(field, str) or not field:
            raise InvalidRuleError(
                f"rule {rule_id!r} option 'field' must be a non-empty string"
            )

        normalized_rule: Dict[str, Any] = {
            "id": rule_id,
            "type": rule_type,
            "field": field,
        }

        if rule_type == "range":
            min_value = options.get("min", _MISSING)
            max_value = options.get("max", _MISSING)
            if min_value is _MISSING or max_value is _MISSING:
                raise InvalidRuleError(
                    f"rule {rule_id!r} options must include both 'min' and 'max'"
                )
            if not _is_number(min_value) or not _is_number(max_value):
                raise InvalidRuleError(
                    f"rule {rule_id!r} options 'min' and 'max' must be numbers"
                )
            if min_value > max_value:
                raise InvalidRuleError(
                    f"rule {rule_id!r} option 'min' must not be greater than 'max'"
                )
            normalized_rule["min"] = min_value
            normalized_rule["max"] = max_value

        elif rule_type == "regex":
            pattern = options.get("pattern", _MISSING)
            if pattern is _MISSING:
                raise InvalidRuleError(
                    f"rule {rule_id!r} option 'pattern' is required"
                )
            if not isinstance(pattern, str):
                raise InvalidRuleError(
                    f"rule {rule_id!r} option 'pattern' must be a string"
                )
            try:
                normalized_rule["pattern"] = re.compile(pattern)
            except re.error as exc:
                raise InvalidRuleError(
                    f"rule {rule_id!r} has an invalid regex pattern: {exc}"
                ) from exc

        elif rule_type == "allowed_values":
            values = options.get("values", _MISSING)
            if values is _MISSING:
                raise InvalidRuleError(
                    f"rule {rule_id!r} option 'values' is required"
                )
            if not isinstance(values, list):
                raise InvalidRuleError(
                    f"rule {rule_id!r} option 'values' must be a list"
                )
            normalized_rule["allowed"] = {_json_key(value) for value in values}

        normalized.append(normalized_rule)

    return normalized


_MISSING = object()


def validate(records: Any, rules: Any) -> Dict[str, Any]:
    """对记录执行规则校验。

    返回 ``{"passed", "summary", "violations"}``；records/rules 结构或规则
    定义非法时抛 :class:`InvalidInputError` / :class:`InvalidRuleError`
    （均为 :class:`ValueError`）。
    """
    if not isinstance(records, list):
        raise InvalidInputError("records must be a list")

    # 先完整校验规则（规则先于记录），再检查记录元素结构并开始逐规则检查。
    normalized_rules = _validate_rules(rules)

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InvalidInputError(f"record at index {index} must be an object")

    violations: List[Dict[str, Any]] = []

    for rule in normalized_rules:
        rule_id = rule["id"]
        rule_type = rule["type"]
        field = rule["field"]
        message = MESSAGES[rule_type]

        # unique 需要在单条规则内维护已见值集合。
        seen_values = set()

        for record_index, record in enumerate(records):
            present = field in record
            value = record[field] if present else None
            record_id = record.get("id")
            violated = False

            if rule_type == "required":
                violated = (not present) or value is None

            elif rule_type == "unique":
                # 仅对非 null 值按 JSON 相等判重。
                if present and value is not None:
                    key = _json_key(value)
                    if key in seen_values:
                        violated = True  # 只报告后面的重复项
                    else:
                        seen_values.add(key)

            elif rule_type == "range":
                # 缺失字段按 null 处理；非数字一律违反。
                violated = (not _is_number(value)) or not (
                    rule["min"] <= value <= rule["max"]
                )

            elif rule_type == "regex":
                # 非字符串（含 null）一律违反。
                violated = (not isinstance(value, str)) or (
                    rule["pattern"].fullmatch(value) is None
                )

            elif rule_type == "allowed_values":
                # 缺失字段按 null 判断是否在允许集合内。
                violated = _json_key(value) not in rule["allowed"]

            if violated:
                violations.append(
                    {
                        "rule_id": rule_id,
                        "record_index": record_index,
                        "record_id": record_id,
                        "field": field,
                        "value": value,
                        "message": message,
                    }
                )

    return {
        "passed": not violations,
        "summary": {
            "record_count": len(records),
            "violation_count": len(violations),
            "checked_rule_count": len(normalized_rules),
        },
        "violations": violations,
    }
