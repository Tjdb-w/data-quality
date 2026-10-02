# Data Quality

数据质量校验与血缘引擎：规则校验、异常样本定位、上下游血缘追溯。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：独立的数据质量规则校验（`data_quality.validate` 与 `dq validate`）。
- 暂未实现：血缘追溯。

## 安装

```bash
pip install -e .
```

仅使用 Python 标准库，无需第三方依赖（要求 Python 3.8+）。

## 命令行用法

`dq validate` 从标准输入读取一个含 `records` 和 `rules` 的 UTF-8 JSON 对象，
并以 UTF-8 JSON 输出校验结果：

```bash
dq validate < input.json
# 或
python -m data_quality validate < input.json
```

输入示例：

```json
{
  "records": [
    {"id": "r1", "age": 17, "level": "admin"},
    {"id": "r2", "age": 30, "level": "user"}
  ],
  "rules": [
    {"id": "age-range", "type": "range",
     "options": {"field": "age", "min": 18, "max": 120}},
    {"id": "level-allowed", "type": "allowed_values",
     "options": {"field": "level", "values": ["user", "guest"]}}
  ]
}
```

输出示例：

```json
{
  "passed": false,
  "summary": {"record_count": 2, "violation_count": 2, "checked_rule_count": 2},
  "violations": [
    {"rule_id": "age-range", "record_index": 0, "record_id": "r1",
     "field": "age", "value": 17, "message": "value out of range"},
    {"rule_id": "level-allowed", "record_index": 0, "record_id": "r1",
     "field": "level", "value": "admin", "message": "value is not allowed"}
  ]
}
```

全部通过时 `passed` 为 `true` 且 `violations` 为空数组。

### 退出码与错误

| 场景 | error.code | 退出码 |
| --- | --- | --- |
| JSON 无法解析（或非 UTF-8） | `INVALID_JSON` | 2 |
| 顶层结构或 records 非法 | `INVALID_INPUT` | 2 |
| 规则定义非法（未知类型、重复 id、选项冲突等） | `INVALID_RULE` | 2 |
| 校验完成（含发现违规） | — | 0 |

错误输出形如 `{"ok": false, "error": {"code": "...", "message": "..."}}`。

## Python API

```python
from data_quality import validate

result = validate(records, rules)
```

入参非法（参数或结构错误、未知类型、重复 id、选项冲突等）时抛 `ValueError`
（子类 `InvalidInputError` / `InvalidRuleError`，位于 `data_quality.validator`）。

## 规则

检查顺序为「先规则、后记录」：规则按输入顺序排列，每条规则内按记录顺序检查，
因此可通过 `rule_id` + `record_index`（从 0 开始）定位异常样本。
每条违规包含 `rule_id`、`record_index`、`record_id`（缺失时为 `null`）、
`field`、原始 `value` 与固定 `message`。

| type | options | 语义 |
| --- | --- | --- |
| `required` | `field` | 字段缺失或值为 `null` 即违规 |
| `unique` | `field` | 对非 null 值按 JSON 相等判重，只报告后出现的重复项 |
| `range` | `field`, `min`, `max` | 闭区间 `[min, max]`；非数字值（含 null、布尔、缺失）违规 |
| `regex` | `field`, `pattern` | 用 `pattern` 做字符串**完全匹配**；非字符串值违规 |
| `allowed_values` | `field`, `values` | 值必须在 `values` 中；缺失字段按 `null` 判断 |

规则要求：每条规则须有非空且不重复的字符串 `id`、受支持的 `type`、对象类型的
`options`，且 `options.field` 为非空字符串；`range` 的 `min`/`max` 必须是数字
且 `min <= max`；`regex` 的 `pattern` 必须是合法正则。

## 约定

- 公开行为以 README 与源码为准。
- 除标准输入输出外不写文件、不访问外部服务。
- 后续需求在此基线上增量实现。
