# Data Quality

数据质量校验与血缘引擎：规则校验、异常样本定位、上下游血缘追溯。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：独立的数据质量规则校验（Python 包 `data_quality` 与命令行 `dq validate`）。
- 已实现：上下游血缘追溯（`trace_lineage` 与命令行 `dq lineage`）。

## 安装

需要 Python 3.8+，仅使用标准库：

```bash
pip install -e .
```

安装后提供命令行入口 `dq`；也可通过 `python -m data_quality` 调用。

## Python API

```python
from data_quality import validate

result = validate(records, rules)
```

- `records`：JSON 对象（dict）组成的列表。
- `rules`：规则对象列表，每条规则含 `id`、`type`、`options`。
- 规则在校验任何记录之前先统一校验与排序；随后按规则顺序、记录顺序检查。

### 规则类型

字段配置统一位于 `options.field`。

| 规则 | options | 违反条件 |
| --- | --- | --- |
| `required` | `field` | 字段缺失或值为 `null`（`0`、`""`、`false` 不算缺失） |
| `unique` | `field` | 非 `null` 值按 JSON 相等判重，只报告后出现的重复项；`null` 不参与判重 |
| `range` | `field`、`min`、`max` | 值不是数字，或不在 `[min, max]` 闭区间内（布尔值不是数字） |
| `regex` | `field`、`pattern` | 值不是字符串，或不能被 `pattern` **完全匹配**（`re.fullmatch`） |
| `allowed_values` | `field`、`values` | 值不在 `values` 中；字段缺失按 `null` 判断（因此可用 `null` 作为允许值） |

JSON 相等为结构化比较：对象忽略键顺序、数组按顺序逐项比较，布尔值不与数字相等。

### 返回结果

```json
{
  "passed": false,
  "summary": {
    "record_count": 4,
    "violation_count": 7,
    "checked_rule_count": 5
  },
  "violations": [
    {
      "rule_id": "name-req",
      "record_index": 1,
      "record_id": "rec-1",
      "field": "name",
      "value": null,
      "message": "is required but is missing or null"
    }
  ]
}
```

- 每条违反包含 `rule_id`、从 0 开始的 `record_index`、`record_id`、`field`、原始 `value` 和固定 `message`。
- `record_id` 取记录的 `id` 字段；记录没有 `id` 字段时为 `null`。
- 字段缺失时 `value` 为 `null`。
- 全部通过时 `passed` 为 `true` 且 `violations` 为空；否则 `passed` 为 `false`。
- 依据 `rule_id` + `record_index`（及 `record_id`）即可定位异常样本。

### 错误

以下情况抛出 `ValueError` 子类（均位于 `data_quality`）：

- `InvalidInputError`：`records` 不是列表，或其中元素不是 JSON 对象。
- `InvalidRuleError`：规则结构错误、未知类型、`id` 为空或重复、`options` 不匹配、`min > max`、`pattern` 不是合法正则等。

## 命令行

`dq validate` 从标准输入读取一个 UTF-8 JSON 对象，字段为 `records` 与 `rules`，并以 UTF-8 JSON 输出结果：

```bash
dq validate < payload.json
```

成功（无论是否有违反）退出码为 0；输入有误时退出码为 2，并向标准输出写入：

```json
{"error": {"code": "INVALID_RULE", "message": "..."}}
```

错误码：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_INPUT`：载荷不是 JSON 对象、缺少 `records`/`rules`、或 `records` 结构错误。
- `INVALID_RULE`：规则定义非法（未知类型、重复 id、选项冲突、非法正则等）。

除标准输入与标准输出外，不写文件、不访问外部服务。

## 血缘追溯

### Python API

```python
from data_quality import trace_lineage

result = trace_lineage(nodes, edges, target, direction="both", max_depth=None)
```

- `nodes`：非空且互不重复的非空字符串列表，声明全部节点。
- `edges`：边对象列表，每条边仅含 `source`、`target` 两个字段，表示 `source` 指向 `target`；端点必须已在 `nodes` 中声明，重复边报错。自环与环路合法，遍历必然有限。
- `target`：起始节点 id（depth 为 0）。
- `direction`：`"upstream"`（沿边反向扩展）、`"downstream"`（沿边正向扩展）或 `"both"`（默认，返回两侧）。
- `max_depth`：`None`（默认，不限深度）或大于等于 0 的整数（布尔值不算整数）；`0` 只含 target 本身。

### 返回结果

```json
{
  "target": "c",
  "direction": "both",
  "max_depth": null,
  "upstream": {
    "nodes": [{"id": "c", "depth": 0}, {"id": "a", "depth": 1}],
    "edges": [{"source": "a", "target": "c"}]
  },
  "downstream": {
    "nodes": [{"id": "c", "depth": 0}, {"id": "d", "depth": 1}],
    "edges": [{"source": "c", "target": "d"}]
  }
}
```

- 每一侧含 `nodes` 与 `edges`。节点含 `id`、`depth`：target 为 0，每向外相邻一层加 1；每个节点按最短 depth 唯一出现，按 `depth`、`id` 排序。
- `edges` 为两侧端点都在本侧节点集合内的全部原始边，按 `source`、`target` 排序。
- 无亲属时该侧 `nodes` 只有 target、`edges` 为空；单侧查询时另一侧为空。

### 错误

以下情况抛出 `ValueError` 子类（均位于 `data_quality`）：

- `InvalidLineageInputError`：`nodes`/`edges` 缺失或结构、端点、重复项有误。
- `InvalidLineageQueryError`：`direction`、`max_depth` 或 `target` 非法。
- `UnknownLineageTargetError`：`target` 未在 `nodes` 中声明。

### 命令行

`dq lineage` 从标准输入读取一个 UTF-8 JSON 对象，字段为 `nodes`、`edges`、`target` 与可省略的 `direction`、`max_depth`，并以 UTF-8 JSON 输出结果：

```bash
dq lineage < lineage.json
```

合法查询退出码为 0；输入有误时退出码为 2，并向标准输出写入 `{"error": {"code": ..., "message": ...}}`（`message` 非空）。错误码：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_LINEAGE_INPUT`：`nodes`/`edges` 结构、端点或重复项有误。
- `INVALID_LINEAGE_QUERY`：`direction`、`max_depth` 或 `target` 非法。
- `UNKNOWN_LINEAGE_TARGET`：`target` 未在 `nodes` 中声明。

## 测试

```bash
python -m unittest discover -s tests
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
