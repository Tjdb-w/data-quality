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
- 已实现：字段级质量影响分析（`analyze_field_impacts`，Python API；命令行不变）。
- 已实现：跨字段一致性规则（`register_composite_rules` / `evaluate_composite_rules` / `query_composite_results`，命令行 `dq composite-register` / `composite-evaluate` / `composite-query`；`dq validate` 支持一次校验同时运行单字段与跨字段规则）。

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

### 一次校验同时运行跨字段规则

`validate` 增加两个可选关键字参数 `dataset` 与 `composite_rules`（必须同时出现）：

```python
result = validate(records, rules, dataset=dataset, composite_rules=composite_rules)
```

- 单字段规则的 `passed` / `summary` / `violations` 与不提供跨字段规则时**完全一致**，跨字段结果只附加在新键 `composite` 下。
- 全部规则（单字段与跨字段）先统一注册：跨字段规则被拒绝时整次校验失败，不执行任何记录，也不会部分注册。

## 跨字段一致性规则

跨字段规则判断同一记录内多个字段是否共同满足约束，与单字段规则共享"注册 → 执行 → 结果查询"的使用方式。

```python
from data_quality import (
    register_composite_rules,
    evaluate_composite_rules,
    query_composite_results,
)

compiled = register_composite_rules(dataset, composite_rules)
output = evaluate_composite_rules(records, compiled)
selected = query_composite_results(output, {"rule_id": "dates",
                                            "severity": "error"})
```

### 规则注册

- `dataset`：`{"dataset_id": 非空字符串, "fields": ...}`。`fields` 可以是字段名（非空字符串）列表，也可以是字段名到类型字符串的映射（类型为 `date` / `datetime` 的字段才允许参与日期先后比较）；字段顺序被保留。
- 规则为非空列表，每条恰好含四个键：
  - `rule_id`：非空字符串，批次内唯一、稳定，不出现在结果之外的任何重命名中。
  - `fields`：非空、互不重复的目标字段集合，必须已在数据集声明，且**恰好**是其条件引用的全部字段。
  - `severity`：严重级别，只能是 `error`、`warning`、`info`。
  - `conditions`：非空关联条件列表，多条件按**与（AND）**组合。
- 关联条件至少覆盖四类：

| 条件 type | 字段 | 含义 |
| --- | --- | --- |
| `field_equals` | `left`、`right` | 两个字段按 JSON 相等必须相等 |
| `field_not_equals` | `left`、`right` | 两个字段必须不相等 |
| `date_order` | `left`、`right`、可选 `format` | `left` 日期必须不晚于 `right`（相等合法） |
| `required_if` | `when`、`field` | `when` 存在且非 `null` 时，`field` 必须存在且非 `null` |

- 比较类条件的 `left`/`right` 必须是两个不同的已声明字段（禁止引用自身）；`required_if` 的 `when` 与 `field` 也不得相同。
- `date_order` 默认按 ISO `YYYY-MM-DD` 或 ISO 8601 日期时间比较；提供 `format` 时按 strptime 解析（如 `"%d/%m/%Y"`）。日期字段类型不是 `date`/`datetime`、或格式含无法识别的指令（如 `%Q`、悬空 `%`）都在注册阶段拒绝。
- 注册是原子的：任一规则无法确定执行结果时整批拒绝，不部分注册。

### 执行

`evaluate_composite_rules(records, compiled)` 按注册顺序逐条规则、再按记录顺序评估，每条规则对每条记录产生**一条完整结果**：

- `PASSED`：关联字段齐全且全部条件成立；
- `FAILED`：关联字段齐全但条件不全成立（含日期值无法解析，坏数据不会被静默放过）；
- `SKIPPED_MISSING_FIELD`：记录缺少该规则引用的任一字段。既不算通过也不算失败，不阻断其他规则继续执行。

同一记录违反多条规则时保留全部结果，按规则注册顺序、记录顺序稳定输出。

完整结果含数据集标识、记录定位信息、规则与关联字段、结论、严重级别和可供下游查询的上下文：

```json
{
  "dataset_id": "ds",
  "results": [
    {
      "dataset_id": "ds",
      "record_index": 1,
      "record_id": "s2",
      "rule_id": "dates",
      "fields": ["start", "end"],
      "conclusion": "FAILED",
      "severity": "error",
      "context": {
        "conditions": [
          {"type": "date_order", "fields": ["start", "end"], "satisfied": false}
        ],
        "missing_fields": [],
        "values": {"start": "2024-05-01", "end": "2024-01-01"}
      }
    }
  ],
  "summary": {
    "dataset_id": "ds",
    "record_count": 2,
    "rule_count": 1,
    "passed_count": 1,
    "failed_count": 1,
    "skipped_count": 0
  }
}
```

- `record_id` 取记录的 `id` 字段，缺失时为 `null`；`record_index` 从 0 开始。跳过结果的 `context.missing_fields` 列出缺失字段、`values` 为 `null`。

### 结果查询

`query_composite_results(results, filters)` 在不改变既有单字段规则历史查询口径的前提下筛选跨字段结果：

- 可按 `rule_id`（字符串或字符串列表）、`severity`（同上）、记录定位 `record_index`（非负整数）或 `record_id`（字符串）筛选；条件之间为与。
- 筛选只做子集过滤，继续遵守注册顺序/记录顺序的稳定输出。
- `results` 可传执行返回的整个字典，也可传裸结果列表。

### 异常样本定位与血缘沿用

`composite_impact_inputs(results)` 把 FAILED 结果适配为 `analyze_field_impacts` 直接消费的 `validationResults` 与 `anomalySamples` 片段：

- 每条失败规则成为一个 `status: "failed"`、`fields` 为全部参与字段（`{"dataset", "field"}`）的规则项；每条失败记录成为一个异常样本，样本编号就是记录定位（有 `id` 用 `id`，否则为 `record:<index>`，另有 `composite_sample_id` 辅助函数）。
- 因此从一条跨字段异常跳回时，看到的记录与参与字段和校验结果完全一致；把这些片段并入既有影响分析载荷后，如果参与字段已是血缘节点，现有的上下游追溯语义（下游字段、受影响规则、异常样本、路径）原样适用，无需改动任何血缘入口。

### 跨字段规则错误

注册阶段抛出 `CompositeRuleError`（`ValueError` 子类，含可定位的 `rule_id` 与 `field` 属性），错误码：

- `INVALID_RULE_SET`：规则集为空或不是列表。
- `DUPLICATE_RULE_ID`：`rule_id` 重复。
- `INVALID_SEVERITY`：严重级别未知。
- `UNSUPPORTED_COMPOSITE_CONDITION`：出现不支持的关联条件类型。
- `INVALID_COMPOSITE_RULE`：其他无法确定执行结果的定义——字段不存在、条件引用自身、目标字段与条件引用字段不一致、日期字段类型/格式不合法等；错误对象带 `rule_id` 与可定位的 `field`。

执行/查询阶段抛出 `InvalidRecordReferenceError`（`LookupError` 子类，错误码 `INVALID_RECORD_REFERENCE`）：记录不是 JSON 对象列表、无法定位记录、编译规则与数据集不匹配，或查询筛选参数非法。

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

载荷可选地同时包含 `dataset` 与 `composite_rules`，使一次校验同时运行单字段与跨字段规则；单字段结果结构不变，跨字段结果附加在 `composite` 键下。两者必须同时出现。

成功（无论是否有违反）退出码为 0；输入有误时退出码为 2，并向标准输出写入：

```json
{"error": {"code": "INVALID_RULE", "message": "..."}}
```

错误码：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_INPUT`：载荷不是 JSON 对象、缺少 `records`/`rules`、或 `records` 结构错误。
- `INVALID_RULE`：规则定义非法（未知类型、重复 id、选项冲突、非法正则等）。
- `INVALID_RULE_SET` / `DUPLICATE_RULE_ID` / `INVALID_SEVERITY` / `UNSUPPORTED_COMPOSITE_CONDITION` / `INVALID_COMPOSITE_RULE`：载荷含 `composite_rules` 时的跨字段规则注册错误（见上文）。
- `INVALID_RECORD_REFERENCE`：跨字段执行无法定位记录。

### dq composite-register / composite-evaluate / composite-query

三个子命令分别对应跨字段规则的注册、执行与结果查询，均从标准输入读取 UTF-8 JSON：

```bash
dq composite-register  < register.json   # {"dataset": ..., "rules": [...]} -> {"compiled_rules": [...]}
dq composite-evaluate  < evaluate.json   # {"records": [...], "compiled_rules": [...]}
dq composite-query     < query.json      # {"results": ..., "filters": {...}}
```

- `composite-evaluate` 也接受 `{"records": [...], "dataset": ..., "rules": [...]}`，在单次调用内先注册再执行；可选 `dataset_id` 用于校验记录所属数据集与规则注册数据集一致。
- `composite-query` 的 `filters` 可省略，支持 `rule_id`、`severity`（字符串或字符串数组）、`record_index`、`record_id`，输出 `{"results": [...]}` 且保持稳定顺序。

合法输入退出码为 0；输入有误时退出码为 2，错误码为 `INVALID_JSON`、`INVALID_RULE_SET`、`DUPLICATE_RULE_ID`、`INVALID_SEVERITY`、`UNSUPPORTED_COMPOSITE_CONDITION`、`INVALID_COMPOSITE_RULE` 或 `INVALID_RECORD_REFERENCE`，错误体同样为 `{"error": {"code", "message"}}`。

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

## 测试

```bash
python -m unittest discover -s tests
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
