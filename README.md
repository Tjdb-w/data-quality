# Data Quality

数据质量校验与血缘引擎：规则校验、异常样本定位、上下游血缘追溯。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：独立的数据质量规则校验（Python 包 `data_quality` 与命令行 `dq validate`）。
- 已实现：上下游血缘追溯（`trace_lineage` 与命令行 `dq lineage`）。
- 已实现：字段级上下游血缘追溯（`trace_field_lineage` 与命令行 `dq field-lineage`）。
- 已实现：血缘路径解释与影响范围查询（`explain_lineage_paths` / `explain_field_lineage_paths` 与命令行 `dq lineage-paths` / `dq field-lineage-paths`）。
- 已实现：异常样本跨规则关联定位（`correlate_violations` 与命令行 `dq correlate`）。
- 已实现：跨数据集关联样本的异常关联定位（`correlate_linked_violations` 与命令行 `dq correlate-links`）。
- 已实现：异常来源证据分析（`analyze_violation_origins` 与命令行 `dq violation-origins`）。
- 已实现：字段级质量影响分析（`analyze_field_impacts`，Python API；命令行不变）。
- 已实现：跨字段一致性规则（`register_composite_rules` / `evaluate_composite_rules` / `query_composite_results`，并经 `validate` 与命令行 `dq validate` / `dq query-results` 使用）。

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

## 跨字段一致性规则

跨字段（组合）规则判断**同一条记录内**多个字段是否共同满足约束。规则仍通过既有公开入口注册、执行与查询：`validate` 增加仅关键字参数 `dataset`、`composite_rules`、`record_refs`，一次执行同时保留单字段规则结果并产出跨字段结果；单字段规则的通过/告警/失败结论、失败样本内容、优先级、错误码与结果结构完全不变。也可以直接使用下列函数（均公开于 `data_quality`）：

```python
from data_quality import (
    register_composite_rules,      # 注册（编译）规则集，全有或全无
    evaluate_composite_rules,      # 逐记录评估
    query_composite_results,       # 按 rule_id / 严重级别 / 记录定位筛选
)
```

### 规则定义

```python
rule_set = register_composite_rules("dwd_orders", [
    {
        "rule_id": "date-order",                 # 稳定的规则标识
        "fields": ["start_date", "end_date"],    # 目标字段集合
        "severity": "error",                     # error / warning / info
        "conditions": [                          # 非空，多个条件做与（AND）组合
            {"type": "date_before",
             "earlier_field": "start_date",
             "later_field": "end_date"}
        ],
    },
])
results = evaluate_composite_rules(records, rule_set)
```

- `rule_id`：非空字符串，同一规则集内唯一；重复注册返回 `DUPLICATE_RULE_ID`。
- `fields`：非空、互不重复的非空字段名列表，即规则的目标字段集合；条件只能引用其中声明的字段，引用不存在（未声明）的字段属于 `INVALID_COMPOSITE_RULE`。
- `severity`：严重级别，只能是 `error`、`warning`、`info`；其他值返回 `INVALID_SEVERITY`，不会静默改写为默认级别。
- `conditions`：非空条件列表，逐项 AND 组合，全部满足才为 `PASSED`。

四类关联条件：

| 条件 type | 字段 | 含义 |
| --- | --- | --- |
| `field_equal` | `left_field`、`right_field` | 两个字段值按 JSON 相等必须相等（布尔不与数字相等） |
| `field_not_equal` | `left_field`、`right_field` | 两个字段值必须不相等 |
| `date_before` | `earlier_field`、`later_field`，可选 `format` | `earlier_field` 的日期必须**严格早于** `later_field`；日期按严格 `YYYY-MM-DD` 解析（目前唯一支持的 `format`，不支持的格式注册期拒绝），相等、非法日期均判违反 |
| `required_when` | `when_field`、`equals`（可省略，默认 `null`）、`required_field` | 当 `when_field` 等于 `equals`（前置条件成立）时，`required_field` 必须存在且非 `null`；前置条件不成立时该条件自动满足 |

四类条件均禁止引用自身（两侧指向同一字段，或 `required_field` 与 `when_field` 相同），注册期以 `INVALID_COMPOSITE_RULE` 拒绝，并附带可定位的 `rule_id` 与字段名。

### 执行与结论

逐条读取记录，同一条记录内按规则的注册顺序评估；输出顺序为「记录顺序 × 注册顺序」，同一条记录违反多条规则时保留全部结果并稳定输出。每条结论：

- `PASSED`：目标字段齐全且全部条件满足。
- `FAILED`：目标字段齐全但至少一个条件不满足。
- `SKIPPED_MISSING_FIELD`：该记录缺少目标字段集合中的任一字段。既不判为通过，也不影响其他规则继续执行；`context.missing_fields` 给出缺失字段。

完整结果对象包含：`dataset_id`（数据集标识）、`record_index`（从 0 开始的记录定位）、`record_id`（记录的 `id`，无则为 `null`）、`rule_id`、`fields`（关联字段）、`status`（规则结论）、`severity`（严重级别）与 `context`（可供下游查询的上下文：参与字段值快照 `field_values`、`missing_fields`、逐条件 `satisfied` 结论）。

```python
validate(
    records,
    single_field_rules,
    dataset="dwd_orders",
    composite_rules=composite_rules,
    record_refs=[{"record_id": "rec-1"}],   # 可选，只评估指定记录
)
# 返回在既有 {"passed", "summary", "violations"} 之外追加：
# "composite_rule_count" 与 "composite_results"
```

- 不传 `composite_rules` 时，`validate` 的返回结构、字段与历史完全一致（不新增任何键）。
- 所有规则（单字段与跨字段）在校验任何记录之前完成注册校验；任何一条跨字段规则无法确定执行结果都会中止整次执行，**不会部分注册**。
- 单字段规则仍保持规则优先（rule-major）的输出与失败样本内容；跨字段结果单独存放，不改变 `passed`、`summary`、`violations`。
- `record_refs` 用于只评估能定位到的记录：元素为 `{"record_index": i}`、`{"record_id": "..."}` 或二者同时给出（必须一致）；无法定位到记录（越界、未知 id、id 与下标不一致、结构非法）返回 `INVALID_RECORD_REFERENCE`。省略时评估全部记录。

### 结果查询

```python
query_composite_results(
    results,
    rule_id="date-order",   # 可选
    severity="error",       # 可选
    record_index=3,         # 可选
    record_id="rec-3",      # 可选
)
# {"count": n, "results": [...]}
```

多个筛选条件之间为 AND；筛选不重排结果，继续遵守「记录顺序 × 注册顺序」的稳定顺序，也不改变既有单字段规则的历史查询口径。命令行入口为 `dq query-results`。

### 异常样本定位与血缘消费

跨字段 `FAILED` 结果可直接被既有异常样本定位消费，且不改变既有入口的输入输出：

```python
from data_quality import composite_results_to_impact_inputs

adapted = composite_results_to_impact_inputs(results)
# {"validationResults": [...], "anomalySamples": {...}}
```

- 每条失败规则映射为一条 `status="failed"` 的 `validationResults`，`fields` 为全部参与字段的 `{"dataset", "field"}` 引用（按 `rule_id` 排序）；每条失败记录映射为一个可定位的 `anomalySamples`，样本 id 优先取记录的非空字符串 `id`，否则使用确定性的 `"record:<record_index>"`，与结果中的 `record_index` / `record_id` 一致。
- 从某条跨字段异常跳回对应记录与参与字段时，定位信息（数据集、记录下标/记录 id、字段名与字段值快照）与校验结果完全一致；调用方只需再补充 `datasets`、`lineageEdges`、`seedFields` 即可调用 `analyze_field_impacts`。
- 若异常字段属于已有血缘节点，直接以结果中的 `{"dataset", field}`（字段级血缘中为 `{"table", "column"}`）调用 `trace_field_lineage`，沿用当前上下游追溯语义展示其关联输入与输出。

### 注册与执行错误

以下异常均为 `ValueError` 子类（公开于 `data_quality`），携带稳定 `code`，并在适用时附带可定位的 `rule_id` 与 `field_name`；注册类错误保证全有或全无：

| 异常 | 错误码 | 触发情形 |
| --- | --- | --- |
| `CompositeRuleSetError` | `INVALID_RULE_SET` | 规则集为空或不是列表、`dataset_id` 缺失/为空、规则不是对象 |
| `DuplicateRuleIdError` | `DUPLICATE_RULE_ID` | 同一规则集内 `rule_id` 重复 |
| `InvalidSeverityError` | `INVALID_SEVERITY` | 严重级别不在 `error`/`warning`/`info` 内 |
| `UnsupportedCompositeConditionError` | `UNSUPPORTED_COMPOSITE_CONDITION` | 条件 `type` 不受支持 |
| `InvalidCompositeRuleError` | `INVALID_COMPOSITE_RULE` | 字段不存在（未在 `fields` 声明）、条件引用自身、日期字段格式（`format`）不合法、字段集合/条件结构非法、`rule_id` 非法、键缺失或多余等一切无法确定执行结果的定义 |
| `InvalidRecordReferenceError` | `INVALID_RECORD_REFERENCE` | 执行时记录定位无法解析，或记录本身不是 JSON 对象列表 |

空规则集、重复 `rule_id`、未知严重级别、不支持的关联条件分别对应上表前四个错误码，绝不静默忽略或改写为默认规则。

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

## 血缘路径解释与影响范围查询

在既有血缘图上，`explain_lineage_paths`（节点级）与 `explain_field_lineage_paths`（字段级）把目标节点到上游来源、下游受影响对象之间的**实际有序路径**逐条返回。入参与 `trace_lineage` / `trace_field_lineage` 完全相同，校验规则与错误语义复用既有实现；本功能只返回内存中的查询结果，不新增任何落盘行为。

```python
from data_quality import explain_lineage_paths, explain_field_lineage_paths

result = explain_lineage_paths(nodes, edges, target, direction="both", max_depth=None)
```

- `nodes` / `fields` / `edges` / `target` / `direction` / `max_depth`：与血缘追溯接口一致，`upstream` 沿边反向回溯来源，`downstream` 沿边正向传播到直接与间接依赖对象。
- 每个方向枚举从 `target` 出发的全部**简单路径**：同一节点可在不同路径中出现，但同一条路径不会重复经过节点，因此不会因环而无限延伸。
- `max_depth` 限制每条路径的边数；`0` 时该方向只含一条仅含目标节点的路径。

### 路径解释返回结果

```json
{
  "target": "dwd",
  "direction": "both",
  "max_depth": null,
  "upstream": {
    "paths": [
      {
        "depth": 2,
        "nodes": [
          {"node": {"id": "dwd", "depth": 0}, "edge": null},
          {"node": {"id": "ods", "depth": 1}, "edge": {"source": "ods", "target": "dwd"}},
          {"node": {"id": "raw", "depth": 2}, "edge": {"source": "raw", "target": "ods"}}
        ]
      }
    ],
    "cycle": false,
    "cycle_nodes": [],
    "cycle_edges": []
  },
  "downstream": {
    "paths": [],
    "cycle": false,
    "cycle_nodes": [],
    "cycle_edges": []
  }
}
```

- 结果回显 `target`、`direction`、`max_depth`，并含 `upstream`、`downstream` 两侧；只查一侧时另一侧为 `{"paths": [], "cycle": false, "cycle_nodes": [], "cycle_edges": []}`。
- 每条路径含 `depth`（边数）与有序的 `nodes` 步骤；每一步含：
  - `node`：稳定可识别的节点标识与可展示名称。节点级为 `{"id", "depth"}`；字段级为 `{"table", "column", "depth"}`（表名、字段名）。`depth` 即该节点在路径中的先后位置，目标节点为 0。
  - `edge`：造成该关系的**直接边**，为造成本节点与上一节点关系的原始边对象；目标节点处为 `null`。
- 路径按 `(depth, 路径节点稳定标识序列, 路径边稳定标识序列)` 排序，相同输入重复查询结果完全一致。
- 目标存在但某方向没有任何关系时，该方向为正常成功结果（退出码 0、无 error 包装）、`paths` 为空数组，不视为异常。
- 发现环时 `cycle` 为 `true`，`cycle_nodes` 为环上节点（按稳定标识排序），`cycle_edges` 为组成环的有向边（含闭合环的回边，按边稳定标识排序）；已经确定的路径段与其余独立路径照常保留，不会因环被丢弃。
- 字段级结果中 `target` 与 `node` 为 `{"table", "column"}`，`edge` 的 `source` / `target` 同为字段对象。

### 路径解释错误

- 图结构与查询参数错误沿用既有异常：节点级为 `InvalidLineageInputError` / `InvalidLineageQueryError`，字段级为 `InvalidFieldLineageInputError` / `InvalidFieldLineageQueryError`（均为 `ValueError` 子类）。
- `LineageNodeNotFoundError`（`ValueError` 子类，属性 `code = "LINEAGE_NODE_NOT_FOUND"`）：`target` 未在图中声明。此错误码为两个入口共用，节点级与字段级一致。

校验顺序为图结构 → 查询参数 → target 声明性，与血缘追溯保持一致。

## 异常样本跨规则关联定位

`correlate_violations` 把一批带稳定样本标识的校验结果与一张字段级血缘图合并，把同一样本上由同一字段或其血缘相邻字段触发的违规归入一个关联事件。

```python
from data_quality import correlate_violations

result = correlate_violations(results, lineage)
```

- `results`：校验结果对象列表，每条恰好包含六个键：
  - `rule_id`、`dataset_id`、`field_id`、`sample_id`：非空字符串；任一为空（或非字符串）抛出 `ValueError`。
  - `violated`：布尔值；`false` 的记录只参与校验，不产生事件。
  - `value`：任意 JSON 值（违规值，可为 `null`）。
  - 同一规则对同一样本重复出现时只保留一条；若重复记录内容（数据集、字段、是否违规、违规值）冲突则抛出 `ValueError`。
- `lineage`：血缘图对象，恰好包含三个键：
  - `datasets`：互不重复的非空数据集 id 列表（数据集节点，可为空列表）。
  - `fields`：对象，键为已声明的数据集 id，值为互不重复的非空字段 id 列表（字段节点）。
  - `edges`：类型化上下游边列表，每条恰好含 `source`、`target`、`type` 三键；两端为 `{"dataset": ..., "field": ...}` 且必须已在 `fields` 声明（悬空边报错）；`type` 只能是 `upstream`（target 是 source 的直接上游）或 `downstream`（target 是 source 的直接下游）。等价重述的边只保留一条；相互矛盾的重复边（同一对字段被同时断言为相反方向）抛出 `ValueError`。
- 校验结果引用未在血缘图中声明的数据集或字段时抛出 `LookupError`。

### 关联定位返回结果

```json
{
  "events": [
    {
      "sample_id": "样本-1",
      "rules": ["r1", "r2"],
      "fields": [
        {"dataset": "dwd", "field": "label"},
        {"dataset": "ods", "field": "name"}
      ],
      "upstream_fields": [],
      "downstream_fields": [{"dataset": "ads", "field": "label"}]
    }
  ]
}
```

- 同一样本上，只要两个违规字段之间存在一条有效血缘边（任一方向）就连通；连通分量即一个事件。仅因同属一个数据集但没有血缘边的字段不会被错误合并。
- `rules` 为事件内违规规则 id，按规则标识升序排列。
- `fields` 为受影响字段集合，`upstream_fields` / `downstream_fields` 为受影响字段在血缘图中的直接上游 / 直接下游字段（不含事件内字段本身）；三者均去重并按 `(dataset, field)` 稳定排序。
- 事件按 `sample_id` 排序输出；无违规结果时返回 `{"events": []}`，不产生仅含空字段的占位事件。
- 相同输入始终得到完全相同的结果；时间信息、随机标识与未提供的外部元数据不会进入结果，输入的校验记录与血缘图不会被改写。

### 关联定位错误

以下情况抛出异常（均公开于 `data_quality`）：

- `InvalidCorrelationInputError`（`ValueError` 子类）：`results`/`lineage` 结构有误、标识为空、重复记录内容冲突、边含多余/缺失键、悬空边、非法边类型、相互矛盾的重复边。
- `UnknownCorrelationReferenceError`（`LookupError` 子类）：校验结果引用了未声明的数据集或字段。

校验顺序为血缘图结构 → 校验结果结构（含重复冲突）→ 引用解析。

## 跨数据集关联样本的异常关联定位

`correlate_linked_violations` 是 `correlate_violations` 的跨数据集版本：`results`、`lineage` 的结构、校验与字段分组语义完全沿用前者，额外接收一份无向的 `sample_links`，把不同数据集（或同数据集）中对应的样本关联起来。

```python
from data_quality import correlate_linked_violations

result = correlate_linked_violations(results, lineage, sample_links)
```

- `results` / `lineage`：与 `correlate_violations` 完全相同，校验规则、异常与错误码不变。
- `sample_links`：关联关系对象列表，每条恰好含 `left`、`right` 两个键；两端均为恰好含 `dataset_id`、`sample_id` 两个非空字符串键的对象。
  - 关系**无向**：`{left: A, right: B}` 与 `{left: B, right: A}` 是同一条关系。
  - 端点只含样本定位，**不含 value、也不比较 value**：仅按 `dataset_id` + `sample_id` 与 `results` 中的样本匹配。
  - 每个端点都必须能在 `results` 中找到相同 `dataset_id` 与 `sample_id` 的结果（该结果是否违规均可）。

### 关联与分组

- 以每条结果的 `dataset_id` 与 `sample_id` 构造样本引用 `{"dataset_id", "sample_id"}`，沿无向 `sample_links` 求**传递闭包**：每个连通分量是一个样本关联组。
- 仅 `violated=true` 的结果参与事件；同一关联组内，违规字段继续按字段血缘的**相邻语义**分组（一条有效血缘边相连才合并，仅同数据集但无血缘边不合并）。
- 全为通过结果的关联组（含仅由通过样本桥接的分量）不产生事件；无任何违规时返回 `{"events": []}`。

### 返回结果

```json
{
  "events": [
    {
      "sample_refs": [
        {"dataset_id": "dwd", "sample_id": "样本-1"},
        {"dataset_id": "ods", "sample_id": "样本-a"}
      ],
      "rules": ["r1", "r2"],
      "fields": [
        {"dataset": "dwd", "field": "label"},
        {"dataset": "ods", "field": "name"}
      ],
      "upstream_fields": [],
      "downstream_fields": [{"dataset": "ads", "field": "label"}]
    }
  ]
}
```

事件恰好含五个键，顺序固定：

- `sample_refs`：事件内去重后的样本引用，按 `(dataset_id, sample_id)` 排序。
- `rules`：去重后的违规规则 id，按 id 升序。
- `fields`：去重后的受影响字段引用 `{"dataset", "field"}`，按 `(dataset, field)` 排序。
- `upstream_fields` / `downstream_fields`：受影响字段在血缘图中分量外的直接上游 / 直接下游字段（不含事件字段本身），去重并按 `(dataset, field)` 排序。
- 事件整体按 `(sample_refs 序列, fields 序列)` 排序；相同输入始终得到完全相同的结果，不修改输入。

### 错误

以下情况抛出异常（均公开于 `data_quality`），且**不返回部分结果**：

- `InvalidLinkedCorrelationInputError`（`ValueError` 子类，码 `INVALID_LINKED_CORRELATION_INPUT`）：`sample_links` 不是列表、链接或端点含缺失/多余键、标识为空、自链接（两端为同一样本）、重复关系（含左右反转的重复）。
- `UnknownLinkedCorrelationReferenceError`（`LookupError` 子类，码 `UNKNOWN_LINKED_CORRELATION_REFERENCE`）：链接端点在 `results` 中找不到相同 `dataset_id` + `sample_id` 的结果。
- `results` / `lineage` 仍抛出原有的 `InvalidCorrelationInputError` / `UnknownCorrelationReferenceError`。

校验顺序为血缘图结构 → 结果结构（含重复冲突）→ 结果引用解析 → sample_links 结构（含自链接/重复）→ 链接端点引用解析；结构错误先于未知引用错误。

## 异常来源证据分析

`analyze_violation_origins` 在规则校验、异常关联与字段血缘之上，为一批已确认的违规目标追溯同一样本内的来源证据（上游与自身违规）与影响证据（下游违规）。`results`、`lineage` 的结构、校验与去重语义完全沿用 `correlate_violations`，额外接收一份 `targets` 目标列表。

```python
from data_quality import analyze_violation_origins

result = analyze_violation_origins(results, lineage, targets)
```

- `results` / `lineage`：与 `correlate_violations` 完全相同。
- `targets`：目标对象列表，每个目标恰好含 `dataset_id`、`field_id`、`sample_id` 三个非空字符串键；目标按三键定位一条 `violated=true` 的结果，找不到（含仅有通过结果）抛出 `UnknownOriginTargetError`。
- 证据只取与目标**同一样本**的违规结果；其他样本、通过结果与无血缘关系的字段不参与。

### 来源与影响证据

- `originEvidence`：目标字段自身的违规，以及位于目标上游（可沿正向边到达目标）字段的同一样本违规；`path` 从证据字段正向延伸到目标字段，自身证据的路径只含目标字段一项。
- `impactEvidence`：从目标沿正向边可达的**非目标**字段上的同一样本违规；`path` 从目标字段正向延伸到证据字段；目标字段上的自环不会让目标成为自己的影响证据。
- 每条 `path` 均为 `{"dataset_id", "field_id"}` 项组成的最短（边数最少）正向链；同长时按完整字段序列字典序取最小。
- 证据项恰好含 `rule_id`、`dataset_id`、`field_id`、`sample_id`、`violated`、`value`、`path` 七键；同一字段上的多条违规规则各自成为一条证据。
- 证据按 `path` 长度、`dataset_id`、`field_id`、`rule_id` 排序；无证据时对应列表为空。

### 来源证据返回结果

```json
{
  "reports": [
    {
      "target": {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-1"},
      "originEvidence": [
        {
          "rule_id": "r2",
          "dataset_id": "dwd",
          "field_id": "label",
          "sample_id": "样本-1",
          "violated": true,
          "value": "bad",
          "path": [{"dataset_id": "dwd", "field_id": "label"}]
        }
      ],
      "impactEvidence": []
    }
  ]
}
```

- `reports` 与 `targets` 一一对应并保持其顺序；空 `targets` 返回 `{"reports": []}`。
- 相同输入始终得到完全相同的结果，不修改输入。

### 来源证据错误

以下情况抛出异常（均公开于 `data_quality`），且不返回部分结果：

- `InvalidOriginInputError`（`ValueError` 子类，码 `INVALID_ORIGIN_INPUT`）：`results`/`lineage`/`targets` 结构有误、标识为空、重复记录内容冲突、边非法或矛盾。
- `UnknownOriginReferenceError`（`LookupError` 子类，码 `UNKNOWN_ORIGIN_REFERENCE`）：校验结果引用了未声明的数据集或字段。
- `UnknownOriginTargetError`（`LookupError` 子类，码 `UNKNOWN_ORIGIN_TARGET`）：目标没有对应的 `violated=true` 结果。

校验顺序为血缘图结构 → 结果结构（含重复冲突）→ targets 结构 → 结果引用解析 → 逐目标违规定位。

## 字段级质量影响分析

`analyze_field_impacts` 在既有规则校验、异常定位与字段血缘之上，从一批种子字段出发，分析每个种子沿有向血缘边可达的下游字段，以及落在这些下游字段上的非通过规则与可定位异常样本。

```python
from data_quality import analyze_field_impacts

result = analyze_field_impacts(payload)
```

- `payload`：恰好包含五个键的 JSON 对象（键多余或缺失均抛错）：
  - `datasets`：对象，键为非空数据集 id，值为字段名（非空字符串）数组。
  - `lineageEdges`：有向边数组，每条恰好含 `sourceDataset`、`sourceField`、`targetDataset`、`targetField` 四个非空字符串键；方向由 source 指向 target。
  - `validationResults`：规则数组，每条恰好含 `ruleId`（非空字符串）、`status`（`passed`、`failed`、`skipped` 之一）、`fields`（`{"dataset", "field"}` 引用数组）、`failedSampleIds`（非空字符串数组）。
  - `anomalySamples`：按样本编号映射的对象，每个样本恰好含 `dataset`（非空字符串）与 `fieldValues`（字段名到任意 JSON 值的对象）。
  - `seedFields`：`{"dataset", "field"}` 引用数组；字段全名为 `dataset.field`。
- 种子沿有向边收集**至少经过一条边**可达的字段；自环以及回到种子的环也算可达，遇环正常结束；集合去重。
- 未知种子（引用了未声明的数据集/字段）不报错，视作空下游。
- 悬空边（任一端点未在 `datasets` 声明）不中断分析：遍历时跳过，并按 `(sourceDataset, sourceField, targetDataset, targetField)` 四键升序写入 `unresolvedReferences`。
- 重复的种子、规则字段、样本编号与边均合法；图中允许自环与循环。

### 影响分析返回结果

```json
{
  "status": "ok",
  "impacts": [
    {
      "seed": {"dataset": "ods", "field": "name"},
      "downstreamFields": [
        {"dataset": "ads", "field": "label"},
        {"dataset": "dwd", "field": "label"}
      ],
      "affectedRules": ["r1"],
      "anomalySamples": ["s1"],
      "paths": [
        [
          {"dataset": "ods", "field": "name"},
          {"dataset": "dwd", "field": "label"},
          {"dataset": "ads", "field": "label"}
        ],
        [
          {"dataset": "ods", "field": "name"},
          {"dataset": "dwd", "field": "label"}
        ]
      ]
    }
  ],
  "unresolvedReferences": []
}
```

- 顶层 `status` 恒为 `"ok"`；`impacts` 严格按 `seedFields` 的原始顺序逐项对应（重复种子产生重复项）。
- 每项含 `seed`、`downstreamFields`、`affectedRules`、`anomalySamples`、`paths`：
  - `downstreamFields`：去重后的下游字段引用，按字段全名 `dataset.field` 升序。
  - `affectedRules`：涉及任一下游字段且 `status` 非 `passed` 的规则 id，按 `ruleId` 升序。
  - `anomalySamples`：上述受影响规则的 `failedSampleIds` 中、能在 `anomalySamples` 里定位到的样本编号，去重并按编号升序。
  - `paths`：按下游字段全名升序，每项是种子到该字段的最短字段引用序列（含种子与目标）；等长时取字典序最小的序列。
- `seedFields` 为空时返回空 `impacts`，但悬空边仍照常写入 `unresolvedReferences`。
- 相同输入始终得到完全相同的结果；不修改输入，不产生部分返回。

### 影响分析错误

以下情况抛出 `ImpactInputError`（`ValueError` 子类，公开于 `data_quality`），不产生部分返回，也不新增其他业务错误：

- `payload` 不是对象、缺少或多出顶层键。
- `datasets`、`lineageEdges`、`validationResults`、`anomalySamples`、`seedFields` 或其中的字段/引用数组结构错误。
- 任一标识（数据集、字段、规则、样本编号、种子引用）为空或不是字符串。
- 规则 `status` 不属于 `passed`、`failed`、`skipped`。

## 命令行

### dq validate

从标准输入读取一个 UTF-8 JSON 对象，字段为 `records` 与 `rules`，并以 UTF-8 JSON 输出结果：

```bash
dq validate < payload.json
```

跨字段规则在同一载荷中可选传入 `dataset`、`composite_rules`、`record_refs`（提供 `composite_rules` 时 `dataset` 必填）：

```json
{
  "records": [{"id": "r1", "start": "2026-01-01", "end": "2026-02-01"}],
  "rules": [],
  "dataset": "dwd_orders",
  "composite_rules": [
    {"rule_id": "date-order", "fields": ["start", "end"], "severity": "error",
     "conditions": [{"type": "date_before", "earlier_field": "start", "later_field": "end"}]}
  ]
}
```

成功（无论是否有违反或跳过）退出码为 0；输入有误时退出码为 2，并向标准输出写入：

```json
{"error": {"code": "INVALID_RULE", "message": "..."}}
```

错误码：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_INPUT`：载荷不是 JSON 对象、缺少 `records`/`rules`、或 `records` 结构错误。
- `INVALID_RULE`：单字段规则定义非法（未知类型、重复 id、选项冲突、非法正则等）。
- `INVALID_RULE_SET`：跨字段规则集为空/结构不可用，或提供了 `composite_rules` 却缺少非空 `dataset`。
- `DUPLICATE_RULE_ID`：跨字段 `rule_id` 重复。
- `INVALID_SEVERITY`：跨字段规则严重级别未知。
- `UNSUPPORTED_COMPOSITE_CONDITION`：不支持的跨字段关联条件类型。
- `INVALID_COMPOSITE_RULE`：跨字段规则无法确定执行结果（字段不存在、条件引用自身、日期格式不合法等）。
- `INVALID_RECORD_REFERENCE`：`record_refs` 无法定位到记录。

### dq query-results

从标准输入读取一个 UTF-8 JSON 对象，必填 `results`（完整跨字段结果列表），并可选 `rule_id`、`severity`、`record_index`、`record_id` 四个筛选字段；以 UTF-8 JSON 输出 `{"count": n, "results": [...]}`，筛选结果保持稳定顺序：

```bash
dq query-results < results.json
```

输入有误时退出码为 2，错误码为 `INVALID_JSON` 或 `INVALID_INPUT`（缺少 `results`、筛选值类型错误等）。

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

### dq lineage-paths

从标准输入读取与 `dq lineage` 完全相同的 UTF-8 JSON 对象（`nodes`、`edges`、`target` 必填；`direction`、`max_depth` 可省略）：

```bash
dq lineage-paths < lineage.json
```

合法查询退出码为 0 并输出上述路径解释结果（含上游有序路径与下游影响范围）；目标存在但某方向无关系时仍为退出码 0、`paths` 为空数组。输入有误时退出码为 2，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_LINEAGE_INPUT`：`nodes`/`edges` 结构、端点或重复项有误。
- `INVALID_LINEAGE_QUERY`：`target`/`direction`/`max_depth` 非法。
- `LINEAGE_NODE_NOT_FOUND`：`target` 未在 `nodes` 中声明。

### dq field-lineage-paths

从标准输入读取与 `dq field-lineage` 完全相同的 UTF-8 JSON 对象（`fields`、`edges`、`target` 必填；`direction`、`max_depth` 可省略）：

```bash
dq field-lineage-paths < field-lineage.json
```

合法查询退出码为 0 并输出字段级路径解释结果；输入有误时退出码为 2，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_FIELD_LINEAGE_INPUT`：`fields`/`edges` 结构、端点或重复项有误。
- `INVALID_FIELD_LINEAGE_QUERY`：`target`/`direction`/`max_depth` 非法。
- `LINEAGE_NODE_NOT_FOUND`：`target` 未在 `fields` 中声明。

### dq correlate

从标准输入读取异常关联定位的 UTF-8 JSON 对象（`results`、`lineage` 均必填）：

```bash
dq correlate < correlate.json
```

合法输入退出码为 0 并输出上述关联事件结果；输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_CORRELATION_INPUT`：`results`/`lineage` 结构、标识、重复记录或边有误。
- `UNKNOWN_CORRELATION_REFERENCE`：校验结果引用了未声明的数据集或字段。

除标准输入与标准输出外，不写文件、不访问外部服务。

### dq correlate-links

从标准输入读取跨数据集关联定位的 UTF-8 JSON 对象（`results`、`lineage`、`sample_links` 均必填）：

```bash
dq correlate-links < correlate-links.json
```

其中 `results`、`lineage` 与 `dq correlate` 完全相同；`sample_links` 为无向关联关系列表，每条含 `left`、`right`，端点只含 `dataset_id`、`sample_id`：

```json
{
  "results": [
    {"rule_id": "r1", "dataset_id": "ods", "field_id": "name", "sample_id": "a", "violated": true, "value": "x"},
    {"rule_id": "r2", "dataset_id": "dwd", "field_id": "label", "sample_id": "b", "violated": true, "value": "y"}
  ],
  "lineage": {
    "datasets": ["ods", "dwd"],
    "fields": {"ods": ["name"], "dwd": ["label"]},
    "edges": [
      {"source": {"dataset": "dwd", "field": "label"},
       "target": {"dataset": "ods", "field": "name"}, "type": "upstream"}
    ]
  },
  "sample_links": [
    {"left": {"dataset_id": "ods", "sample_id": "a"},
     "right": {"dataset_id": "dwd", "sample_id": "b"}}
  ]
}
```

合法输入退出码为 0 并输出上述跨数据集关联事件（`sample_refs`、`rules`、`fields`、`upstream_fields`、`downstream_fields`）；输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_CORRELATION_INPUT`：`results`/`lineage` 结构、标识、重复记录或边有误。
- `UNKNOWN_CORRELATION_REFERENCE`：校验结果引用了未声明的数据集或字段。
- `INVALID_LINKED_CORRELATION_INPUT`：`sample_links` 结构、标识有误，或存在自链接、重复关系。
- `UNKNOWN_LINKED_CORRELATION_REFERENCE`：链接端点在 `results` 中找不到相同 `dataset_id` + `sample_id` 的结果。

除标准输入与标准输出外，不写文件、不访问外部服务。

### dq violation-origins

从标准输入读取异常来源证据分析的 UTF-8 JSON 对象（`results`、`lineage`、`targets` 均必填）：

```bash
dq violation-origins < origins.json
```

其中 `results`、`lineage` 与 `dq correlate` 完全相同；`targets` 为目标列表，每个目标恰好含 `dataset_id`、`field_id`、`sample_id` 三个非空字符串键：

```json
{
  "results": [
    {"rule_id": "r1", "dataset_id": "ods", "field_id": "name", "sample_id": "a", "violated": true, "value": "x"},
    {"rule_id": "r2", "dataset_id": "dwd", "field_id": "label", "sample_id": "a", "violated": true, "value": "y"}
  ],
  "lineage": {
    "datasets": ["ods", "dwd"],
    "fields": {"ods": ["name"], "dwd": ["label"]},
    "edges": [
      {"source": {"dataset": "dwd", "field": "label"},
       "target": {"dataset": "ods", "field": "name"}, "type": "upstream"}
    ]
  },
  "targets": [
    {"dataset_id": "dwd", "field_id": "label", "sample_id": "a"}
  ]
}
```

合法输入退出码为 0 并输出上述来源/影响证据报告（`reports`，与 `targets` 同序）；输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_ORIGIN_INPUT`：`results`/`lineage`/`targets` 结构、标识、重复记录或边有误。
- `UNKNOWN_ORIGIN_REFERENCE`：校验结果引用了未声明的数据集或字段。
- `UNKNOWN_ORIGIN_TARGET`：目标没有对应的 `violated=true` 结果。

除标准输入与标准输出外，不写文件、不访问外部服务。

## 测试

```bash
python -m unittest discover -s tests
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
