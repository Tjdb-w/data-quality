# Data Quality

数据质量校验与血缘引擎：规则校验、异常样本定位、上下游血缘追溯。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：独立的数据质量规则校验（Python 包 `data_quality` 与命令行 `dq validate`）。
- 已实现：上下游血缘追溯（`trace_lineage` 与命令行 `dq lineage`）。
- 已实现：字段级上下游血缘追溯（`trace_field_lineage` 与命令行 `dq field-lineage`）。
- 已实现：异常样本跨规则关联定位（`correlate_sample_anomalies` 与命令行 `dq correlate-anomalies`）。

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

## 血缘追溯

`nodes`、`edges` 构成血缘图，边由 `source` 指向 `target`。

```python
from data_quality import trace_lineage

result = trace_lineage(nodes, edges, target, direction="both", max_depth=None)
```

- `nodes`：非空、互不重复的非空字符串列表。
- `edges`：仅含 `source`、`target` 两个键的对象列表，两端必须已在 `nodes` 声明，重复边报错；自环与环路合法。
- `target`：追溯起点节点 id。
- `direction`：`upstream`（沿边反向）、`downstream`（沿边正向）或 `both`（默认，两侧都返回）。
- `max_depth`：`null`（默认，不限深度）或大于等于 0 的整数；布尔值不算整数；`0` 时只含 `target`。

### 血缘返回结果

```json
{
  "target": "dwd",
  "direction": "both",
  "max_depth": null,
  "upstream": {
    "nodes": [{"id": "dwd", "depth": 0}, {"id": "ods", "depth": 1}],
    "edges": [{"source": "ods", "target": "dwd"}]
  },
  "downstream": {
    "nodes": [{"id": "dwd", "depth": 0}, {"id": "ads", "depth": 1}],
    "edges": [{"source": "dwd", "target": "ads"}]
  }
}
```

- 结果回显 `target`、`direction`、`max_depth`，并含 `upstream`、`downstream` 两侧，各含 `nodes`、`edges`。
- 节点为 `{"id", "depth"}`：`target` 深度为 0，每跨一条边加 1；同一节点按最短深度唯一出现，节点按 `(depth, id)` 排序。
- 边为该侧已到达节点之间的**全部原始边**（不只最短路径树），按 `(source, target)` 排序。
- 无亲属时该侧 `nodes` 只有 `target`、`edges` 为空；只查一侧时另一侧为 `{"nodes": [], "edges": []}`。

### 血缘错误

以下情况抛出 `ValueError` 子类（均公开于 `data_quality`）：

- `InvalidLineageInputError`：`nodes`/`edges` 缺失或结构有误、节点重复或为空、边含多余/缺失键、端点未声明、边重复。
- `InvalidLineageQueryError`：`target` 不是字符串、`direction` 不在三者之内、`max_depth` 不是 `null` 或非负整数（含布尔值）。
- `UnknownLineageTargetError`：`target` 未在 `nodes` 中声明。

校验顺序为图结构 → 查询参数 → target 声明性。

## 字段级血缘追溯

`fields` 声明各表的字段，`edges` 为字段间的有向边，边由 `source` 指向 `target`。

```python
from data_quality import trace_field_lineage

result = trace_field_lineage(fields, edges, target, direction="both", max_depth=None)
```

- `fields`：JSON 对象，键为非空表 id，值为互不重复的非空字段 id 列表。
- `edges`：仅含 `source`、`target` 两个键的对象列表；两端均为只含 `table`、`column` 且已在 `fields` 声明的字段，重复边报错；自环与环路合法。
- `target`：追溯起点字段，只含 `table`、`column` 两个键。
- `direction`：`upstream`（沿边反向）、`downstream`（沿边正向）或 `both`（默认，两侧都返回）。
- `max_depth`：`null`（默认，不限深度）或大于等于 0 的整数；布尔值不算整数；`0` 时只含 `target`。

### 字段级血缘返回结果

```json
{
  "target": {"table": "dwd", "column": "label"},
  "direction": "both",
  "max_depth": null,
  "upstream": {
    "fields": [
      {"table": "dwd", "column": "label", "depth": 0},
      {"table": "ods", "column": "name", "depth": 1}
    ],
    "edges": [
      {
        "source": {"table": "ods", "column": "name"},
        "target": {"table": "dwd", "column": "label"}
      }
    ]
  },
  "downstream": {
    "fields": [
      {"table": "dwd", "column": "label", "depth": 0},
      {"table": "ads", "column": "label", "depth": 1}
    ],
    "edges": [
      {
        "source": {"table": "dwd", "column": "label"},
        "target": {"table": "ads", "column": "label"}
      }
    ]
  }
}
```

- 结果回显 `target`、`direction`、`max_depth`，并含 `upstream`、`downstream` 两侧，各含 `fields`、`edges`。
- 字段为 `{"table", "column", "depth"}`：`target` 深度为 0，每跨一条边加 1；同一字段按最短深度唯一出现，字段按 `(depth, table, column)` 排序。
- 边为该侧已到达字段之间的**全部原始边**（保留原对象），按 `(source.table, source.column, target.table, target.column)` 排序。
- 无亲属时该侧 `fields` 只有 `target`、`edges` 为空；只查一侧时另一侧为 `{"fields": [], "edges": []}`。

### 字段级血缘错误

以下情况抛出 `ValueError` 子类（均公开于 `data_quality`）：

- `InvalidFieldLineageInputError`：`fields`/`edges` 缺失或结构有误、表 id 或字段 id 为空、字段重复、边含多余/缺失键、端点未声明或结构错误、边重复。
- `InvalidFieldLineageQueryError`：`target` 不是只含 `table`、`column` 的非空字符串对象、`direction` 不在三者之内、`max_depth` 不是 `null` 或非负整数（含布尔值）。
- `UnknownFieldLineageTargetError`：`target` 未在 `fields` 中声明。

校验顺序为图结构 → 查询参数 → target 声明性。

## 异常样本跨规则关联定位

把一批带稳定样本标识的校验结果与一份字段血缘图同时提交；同一样本上由同一字段或血缘相邻字段（一条有效边直接相连）触发的违规归入一个关联事件。仅同属一个数据集而无血缘边的字段不会被合并。

```python
from data_quality import correlate_sample_anomalies

result = correlate_sample_anomalies(results, lineage_graph)
```

- `results`：校验结果列表，每条结果只含以下键：
  - `rule_id`、`dataset_id`、`field_id`、`sample_id`：均为非空字符串。
  - `is_violation`：布尔值（`true`/`false`，不接受 `1`/`0`）。
  - `violating_value`：违规值，任意 JSON 值；未违规则通常为 `null`。
- `lineage_graph`：`{"nodes": [...], "edges": [...]}`。
  - `nodes`：`{"dataset_id", "field_id"}` 对象列表，互不重复。
  - `edges`：只含 `source`、`target`、`type` 三个键；两端均为已声明字段，`type` 只能是 `upstream`（`target` 喂给 `source`）或 `downstream`（`source` 喂给 `target`）。重复边必须表达相同流向，相互矛盾时报错。

### 关联规则

- 按 `sample_id` 分别处理：同一 `(sample_id, rule_id)` 重复出现且内容完全一致时只保留一条；内容冲突（字段、是否违规或违规值不一致）抛出 `ValueError`。
- 每个样本只取违规结果（`is_violation` 为 `true`）建图；通过的结果不产生事件。全部通过或 `results` 为空时返回 `{"events": []}`，不会产生仅含空字段的占位事件。
- 事件内字段彼此为同一字段或由有效边逐跳相连（中间字段也须在该样本上违规才会连通两侧）；没有任何边相连的字段分属不同事件。

### 关联返回结果

```json
{
  "events": [
    {
      "sample_id": "s-1",
      "rule_ids": ["range-age", "regex-age-bucket"],
      "fields": [
        {"dataset_id": "ods", "field_id": "age"},
        {"dataset_id": "dwd", "field_id": "age_bucket"}
      ],
      "upstream_fields": [{"dataset_id": "raw", "field_id": "age"}],
      "downstream_fields": [{"dataset_id": "ads", "field_id": "age_group"}]
    }
  ]
}
```

- `rule_ids` 按规则标识排序去重；`fields` 为该事件承载违规的字段集合。
- `upstream_fields` / `downstream_fields` 为事件字段的**直接**上游/下游邻居（各只跨一条边），不含事件自身字段；自环不产生邻居。
- 所有集合按 `(dataset_id, field_id)` 排序去重；事件按 `sample_id` 排序（同一样本的多个事件相邻，顺序按其最小字段稳定确定）。相同输入始终得到完全相同的结果，结果中不引入时间信息、随机标识或未提供的外部元数据。
- 原始校验记录与血缘边均不会被改写。

### 关联错误

以下情况抛出异常（均公开于 `data_quality`）：

- `InvalidCorrelationInputError`（`ValueError` 子类）：`results` 不是列表、元素不是对象、键缺失或多余、任一标识为空或非字符串、`is_violation` 不是布尔值。
- `InvalidCorrelationGraphError`（`ValueError` 子类）：`lineage_graph` 结构有误、节点为空/重复、边含多余或缺失键、边类型非法、悬空边（端点未声明）或相互矛盾的重复边。
- `UnknownCorrelationReferenceError`（**`LookupError` 子类**）：校验结果引用了未在 `nodes` 声明的数据集或字段（通过结果同样受此约束）。
- 同一 `(sample_id, rule_id)` 的重复记录内容冲突时抛出普通 `ValueError`。

校验顺序为结果结构 → 图结构 → 引用声明性 → 重复记录一致性。

## 命令行

### dq validate

从标准输入读取一个 UTF-8 JSON 对象，字段为 `records` 与 `rules`，并以 UTF-8 JSON 输出结果：

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

### dq lineage

从标准输入读取同字段的 UTF-8 JSON 对象（`nodes`、`edges`、`target` 必填；`direction`、`max_depth` 可省略，默认 `both` 与 `null`）：

```bash
dq lineage < lineage.json
```

合法查询退出码为 0 并输出上述血缘结果；输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_LINEAGE_INPUT`：`nodes`/`edges` 结构、端点或重复项有误。
- `INVALID_LINEAGE_QUERY`：`target`/`direction`/`max_depth` 非法。
- `UNKNOWN_LINEAGE_TARGET`：`target` 未在 `nodes` 中声明。

### dq field-lineage

从标准输入读取字段级血缘的 UTF-8 JSON 对象（`fields`、`edges`、`target` 必填；`direction`、`max_depth` 可省略，默认 `both` 与 `null`）：

```bash
dq field-lineage < field-lineage.json
```

合法查询退出码为 0 并输出上述字段级血缘结果；输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_FIELD_LINEAGE_INPUT`：`fields`/`edges` 结构、端点或重复项有误。
- `INVALID_FIELD_LINEAGE_QUERY`：`target`/`direction`/`max_depth` 非法。
- `UNKNOWN_FIELD_LINEAGE_TARGET`：`target` 未在 `fields` 中声明。

### dq correlate-anomalies

从标准输入读取 UTF-8 JSON 对象，字段为 `results` 与 `lineage_graph`：

```bash
dq correlate-anomalies < anomalies.json
```

合法输入退出码为 0 并输出上述关联事件结果（无违规时输出 `{"events": []}`）；输入有误时退出码为 2，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_CORRELATION_INPUT`：载荷不是 JSON 对象、缺少 `results`，或校验结果结构/标识非法。
- `INVALID_CORRELATION_GRAPH`：缺少 `lineage_graph`，或节点/边结构、边类型、悬空边、矛盾重复边有误。
- `CONFLICTING_CORRELATION_RESULT`：同一 `(sample_id, rule_id)` 的重复记录内容冲突。
- `UNKNOWN_CORRELATION_REFERENCE`：校验结果引用了未声明的数据集或字段。

除标准输入与标准输出外，不写文件、不访问外部服务。

## 测试

```bash
python -m unittest discover -s tests
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
