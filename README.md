# Data Quality

数据质量校验与血缘引擎：规则校验、异常样本定位、上下游血缘追溯。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：独立的数据质量规则校验（Python 包 `data_quality` 与命令行 `dq validate`）。
- 已实现：上下游血缘追溯（`trace_lineage` 与命令行 `dq lineage`）。
- 已实现：字段级上下游血缘追溯（`trace_field_lineage` 与命令行 `dq field-lineage`）。
- 已实现：样本级上下游血缘追溯（`trace_sample_lineage` 与命令行 `dq sample-lineage`）：在字段边与无向样本关联上按样本见证关系追溯样本，不修改记录、不落盘、不访问服务。
- 已实现：血缘路径解释与影响范围查询（`explain_lineage_paths` / `explain_field_lineage_paths` 与命令行 `dq lineage-paths` / `dq field-lineage-paths`）。
- 已实现：异常样本跨规则关联定位（`correlate_violations` 与命令行 `dq correlate`）。
- 已实现：跨数据集关联样本的异常关联定位（`correlate_linked_violations` 与命令行 `dq correlate-links`）。
- 已实现：异常来源证据分析（`analyze_violation_origins` 与命令行 `dq violation-origins`）。
- 已实现：跨样本异常来源归因（`analyze_linked_origins` 与命令行 `dq linked-origins`）：在无向样本关联上跨样本解释目标异常，证据同时给出最短无向 `sample_path` 与正向字段血缘 `path`，只做内存计算，不改记录、不落盘、不访问服务。
- 已实现：字段级质量影响分析（`analyze_field_impacts` 与命令行 `dq field-impact`）。
- 已实现：变更影响分析（`analyze_change_impact` 与命令行 `dq change-impact`）：对入口数据集的删除、重命名、类型变更，沿已登记的有向血缘边计算直接或间接下游数据集、可证明的受影响字段、字段来源与稳定最短路径，只做内存计算，不改数据、规则或血缘元数据、不落盘、不访问服务。
- 已实现：两次快照的异常漂移对比（`compare_quality_snapshots` 与命令行 `dq snapshot-diff`）。
- 已实现：两份字段血缘快照的比较（`compare_lineage_snapshots` 与命令行 `dq lineage-diff`）：比较两份内存中的字段血缘快照，并为目标字段给出上下游可达字段与诱导边差异，不修改记录、不落盘、不访问服务。
- 已实现：跨数据集引用完整性校验（`validate_references` 与命令行 `dq reference-integrity`）。
- 已实现：数据集级质量门槛（`evaluate_quality_gates` 与命令行 `dq quality-gates`）：以单字段规则为基础，按门槛汇总失败记录比例并给出异常样本。
- 已实现：批次跨记录校验（`evaluate_batch_rules` 与命令行 `dq batch-validate`）：在同一批对象记录上执行复合唯一键（`unique_key`）与分组比例（`group_ratio`）规则，返回同一内存报告，不改记录、不落盘、不访问服务。
- 已实现：单字段异常人工豁免（`apply_violation_exemptions` 与命令行 `dq exemptions`）：将 `validate` 结果中的异常按豁免精确拆分为未豁免与已豁免，不落盘、不改变既有规则与结果；质量门槛可选用同一套豁免规则。
- 已实现：跨字段一致性规则（`register_composite_rules` / `evaluate_composite_rules` / `query_composite_results`，并经 `validate` 与命令行 `dq validate` / `dq query-results` 使用）。
- 已实现：记录画像与规则候选（`profile_records` 与命令行 `dq profile`）：与 `validate` 同输入，只做内存统计并生成 validate 形态的候选规则对象，不改记录、不落盘、不访问服务。

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

## 记录画像与规则候选

`profile_records` 与 `validate` 使用相同的记录输入，但不执行任何规则：它对字段做内存统计（画像），并据画像生成 validate 单字段规则形态的候选规则对象。整个过程只读输入，不修改记录、不落盘、不访问外部服务。

```python
from data_quality import profile_records

result = profile_records(records)            # fields 可省略
result = profile_records(records, ["name", "age"])
```

- `records`：JSON 对象（dict）组成的列表，与 `validate` 相同。
- `fields`：可选，字段名列表；元素必须是互不重复的非空字符串，画像严格按给定顺序输出。省略时按记录顶层键的**首现序**统计；只统计顶层键（即使显式给出也不深入嵌套结构）。

### 画像返回结果

```json
{
  "record_count": 2,
  "fields": [
    {
      "field": "age",
      "present_count": 2,
      "missing_count": 0,
      "null_count": 0,
      "non_null_count": 2,
      "distinct_non_null_count": 2,
      "type_counts": {"null": 0, "boolean": 0, "number": 2, "string": 0, "array": 0, "object": 0},
      "numeric_min": 3,
      "numeric_max": 42
    }
  ],
  "rule_candidates": [
    {"id": "profile:age:required", "type": "required", "options": {"field": "age"}},
    {"id": "profile:age:unique", "type": "unique", "options": {"field": "age"}},
    {"id": "profile:age:range", "type": "range", "options": {"field": "age", "min": 3, "max": 42}},
    {"id": "profile:age:allowed_values", "type": "allowed_values", "options": {"field": "age", "values": [3, 42]}}
  ]
}
```

每个字段画像含 `field`、`present_count`（字段存在的记录数，含 null）、`missing_count`（字段缺失的记录数）、`null_count`（显式为 `null` 的记录数）、`non_null_count`（`present_count - null_count`）、`distinct_non_null_count`（非 null 去重值个数）、`type_counts`、`numeric_min`、`numeric_max`。

- `type_counts` 固定按 `null`、`boolean`、`number`、`string`、`array`、`object` 六键给出；布尔值计入 `boolean`，不计入 `number`。
- `numeric_min`、`numeric_max` 仅当非 null 值全部为非布尔数字且至少一个时取最小值、最大值；否则为 `null`。
- 去重沿用 validate 的 JSON 相等语义（结构化比较，布尔不与数字相等）。

### 规则候选

`rule_candidates` 为 validate 单字段规则对象（`id`、`type`、`options`），按画像字段顺序分组，组内依次尝试 `required`、`unique`、`range`、`allowed_values`，仅在条件必然满足时生成，规则 id 形如 `profile:字段名:类型`：

- `required`：至少一条记录，且每条记录该字段均存在且非 `null`。
- `unique`：至少两个非 null 值，且两两按 JSON 相等语义不同（`null` 不参与）。
- `range`：无缺失、无 null，且全部为非布尔数字，`min`/`max` 取画像的最小、最大值。
- `allowed_values`：无缺失，且去重取值（含 `null`，按首现序）不超过 20 个；`values` 为首现序去重值列表。

`records` 为空时 `record_count` 为 0；未给 `fields` 时 `fields` 与 `rule_candidates` 均为空数组；显式给了 `fields` 时每个字段都得到全零画像（`type_counts` 六键为 0、`numeric_min`/`numeric_max` 为 `null`）。

### 画像错误

- `InvalidProfileInputError`（`ValueError` 子类，公开于 `data_quality`，码 `INVALID_PROFILE_INPUT`）：`records` 不是列表或元素不是 JSON 对象，或 `fields` 不是列表、元素不是非空字符串或存在重复。出错时不返回部分画像。

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

## 样本级血缘追溯

`samples` 声明已知样本，`fields`、`edges` 沿用字段级血缘语义（边由 `source` 字段指向 `target` 字段，两端均在 `fields` 声明），每条字段边额外携带 `witnesses`；`sample_links` 为样本间的无向关联。样本之间每跨过一条关系（一条被见证的字段边或一条样本关联）深度加 1。

```python
from data_quality import trace_sample_lineage

result = trace_sample_lineage(samples, fields, edges, sample_links,
                              target, direction="both", max_depth=None)
```

- `samples`：样本对象列表，每条恰好含 `dataset_id`、`sample_id` 两个非空字符串键；样本不可重复。
- `fields`：与字段级血缘一致：对象，键为非空表 id，值为互不重复的非空字段 id 列表。
- `edges`：恰好含 `source`、`target`、`witnesses` 三键的对象列表。`source`/`target` 为只含 `table`、`column` 且已声明的字段，边由 source 指向 target，重复边报错，自环与环路合法。`witnesses` 为互不重复的见证列表，每条恰好含 `dataset_id`、`sample_id`、`table`、`column` 四个非空字符串键；其样本必须在 `samples` 声明、字段必须在 `fields` 声明，且 `(table, column)` 必须**匹配该边的 source 或 target 字段对**。
- `sample_links`：无向关联列表，每条恰好含 `left`、`right`，两端均为只含 `dataset_id`、`sample_id` 的对象；自链接、重复关系（含左右反转）报错，两端都必须在 `samples` 中声明。
- `target`：追溯起点样本，只含 `dataset_id`、`sample_id` 两个键。
- `direction`：`upstream`（沿字段边反向：target 字段见证样本 → source 字段见证样本；`sample_links` 两个方向都可跨）、`downstream`（沿字段边正向：source → target；`sample_links` 两个方向都可跨）或 `both`（默认）。
- `max_depth`：`null`（默认，不限深度）或大于等于 0 的整数；布尔值不算整数；`0` 时只含 `target`。

### 样本级血缘返回结果

```json
{
  "target": {"dataset_id": "dwd", "sample_id": "b"},
  "direction": "both",
  "max_depth": null,
  "upstream": {
    "samples": [
      {"dataset_id": "dwd", "sample_id": "b", "depth": 0},
      {"dataset_id": "ods", "sample_id": "a", "depth": 1}
    ],
    "edges": [
      {
        "source": {"table": "ods", "column": "name"},
        "target": {"table": "dwd", "column": "label"},
        "witnesses": [
          {"dataset_id": "dwd", "sample_id": "b", "table": "dwd", "column": "label"},
          {"dataset_id": "ods", "sample_id": "a", "table": "ods", "column": "name"}
        ]
      }
    ]
  },
  "downstream": {
    "samples": [
      {"dataset_id": "dwd", "sample_id": "b", "depth": 0},
      {"dataset_id": "ads", "sample_id": "c", "depth": 1}
    ],
    "edges": [
      {
        "source": {"table": "dwd", "column": "label"},
        "target": {"table": "ads", "column": "label"},
        "witnesses": [
          {"dataset_id": "ads", "sample_id": "c", "table": "ads", "column": "label"},
          {"dataset_id": "dwd", "sample_id": "b", "table": "dwd", "column": "label"}
        ]
      }
    ]
  }
}
```

- 结果回显 `target`、`direction`、`max_depth`，并含 `upstream`、`downstream` 两侧，各含 `samples`、`edges`；只查一侧时另一侧为 `{"samples": [], "edges": []}`。
- 样本为 `{"dataset_id", "sample_id", "depth"}`：`target` 深度为 0，同一 BFS 中首次到达为准（最短深度唯一出现），按 `(depth, dataset_id, sample_id)` 排序。
- 边为该侧两端样本均到达的**全部字段边**：即至少一个已到达样本见证 source 字段、至少一个已到达样本见证 target 字段；边按见证样本引用（再按 source/target 字段引用）排序。
- 每条边保留原始 `source`、`target` 字段引用，`witnesses` 只列匹配该边 `(table, column)` 对的见证（本实现中所有合法见证均匹配），按字段引用（`table`、`column`）再按样本引用（`dataset_id`、`sample_id`）排序。
- 目标与任何关系都不通时，两侧均只含 `target`、`edges` 为空，仍为成功结果。

### 样本级血缘错误

- `InvalidSampleLineageInputError`（`ValueError` 子类）：`samples`/`fields`/`edges`/`sample_links` 结构有误、标识为空、样本/字段/见证/边/关联重复、边端点或见证引用未声明、见证 `(table, column)` 不匹配所在边的 source/target、自链接。
- `InvalidSampleLineageQueryError`（`ValueError` 子类）：`target` 不是只含 `dataset_id`、`sample_id` 的非空字符串对象、`direction` 非法、`max_depth` 不是 `null` 或非负非布尔整数。
- `UnknownSampleLineageTargetError`（`LookupError` 子类）：`target` 未在 `samples` 中声明。
- `UnknownSampleLineageReferenceError`（`LookupError` 子类）：`sample_links` 端点未在 `samples` 中声明。

校验顺序为输入结构（samples → fields → edges → sample_links 形状与重复）→ 查询参数（direction、max_depth、target 形状）→ target 声明性 → sample_links 端点引用；结构错误先于未知引用错误，未知 target 先于未知关联端点。

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

## 跨样本异常来源归因

`analyze_linked_origins` 是 `analyze_violation_origins` 的跨样本版本：在同一份检查 `results` 与字段 `lineage` 之外，额外接收一份无向 `sample_links`（结构与 `correlate_linked_violations` 完全相同），沿无向样本关联跨样本解释每个目标异常的来源证据与影响证据。

```python
from data_quality import analyze_linked_origins

result = analyze_linked_origins(results, lineage, sample_links, targets)
```

- `results` / `lineage`：与 `correlate_violations` 完全相同（结构、标识、去重与矛盾边校验）。
- `sample_links`：与 `correlate_linked_violations` 完全相同的无向关联列表，每条含 `left`、`right`，端点只含 `dataset_id`、`sample_id`；不允许自链接与重复关系，每个端点必须匹配一条结果。
- `targets`：与 `analyze_violation_origins` 完全相同，每项恰好含 `dataset_id`、`field_id`、`sample_id` 三个非空字符串键；保持原序与重复项，每项目标必须匹配一条 `violated=true` 的结果。

### 证据、样本路径与字段路径

只考虑与目标样本处于同一个 `sample_links` 无向连通分量内的样本（目标样本自身距离为 0）：

- `originEvidence`：证据字段为目标字段**自身或其上游**（证据字段可沿字段血缘正向边到达目标字段）的违规结果；目标样本上的目标结果必然是自身来源证据。
- `impactEvidence`：证据字段为从目标字段沿正向边可达的**其他字段**的违规结果。同样本同字段不进入 `impactEvidence`；字段自环不产生自我影响。
- 每条证据保留检查结果的全部键（`rule_id`、`dataset_id`、`field_id`、`sample_id`、`violated`、`value`），并新增两个路径：
  - `sample_path`：目标样本引用（`{"dataset_id", "sample_id"}`）到证据样本引用的最短**无向**路径，端点均含；目标样本自身为只含一项的路径。
  - `path`：按字段血缘正向书写的字段链（`{"dataset_id", "field_id"}` 项）；来源证据从证据字段到目标字段，影响证据从目标字段到证据字段。
- 两类路径都取边数最少者；同长时按完整引用序列字典序取最小。
- 证据按 `sample_path` 长度、`path` 长度、`dataset_id`、`field_id`、`rule_id`、`sample_id` 排序；无证据时对应列表为空。

### 返回结果

```json
{
  "reports": [
    {
      "target": {"dataset_id": "dwd", "field_id": "label", "sample_id": "b"},
      "originEvidence": [
        {
          "rule_id": "r1",
          "dataset_id": "ods",
          "field_id": "name",
          "sample_id": "a",
          "violated": true,
          "value": "x",
          "sample_path": [
            {"dataset_id": "dwd", "sample_id": "b"},
            {"dataset_id": "ods", "sample_id": "a"}
          ],
          "path": [
            {"dataset_id": "ods", "field_id": "name"},
            {"dataset_id": "dwd", "field_id": "label"}
          ]
        }
      ],
      "impactEvidence": []
    }
  ]
}
```

- `reports` 与 `targets` 一一对应并保持其顺序（含重复项）；空 `targets` 返回 `{"reports": []}`。
- 相同输入始终得到完全相同的结果，不修改输入；仅在内存计算，不改记录、不落盘、不访问服务。

### 错误

以下情况抛出异常（均公开于 `data_quality`），且不返回部分结果：

- `LinkedOriginInputError`（`ValueError` 子类，码 `INVALID_INPUT`）：`results`/`lineage`/`sample_links`/`targets` 结构有误、标识为空、重复记录内容冲突、边非法或矛盾、自链接或重复关联。
- `LinkedOriginReferenceError`（`LookupError` 子类，码 `UNKNOWN_REFERENCE`）：校验结果引用了未声明的数据集或字段，或 `sample_links` 端点匹配不到结果。
- `LinkedOriginTargetError`（`LookupError` 子类，码 `UNKNOWN_TARGET`）：目标没有对应的 `violated=true` 结果。

校验顺序为血缘图结构 → 结果结构（含重复冲突）→ targets 结构 → 结果引用解析 → sample_links 结构与端点解析 → 逐目标违规定位。

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

命令行 `dq field-impact` 下，`ImpactInputError` 统一映射为错误码 `INVALID_IMPACT_INPUT`（退出码 2）；无法解析为 UTF-8 JSON 时仍为 `INVALID_JSON`，不返回部分结果。

## 变更影响分析

`analyze_change_impact` 是独立于规则校验与血缘查询的新功能：调用方提交**入口数据集标识、变更类型和变更字段**，功能沿已登记的有向血缘边找到直接或间接下游，返回入口变更摘要、受影响数据集、每个数据集的受影响字段、字段来源与入口到该数据集的稳定最短路径。整个过程只计算潜在下游影响，**不修改数据、规则或血缘元数据，不落盘、不访问服务**；重复调用或并发请求都得到相同顺序与一致结果。

```python
from data_quality import analyze_change_impact

result = analyze_change_impact(payload)
```

- `payload`：删除与重命名时恰好含 `dataset`、`changeType`、`fields`、`metadata` 四个键；类型变更时再含 `newType` 共五个键（键多余或缺失均抛错）。
  - `dataset`：入口数据集标识，非空字符串，区分大小写。
  - `changeType`：只接受 `"delete"`（删除）、`"rename"`（重命名）、`"type_change"`（类型变更）。
  - `fields`：删除与类型变更时为**非空**的字段名（非空字符串）数组，数组内不可重复；重命名时为恰好含 `oldField`、`newField` 两个非空字符串键的单个对象。
  - `newType`：仅类型变更出现；新的原始类型值（任意 JSON 值），与元数据中的原始类型按 JSON 结构相等比较（布尔不与数字相等）。
  - `metadata`：恰好含 `datasets`、`edges` 两个键的已登记元数据。
    - `datasets`：对象，键为非空数据集 id，值为字段声明数组；每个声明恰好含 `name`（非空字段名，同一数据集内不可重复）与 `type`（类型值，**原样保留**，可以是任意 JSON 值）。
    - `edges`：有向边数组，边由 `source` 指向 `target`；每个端点要么是数据集级 `{"dataset"}`，要么是列级 `{"dataset", "field"}`，同一条边两端粒度必须一致，端点必须已在 `datasets` 声明；列端点的字段必须已声明。重复边报错；自环与环路合法。
- 删除表示入口字段消失；重命名表示旧字段迁移到新字段（旧字段的下游与新字段已登记的下游合并考虑，但输出仍是**一条**变更摘要）；类型变更表示同一字段语义类型改变，只比较原始类型，类型值相同（结构相等）的字段不产生影响。
- 只沿**确认方向**的依赖边向下游走：无法证明方向的关系不会把上游误报为受影响对象。遍历对自环与环路安全（可达均指至少跨过一条边，同一条路径不重复节点），不会死循环。
- 字段影响只采信**可证明的列级依赖**：只能沿列级边到达的字段才列为受影响字段；仅有数据集级依赖可达时，受影响字段为空并标记 `unknown: true`，绝不按字段名猜测。数据集图的边为显式数据集级边，加上**源列可由变更字段沿列级边证明到达**的列级边在两端数据集上的投影（源列与变更无关的列级边不投影，不会把无关数据集报为下游）。
- 一个下游字段依赖多个入口字段时，各入口字段分别保留为独立来源。

### 变更影响返回结果

```json
{
  "status": "ok",
  "change": {
    "dataset": "ods",
    "changeType": "delete",
    "fields": ["name"]
  },
  "affectedDatasets": [
    {
      "dataset": "dwd",
      "distance": 1,
      "unknown": false,
      "fields": [
        {
          "field": "label",
          "sources": [
            {
              "dataset": "ods",
              "field": "name",
              "path": [
                {"dataset": "ods", "field": "name"},
                {"dataset": "dwd", "field": "label"}
              ]
            }
          ]
        }
      ],
      "path": [{"dataset": "ods"}, {"dataset": "dwd"}]
    },
    {
      "dataset": "rpt",
      "distance": 2,
      "unknown": true,
      "fields": [],
      "path": [{"dataset": "ods"}, {"dataset": "dwd"}, {"dataset": "rpt"}]
    }
  ]
}
```

- 顶层 `status` 恒为 `"ok"`；`change` 为入口变更摘要：删除/类型变更含 `dataset`、`changeType`、`fields`（类型变更只含实际改变了类型的字段，并另含原始 `newType`），重命名含 `dataset`、`changeType`、`oldField`、`newField`。
- `affectedDatasets` 为沿已登记边跨至少一条边可达的全部下游数据集，按血缘距离升序、同距离按数据集标识升序排列；**没有下游时为空列表，但入口摘要照常返回**。
- 每个受影响数据集恰好含 `dataset`、`distance`、`unknown`、`fields`、`path`：
  - `distance`：从入口数据集到该数据集的最短边数（沿显式数据集级边与源列已证明可达的列边投影构成的数据集图）。
  - `path`：入口数据集到该数据集的稳定最短数据集序列（两端都含，项为 `{"dataset"}`）；等长等价路径取节点标识字典序最小者。
  - `unknown`：该数据集只能由数据集级依赖证明可达时为 `true`，此时 `fields` 为空数组；存在可证明的列级命中字段时为 `false`。
  - `fields`：实际命中的受影响字段，按字段名升序；每项含 `field` 与 `sources`。
  - `sources`：可证明到达该字段的每个入口字段各占一项，按 `(dataset, field)` 升序；每项含入口字段引用 `dataset`、`field` 与列级 `path`（入口字段到该字段的最短字段引用序列，两端都含、至少一条边，项为 `{"dataset", "field"}`；等长取字典序最小序列）。
- 重命名摘要只有一条；旧字段删除影响与新字段依赖影响在同一结果中合并，下游字段的 `sources` 会分别保留 `oldField` 与 `newField` 两个来源。

### 变更影响错误

以下情况抛出 `ChangeImpactError` 子类（均为 `ValueError` 子类，公开于 `data_quality`，携带稳定 `code`），**不返回部分结果**，也不改用其他异常：

| 异常 | 错误码 | 触发情形 |
| --- | --- | --- |
| `ChangeImpactInputError` | `INVALID_CHANGE_IMPACT_INPUT` | 请求或 `metadata` 不是对象、顶层键缺失/多余、`changeType` 非法、标识为空或非字符串、字段声明或边结构非法（含端点未声明、粒度混用、重复边、重复字段声明）等一切结构问题 |
| `DatasetNotFoundError` | `DATASET_NOT_FOUND` | 入口数据集未在 `metadata.datasets` 中登记 |
| `FieldNotFoundError` | `FIELD_NOT_FOUND` | 删除/类型变更的入口字段，或重命名的 `oldField` 未在入口数据集元数据中登记 |
| `InvalidRenameError` | `INVALID_RENAME` | 重命名前后字段同名 |
| `DuplicateFieldError` | `DUPLICATE_FIELD` | 删除/类型变更的字段数组内出现重复字段 |
| `InvalidFieldsError` | `INVALID_FIELDS` | 删除/类型变更的字段集合为空 |

校验顺序为：结构形状（含 `changeType`、顶层键集合与 `metadata` 图结构）→ 字段集合为空 → 重命名同名 → 字段重复 → 入口数据集登记性 → 入口字段登记性。重命名的 `newField` 未登记不报错（视作新字段尚无已登记下游）；类型变更中与原始类型相同的字段不报错、不产生影响。

## 两次快照的异常漂移对比

`compare_quality_snapshots` 在既有校验结果与字段血缘之上，消费两次已经评估完成的快照（`baseline` 与 `current`），只比较其中 `violated=true` 的异常，不重跑任何规则、不修改输入，仅经返回值（命令行经标准输出）给出结果。

```python
from data_quality import compare_quality_snapshots

result = compare_quality_snapshots(payload)
```

- `payload`：恰好包含 `baseline`、`current`、`lineage` 三个顶层键（键多余或缺失均抛错）。
  - `baseline` / `current`：恰好含 `results` 一个键的对象；其中结果列表完全沿用 `correlate_violations` 的契约（六键、非空字符串标识、布尔 `violated`、任意 JSON `value`、同规则同样本重复且内容冲突时报错）。通过结果只参与引用校验，不参与漂移比较。
  - `lineage`：两侧共享的一张字段血缘图，结构与 `correlate_violations` 完全相同。
- 异常身份为 `(rule_id, dataset_id, field_id, sample_id)` 四元组：
  - `new`：仅 `current` 出现；
  - `resolved`：仅 `baseline` 出现；
  - `persisted`：两侧共有。
- 比较只解释已有结果，不产生任何文件或外部访问；相同输入始终得到完全相同的结果。

### 漂移对比返回结果

```json
{
  "status": "ok",
  "summary": {
    "baseline_violation_count": 1,
    "current_violation_count": 2,
    "new_count": 1,
    "resolved_count": 0,
    "persistent_count": 1
  },
  "changes": [
    {
      "state": "persisted",
      "rule_id": "r1",
      "dataset_id": "ods",
      "field_id": "name",
      "sample_id": "s1",
      "baseline_value": "x",
      "current_value": "y",
      "upstream_changes": [],
      "paths": []
    },
    {
      "state": "new",
      "rule_id": "r2",
      "dataset_id": "dwd",
      "field_id": "label",
      "sample_id": "s1",
      "baseline_value": null,
      "current_value": "bad",
      "upstream_changes": [
        {"rule_id": "r1", "dataset_id": "ods", "field_id": "name", "sample_id": "s1"}
      ],
      "paths": [
        [
          {"dataset_id": "ods", "field_id": "name"},
          {"dataset_id": "dwd", "field_id": "label"}
        ]
      ]
    }
  ]
}
```

- 顶层 `status` 恒为 `"ok"`；`changes` 按身份四元组字典序排列。
- 每个 change 恰好含 `state`、`rule_id`、`dataset_id`、`field_id`、`sample_id`、`baseline_value`、`current_value`、`upstream_changes`、`paths` 九键；缺失侧的 value 为 `null`，`persisted` 保留两侧原值（布尔不与数字按 JSON 相等判同）。
- `upstream_changes` 只含**同一 `sample_id`** 下、沿 lineage 正向边从证据字段可达目标字段（目标的上游）且**值发生变化**的其他 new / resolved / persisted 异常：new、resolved 只有一侧值，其出现或消失即为值变化；persisted 仅当两侧 value 按 JSON 相等不一致时计入。目标自身与仅经自环可达的同字段异常排除在外。
- `paths` 与 `upstream_changes` 严格同序，每项为证据字段到目标字段的最短字段序列（两端都含、至少一条边）；等长时取完整字段序列字典序最小者，路径项为 `{"dataset_id", "field_id"}`。
- 两侧均无违规时 `changes` 为空、各计数为 0。

### 漂移对比错误

以下情况抛出异常（均公开于 `data_quality`），不返回部分结果：

- `InvalidSnapshotInputError`（`ValueError` 子类，码 `INVALID_SNAPSHOT_INPUT`）：顶层不是对象或三键不齐、快照不是恰好含 `results` 的对象、两侧结果或血缘图结构非法（含标识为空、重复冲突、非法/矛盾边等，语义与 `correlate` 一致）。
- `UnknownSnapshotReferenceError`（`LookupError` 子类，码 `UNKNOWN_SNAPSHOT_REFERENCE`）：任一侧结果引用了血缘图未声明的数据集或字段。

校验顺序为血缘图结构 → 两侧结果结构（含重复冲突）→ 两侧引用解析；结构错误先于未知引用错误。

## 两份字段血缘快照的比较

`compare_lineage_snapshots` 在字段级血缘之上消费两份已经成型的血缘快照（`baseline` 与 `current`，各含 `fields`、`edges`，结构与 `trace_field_lineage` 的输入一致），外加一份非空 `targets` 目标字段列表，只在内存中比较，不重跑任何规则、不修改输入、不落盘、不访问服务。

```python
from data_quality import compare_lineage_snapshots

result = compare_lineage_snapshots(payload)
```

- `payload`：恰好包含 `baseline`、`current`、`targets` 三个顶层键（键多余或缺失均抛错）。
  - `baseline` / `current`：恰好含 `fields`、`edges` 两键的对象，各自独立按字段级血缘契约校验（表 id 非空、字段互不重复、边两端均为只含 `table`、`column` 的已声明字段、重复边拒绝；自环与环路合法）。同一字段只在一侧声明不是结构错误。
  - `targets`：非空数组，每项为恰好含 `table`、`column` 两个非空字符串键的字段对象，数组内不可重复。
- 可达方向沿用字段级血缘语义：上游沿边反向，下游沿边正向；目标自身总在本侧可达集合中；诱导边为可达字段集合两端均在内的全部原始边。
- 相同输入始终得到完全相同的结果，不依赖字典迭代顺序。

### 血缘快照比较返回结果

```json
{
  "status": "ok",
  "summary": {
    "added_field_count": 1,
    "removed_field_count": 0,
    "added_edge_count": 1,
    "removed_edge_count": 0,
    "changed_target_count": 1,
    "unchanged_target_count": 1
  },
  "changes": [
    {"type": "field_added", "field": {"table": "rpt", "column": "p"}},
    {"type": "edge_added", "edge": {"source": {"table": "ads", "column": "z"}, "target": {"table": "rpt", "column": "p"}}}
  ],
  "targets": [
    {
      "field": {"table": "ads", "column": "z"},
      "status": "changed",
      "upstream_delta": {"added_fields": [], "removed_fields": [], "added_edges": [], "removed_edges": []},
      "downstream_delta": {
        "added_fields": [{"table": "rpt", "column": "p"}],
        "removed_fields": [],
        "added_edges": [
          {"source": {"table": "ads", "column": "z"}, "target": {"table": "rpt", "column": "p"}}
        ],
        "removed_edges": []
      }
    }
  ]
}
```

- 顶层恰好含 `status`、`summary`、`changes`、`targets`，`status` 恒为 `"ok"`。
- `summary` 六键成对展开：`added_field_count`/`removed_field_count` 与 `added_edge_count`/`removed_edge_count` 为两份快照之间的整体差异计数，`changed_target_count`/`unchanged_target_count` 为双侧目标中 `changed` 与 `unchanged` 的计数（单侧 added/removed 目标计入 `changed_target_count`）。
- `changes` 为快照级差异，四类条目依次为 `field_added`、`edge_added`、`field_removed`、`edge_removed`；字段条目含 `field`（`{"table", "column"}`），边条目含 `edge`（`{"source", "target"}`，两端为字段对象）。同类内字段按 `(table, column)`、边按 `(source.table, source.column, target.table, target.column)` 排序。
- `targets` 与查询目标严格同序，每项含 `field`、`status`、`upstream_delta`、`downstream_delta`：
  - `status`：目标只在 `current` 为 `added`，只在 `baseline` 为 `removed`；双侧均有时，上游或下游任一方向的可达字段集合或诱导边集合不同即为 `changed`，否则 `unchanged`。
  - 单侧目标：缺失侧按空集合处理，因此其两个 delta 以该侧全部可达字段与诱导边作为 `added_*`（added 目标）或 `removed_*`（removed 目标）。
  - `upstream_delta`/`downstream_delta` 各含四集合：`added_fields`、`removed_fields`、`added_edges`、`removed_edges`，字段按 `(table, column)`、边按 `(source, target)` 排序。

### 血缘快照比较错误

以下情况抛出异常（均为 `ValueError` 子类，公开于 `data_quality`），不返回部分结果：

- `InvalidLineageSnapshotError`（码 `INVALID_LINEAGE_SNAPSHOT`）：顶层不是对象或三键不齐，或任一侧快照不是恰好含 `fields`、`edges` 的对象、图结构非法。
- `InvalidLineageDiffQueryError`（码 `INVALID_LINEAGE_DIFF_QUERY`）：`targets` 不是非空数组、目标形状不对、键缺失/多余、值不是非空字符串或目标重复。
- `UnknownLineageDiffTargetError`（码 `UNKNOWN_LINEAGE_DIFF_TARGET`）：目标在两份快照中均未声明（只在一侧声明合法）。

校验顺序为 JSON → 顶层与两份快照 → targets 形状 → 逐目标存在性；结构错误先于查询形状错误，查询形状错误先于未知目标错误。

## 数据集级质量门槛

数据集级门槛在既有单字段规则之上做比例汇总：规则定义与判定完全沿用 `validate`，不重新解释；门槛按 `source_rule_id` 引用一条已声明规则，汇总该规则的失败记录占比。

```python
from data_quality import evaluate_quality_gates

report = evaluate_quality_gates(dataset, records, rules, gates)
# 可选第五个参数 exemptions，契约与 apply_violation_exemptions 相同：
report = evaluate_quality_gates(dataset, records, rules, gates, exemptions)
```

- `dataset`：数据集标识，原样写入报告（可为 `null`）。
- `records` / `rules`：与 `validate` 的输入相同；规则非法抛 `InvalidRuleError`，`records` 结构非法抛 `InvalidInputError`。
- `gates`：门槛对象列表，按数组顺序汇总。每条门槛恰好含四键：
  - `rule_id`：门槛自身唯一、非空的字符串标识；
  - `source_rule_id`：非空字符串，必须引用 `rules` 中声明的规则 id；
  - `max_failed_ratio`：0 到 1 之间的 JSON 数字（布尔不是数字）；
  - `severity`：仅限 `error`、`warning`、`info`。
- `exemptions`：可选豁免列表（见下一节）。省略或为空列表时报告与基线逐字段相同；提供时豁免在全部单字段异常上统一按同一套规则校验与匹配。

### 汇总与判定

对每条门槛，按 `source_rule_id` 收集对应规则的违反项：

- `failed_count` 为未豁免违反项数，`record_count` 为记录数，`ratio = failed_count / record_count`；
- `ratio <= max_failed_ratio` 判为 `PASSED`，否则 `FAILED`（等于门槛值算通过）；
- `samples` 按记录顺序原样保留每个未豁免违反项的 `record_index`、`record_id`、`field`、`value`、`message`。

提供 `exemptions` 时，`failed_count`、`ratio`、`samples` 只统计未豁免异常；每条门槛另给 `waived_count` 与 `waived_samples`，后者保留原样例的五个键并附加 `exemption_id`、`reason`。省略或传入空列表时不输出这两个键，报告与基线完全相同。

`records` 为空时 `record_count` 为 0，状态为 `SKIPPED_EMPTY_DATASET`、`ratio` 为 `null`、`samples` 为空，不计成败（提供非空豁免时因无异常可匹配，按豁免错误处理）。

```json
{
  "dataset": "people",
  "results": [
    {
      "rule_id": "age-gate",
      "source_rule_id": "age-range",
      "status": "FAILED",
      "failed_count": 1,
      "record_count": 3,
      "ratio": 0.3333333333333333,
      "samples": [
        {"record_index": 0, "record_id": "r1", "field": "age", "value": 200, "message": "is out of the allowed range"}
      ],
      "waived_count": 0,
      "waived_samples": []
    }
  ]
}
```

### 门槛错误

以下情况抛出异常（均公开于 `data_quality`），不返回部分结果：

- `InvalidQualityGateRuleError`（`ValueError` 子类，码 `INVALID_QUALITY_GATE_RULE`）：`gates` 不是列表、门槛不是对象、键缺失或多余、`rule_id` 为空或重复、`source_rule_id` 为空或非字符串、`severity` 非法，或 `max_failed_ratio` 不是 0–1 的 JSON 数字（含布尔）。
- `UnknownQualityGateSourceError`（`LookupError` 子类，码 `UNKNOWN_QUALITY_GATE_SOURCE`）：`source_rule_id` 未在 `rules` 中声明。
- `InvalidExemptionError`（`ValueError` 子类，码 `INVALID_EXEMPTION_INPUT`）：豁免无效、重复、冲突或未匹配到异常。

校验顺序为规则定义 → 门槛结构 → `source_rule_id` 引用解析 → 记录结构 → 豁免校验与匹配；规则/门槛定义先于记录检查。

## 批次跨记录校验

单字段、跨字段与跨数据集门槛之外，批次校验覆盖同一批记录之间的规则。入口 `evaluate_batch_rules` 只在内存中完成：不修改记录、不落盘、不访问服务。

```python
from data_quality import evaluate_batch_rules

report = evaluate_batch_rules(records, rules)
```

- `records`：JSON 对象（dict）组成的列表。
- `rules`：规则对象列表，按数组顺序执行；规则先于记录整体校验，任一规则或记录非法即整体报错，不返回部分报告。
- 每条规则只含 `id`、`type`、`options` 三键；`id` 为非空且列表内唯一的字符串，`type` 仅限 `unique_key`、`group_ratio`。

### unique_key 复合唯一键

`options` 只含 `fields`：非空、元素互异的非空字段名数组。按字段顺序取键值并以 JSON 相等比较（布尔不等于数字，容器结构化比较，对象忽略键序）：

- 键值全部缺失或为 `null`：跳过，不参与完整性与判重；
- 部分缺失或为 `null`：当前记录报 `has an incomplete unique key`，且不参与判重；
- 键完整且与更早记录 JSON 相等：仅后出现的记录报 `is a duplicate unique key`。

### group_ratio 分组比例

`options` 只含四键：

- `group_fields`：非空、互异的非空分组字段名数组；
- `value_field`：非空字符串，取值字段；
- `allowed_values`：允许值数组（可为空，空数组表示无值命中）；
- `min_ratio`：闭区间 `[0, 1]` 内的 JSON 数字（布尔不是数字）。

记录按分组字段顺序的键值 JSON 相等地分组；任一分组字段缺失/为 `null`、或取值字段缺失/为 `null` 的记录不进入任何分组。组内取值与任一允许值 JSON 相等即命中，`ratio = matched_count / record_count`；`ratio < min_ratio` 时报该组一条异常，等于阈值通过。

### 报告结构

```json
{
  "passed": false,
  "summary": {
    "record_count": 4,
    "rule_count": 2,
    "checked_group_count": 1,
    "violation_count": 2
  },
  "violations": []
}
```

- `summary` 恰好四键：`record_count`、`rule_count`、`checked_group_count`（`unique_key` 不增组；`group_ratio` 按实际形成的组数累加）、`violation_count`。
- 两类异常共有 `rule_id`、`record_index`（从 0 起）、`record_id`（取记录的 `id`，缺失为 `null`）。
- `unique_key` 异常另含 `fields`（字段名数组，按声明序）、`value`（按 JSON 复现的键值数组，缺失位为 `null`）、`message`；同一规则内按记录顺序排列。
- `group_ratio` 异常另含 `group`、`record_count`、`matched_count`、`ratio`、`samples`，不含 `message`：
  - `group` 为 `{"field", "value"}` 数组，字段按声明序，组之间按固定字段顺序、键值 JSON 升序（`null`、布尔、数字、字符串、数组/对象）报告；
  - 共有定位键指向组内第一条记录（该组在记录序中的最早成员）；
  - `samples` 按记录序列出组内每条记录，每条含 `record_index`、`record_id`、`value`、`matched`（该记录是否命中允许值）。
- 空记录、空规则均不产生异常；空规则时 `passed` 为 `true`。

### 批次规则错误

以下异常均公开于 `data_quality`（`ValueError` 子类），不返回部分结果；校验顺序为先规则后记录：

- `InvalidBatchRuleError`（码 `INVALID_BATCH_RULE`）：`rules` 不是列表、规则不是对象、三键缺失或多余、`id` 为空/非字符串/重复、`type` 不受支持、`options` 不是对象，或两类 options 的键集合/字段数组/允许值/`min_ratio` 不合法。
- `InvalidBatchInputError`（码 `INVALID_BATCH_INPUT`）：`records` 不是列表或任一记录不是 JSON 对象。

## 单字段异常豁免

人工豁免只作用于 `validate` 产出的**单字段**异常：规则定义、执行顺序、异常消息与错误码完全不变，不落盘，`validate` 本身的输出不变，复合结果不含豁免处理。

```python
from data_quality import apply_violation_exemptions

report = apply_violation_exemptions(result, exemptions)
```

- `result`：`validate` 的返回结果（只读取其 `violations`；`composite_results` 不参与）。
- `exemptions`：豁免对象列表，每个对象恰好六键：
  - `exemption_id`：非空字符串，列表内唯一；
  - `rule_id`：字符串；
  - `record_index`：非负整数（布尔不算整数）；
  - `record_id`：字符串或 `null`；
  - `field`：字符串；
  - `reason`：非空字符串，作为豁免证据保留。
- 按 `rule_id`、`record_index`、`record_id`、`field` 四元组与异常**精确匹配**：`record_id` 为 `null` 时只匹配无 `id` 的异常，字符串 id 不会被 `null` 匹配。

### 拆分结果

```json
{
  "status": "ok",
  "summary": {
    "input_violation_count": 2,
    "active_violation_count": 1,
    "waived_violation_count": 1,
    "exemption_count": 1
  },
  "active_violations": [
    {"rule_id": "age-range", "record_index": 2, "record_id": "r3", "field": "age", "value": 999, "message": "is out of the allowed range"}
  ],
  "waived_violations": [
    {"rule_id": "age-range", "record_index": 0, "record_id": "r1", "field": "age", "value": 200, "message": "is out of the allowed range", "exemption_id": "e-legacy", "reason": "历史数据已知问题"}
  ]
}
```

- `status` 恒为 `"ok"`；当且仅当 `active_violations` 为空时视为通过（无未豁免异常）。
- `active_violations` 与 `waived_violations` 均按异常原始顺序保留；未豁免项为原异常对象，已豁免项为副本并附加 `exemption_id` 与 `reason`，输入结果不被修改。
- `summary` 四计数满足 `input_violation_count = active_violation_count + waived_violation_count`；`exemption_count` 为有效豁免数。

### 豁免错误

以下情况抛 `InvalidExemptionError`（`ValueError` 子类，公开于 `data_quality`，码 `INVALID_EXEMPTION_INPUT`），不返回部分结果：

- `result` 不是 `validate` 结果（缺少 `violations` 列表）；
- 豁免不是列表、元素不是对象、六键缺失或多余；
- 字段类型/取值非法（`exemption_id`/`reason` 为空、`record_index` 为负或非整数等）；
- `exemption_id` 重复，或两条豁免定位同一异常（reason 相同视为重复，不同视为冲突）；
- 豁免未匹配到任何已报告异常。

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

### dq profile

从标准输入读取一个 UTF-8 JSON 对象，必填 `records`，可选 `fields`；与 `dq validate` 同记录输入，但只输出画像与规则候选，不执行规则、不修改记录、不落盘：

```bash
dq profile < payload.json
```

```json
{"records": [{"id": "r1", "age": 3}, {"id": "r2", "age": 42}], "fields": ["age"]}
```

标准输出为与 `profile_records` 完全相同的一行 JSON（`record_count`、`fields`、`rule_candidates`）。合法执行退出码为 0；输入有误时退出码为 2，标准输出仅含顶层 `error` 对象（只有 `code`、`message` 两个键），错误码为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_PROFILE_INPUT`：载荷不是 JSON 对象、缺少 `records`、`records` 结构错误，或 `fields` 不是互不重复的非空字符串列表。

除标准输入与标准输出外，不写文件、不访问外部服务。

### dq exemptions

从标准输入读取一个 UTF-8 JSON 对象，必填 `result`（`dq validate` 的完整 JSON 输出）与 `exemptions`（六键豁免对象列表），以 UTF-8 JSON 输出一行拆分结果（`status`、`summary`、`active_violations`、`waived_violations`），不落盘：

```bash
dq validate < payload.json > result.json
printf '{"result": %s, "exemptions": %s}' "$(cat result.json)" "$(cat exemptions.json)" | dq exemptions
```

合法执行退出码为 0；输入有误时退出码为 2，标准输出仅含顶层 `error` 对象：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_EXEMPTION_INPUT`：载荷不是 JSON 对象、缺少 `result`/`exemptions`、`result` 不是 validate 结果，或豁免无效、重复、冲突、未匹配到异常。

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

### dq sample-lineage

从标准输入读取样本级血缘的 UTF-8 JSON 对象（`samples`、`fields`、`edges`、`sample_links`、`target` 必填；`direction`、`max_depth` 可省略，默认 `both` 与 `null`）：

```bash
dq sample-lineage < sample-lineage.json
```

```json
{
  "samples": [
    {"dataset_id": "ods", "sample_id": "a"},
    {"dataset_id": "dwd", "sample_id": "b"}
  ],
  "fields": {"ods": ["name"], "dwd": ["label"]},
  "edges": [
    {
      "source": {"table": "ods", "column": "name"},
      "target": {"table": "dwd", "column": "label"},
      "witnesses": [
        {"dataset_id": "ods", "sample_id": "a", "table": "ods", "column": "name"},
        {"dataset_id": "dwd", "sample_id": "b", "table": "dwd", "column": "label"}
      ]
    }
  ],
  "sample_links": [],
  "target": {"dataset_id": "dwd", "sample_id": "b"}
}
```

合法查询退出码为 0 并在标准输出写一行上述样本级血缘结果 JSON（无亲属时只含 `target`，仍退出 0）；输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_SAMPLE_LINEAGE_INPUT`：`samples`/`fields`/`edges`/`sample_links` 结构、标识、重复项或见证引用有误。
- `INVALID_SAMPLE_LINEAGE_QUERY`：`target`/`direction`/`max_depth` 非法。
- `UNKNOWN_SAMPLE_LINEAGE_TARGET`：`target` 未在 `samples` 中声明。
- `UNKNOWN_SAMPLE_LINEAGE_REFERENCE`：`sample_links` 端点未在 `samples` 中声明。

除标准输入与标准输出外，不写文件、不访问外部服务。

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

### dq linked-origins

从标准输入读取跨样本异常来源归因的 UTF-8 JSON 对象；顶层**恰好**含 `results`、`lineage`、`sample_links`、`targets` 四个键，多一个或少一个均报 `INVALID_INPUT`。契约与 `analyze_linked_origins` 的 Python 输入完全相同：

```bash
dq linked-origins < linked-origins.json
```

```json
{
  "results": [
    {"rule_id": "r1", "dataset_id": "ods", "field_id": "name", "sample_id": "a", "violated": true, "value": "x"},
    {"rule_id": "r2", "dwd", "field_id": "label", "sample_id": "b", "violated": true, "value": "y"}
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
    {"left": {"dataset_id": "dwd", "sample_id": "b"},
     "right": {"dataset_id": "ods", "sample_id": "a"}}
  ],
  "targets": [
    {"dataset_id": "dwd", "field_id": "label", "sample_id": "b"}
  ]
}
```

成功时退出码为 0，标准输出只写一行 Python 结果 JSON（行尾一个换行），即上述跨样本来源/影响证据报告（`reports`，与 `targets` 同序、保留重复项）。输入有误时退出码为 2 且 `message` 非空，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_INPUT`：顶层不是对象、顶层键不恰好为四个，或 `results`/`lineage`/`sample_links`/`targets` 结构、标识、重复记录、边、自链接、重复关联有误。
- `UNKNOWN_REFERENCE`：校验结果引用了未声明的数据集或字段，或 `sample_links` 端点匹配不到结果。
- `UNKNOWN_TARGET`：目标没有对应的 `violated=true` 结果。

出错时仅输出 `{"error": {"code": ..., "message": ...}}` 一行，不返回部分结果。除标准输入与标准输出外，不写文件、不访问外部服务。

### dq field-impact

从标准输入读取字段级质量影响分析的 UTF-8 JSON 对象（`datasets`、`lineageEdges`、`validationResults`、`anomalySamples`、`seedFields` 五个键均必填，键多余或缺失均报错），契约与 `analyze_field_impacts` 的 Python 输入完全相同：

```bash
dq field-impact < field-impact.json
```

合法输入退出码为 0，标准输出写入与 `analyze_field_impacts` 完全相同的 JSON（`status`、`impacts`、`unresolvedReferences`；字段可达性、路径选取、规则与样本筛选、悬空边排序等语义均不改变）。输入有误时退出码为 2 且 `message` 非空，错误码为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_IMPACT_INPUT`：顶层不是对象，或五个顶层键的嵌套结构、键集合、字段引用、规则状态、样本映射不符合公开契约。

出错时仅输出 `{"error": {"code": ..., "message": ...}}`，不返回部分结果。除标准输入与标准输出外，不写文件、不访问外部服务。

### dq change-impact

从标准输入读取变更影响分析的 UTF-8 JSON 对象；删除与重命名恰好含 `dataset`、`changeType`、`fields`、`metadata` 四个键，类型变更再含 `newType`（键多余或缺失均报错），契约与 `analyze_change_impact` 的 Python 输入完全相同：

```bash
dq change-impact < change.json
```

```json
{
  "dataset": "ods",
  "changeType": "rename",
  "fields": {"oldField": "name", "newField": "full_name"},
  "metadata": {
    "datasets": {
      "ods": [
        {"name": "name", "type": "string"},
        {"name": "full_name", "type": "string"}
      ],
      "dwd": [{"name": "label", "type": "string"}]
    },
    "edges": [
      {"source": {"dataset": "ods", "field": "name"},
       "target": {"dataset": "dwd", "field": "label"}}
    ]
  }
}
```

合法输入退出码为 0（即使没有任何下游也退出 0、`affectedDatasets` 为空列表并保留入口摘要），标准输出只写一行与 `analyze_change_impact` 完全相同的结果 JSON（`status`、`change`、`affectedDatasets`；字段来源、unknown 标记、距离与路径排序等语义均不改变），只处理内存、不修改数据/规则/血缘元数据、不落盘、不访问服务。输入有误时退出码为 2 且 `message` 非空，标准输出仅含顶层 `error` 对象（只有 `code`、`message` 两个键），不返回部分结果，错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_CHANGE_IMPACT_INPUT`：请求或元数据结构、键集合、标识、字段声明或边非法。
- `DATASET_NOT_FOUND`：入口数据集未登记。
- `FIELD_NOT_FOUND`：入口字段（重命名时为 `oldField`）未在入口数据集登记。
- `INVALID_RENAME`：重命名前后字段同名。
- `DUPLICATE_FIELD`：请求内字段重复。
- `INVALID_FIELDS`：字段集合为空。

### dq snapshot-diff

从标准输入读取两次快照异常漂移对比的 UTF-8 JSON 对象，`baseline`、`current`、`lineage` 三个顶层键均必填（键多余或缺失均报错）；契约与 `compare_quality_snapshots` 的 Python 输入完全相同：

```bash
dq snapshot-diff < snapshots.json
```

```json
{
  "baseline": {
    "results": [
      {"rule_id": "r1", "dataset_id": "ods", "field_id": "name", "sample_id": "a", "violated": true, "value": "x"}
    ]
  },
  "current": {
    "results": [
      {"rule_id": "r1", "dataset_id": "ods", "field_id": "name", "sample_id": "a", "violated": true, "value": "y"},
      {"rule_id": "r2", "dataset_id": "dwd", "field_id": "label", "sample_id": "a", "violated": true, "value": "bad"}
    ]
  },
  "lineage": {
    "datasets": ["ods", "dwd"],
    "fields": {"ods": ["name"], "dwd": ["label"]},
    "edges": [
      {"source": {"dataset": "dwd", "field": "label"},
       "target": {"dataset": "ods", "field": "name"}, "type": "upstream"}
    ]
  }
}
```

合法输入退出码为 0，标准输出只写一行与 `compare_quality_snapshots` 完全相同的结果 JSON（`status`、`summary`、`changes`；异常分类、值保留、上游漂移证据与最短路径等语义均不改变），不重跑规则。输入有误时退出码为 2 且 `message` 非空，标准输出仅含顶层 `error` 对象（只有 `code`、`message` 两个键），错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_SNAPSHOT_INPUT`：快照、结果或血缘结构、标识、重复记录或边有误。
- `UNKNOWN_SNAPSHOT_REFERENCE`：任一侧结果引用了未声明的数据集或字段。

除标准输入与标准输出外，不写文件、不访问外部服务。

### dq lineage-diff

从标准输入读取字段血缘快照比较的 UTF-8 JSON 对象，`baseline`、`current`、`targets` 三个顶层键均必填（键多余或缺失均报错）；契约与 `compare_lineage_snapshots` 的 Python 输入完全相同：

```bash
dq lineage-diff < lineage-snapshots.json
```

```json
{
  "baseline": {
    "fields": {"ods": ["name"], "dwd": ["label"]},
    "edges": [
      {"source": {"table": "ods", "column": "name"},
       "target": {"table": "dwd", "column": "label"}}
    ]
  },
  "current": {
    "fields": {"ods": ["name"], "dwd": ["label"], "ads": ["label"]},
    "edges": [
      {"source": {"table": "ods", "column": "name"},
       "target": {"table": "dwd", "column": "label"}},
      {"source": {"table": "dwd", "column": "label"},
       "target": {"table": "ads", "column": "label"}}
    ]
  },
  "targets": [{"table": "dwd", "column": "label"}]
}
```

合法查询退出码为 0（即使存在 added/removed/changed 差异），标准输出只写一行与 `compare_lineage_snapshots` 完全相同的结果 JSON（`status`、`summary`、`changes`、`targets`），只处理内存、不修改记录、不落盘、不访问服务。输入有误时退出码为 2 且 `message` 非空，标准输出仅含顶层 `error` 对象（只有 `code`、`message` 两个键），错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_LINEAGE_SNAPSHOT`：顶层或任一侧快照结构、字段、边、端点、重复项有误。
- `INVALID_LINEAGE_DIFF_QUERY`：`targets` 不是非空无重复的字段对象数组，或目标键集合/取值非法。
- `UNKNOWN_LINEAGE_DIFF_TARGET`：目标在两份快照中均未声明。

### dq quality-gates

从标准输入读取一个 UTF-8 JSON 对象，必填 `records`、`rules`、`gates`，可选 `dataset` 与 `exemptions`；规则、门槛与豁免契约与 `evaluate_quality_gates` 的 Python 输入完全相同：

```bash
dq quality-gates < gates.json
```

```json
{
  "dataset": "people",
  "records": [{"id": "r1", "age": 200}, {"id": "r2", "age": 5}],
  "rules": [
    {"id": "age-range", "type": "range", "options": {"field": "age", "min": 0, "max": 120}}
  ],
  "gates": [
    {"rule_id": "age-gate", "source_rule_id": "age-range", "max_failed_ratio": 0.1, "severity": "error"}
  ],
  "exemptions": [
    {"exemption_id": "e-legacy", "rule_id": "age-range", "record_index": 0, "record_id": "r1", "field": "age", "reason": "历史数据已知问题"}
  ]
}
```

标准输出为与 Python 入口完全相同的一行 JSON 报告（`dataset`、`results`）；省略 `exemptions` 或传空列表时报告与基线相同，否则每个门槛结果另含 `waived_count` 与 `waived_samples`。合法执行即使出现 `FAILED` 或 `SKIPPED_EMPTY_DATASET` 也退出 0；输入有误时退出码为 2，标准输出仅含顶层 `error` 对象（只有 `code`、`message` 两个键），错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_INPUT`：载荷不是 JSON 对象、缺少 `records`/`rules`/`gates`，或 `records` 结构错误。
- `INVALID_RULE`：单字段规则定义非法。
- `INVALID_QUALITY_GATE_RULE`：门槛结构非法、`rule_id` 重复、`severity` 非法，或 `max_failed_ratio` 不是 0–1 的 JSON 数字。
- `UNKNOWN_QUALITY_GATE_SOURCE`：门槛的 `source_rule_id` 未在 `rules` 中声明。
- `INVALID_EXEMPTION_INPUT`：豁免无效、重复、冲突或未匹配到异常。

### dq batch-validate

从标准输入读取一个 UTF-8 JSON 对象，**恰好**含 `records` 与 `rules` 两个顶层键（缺失或多余均报错）；记录与规则的契约与 `evaluate_batch_rules` 的 Python 输入完全相同：

```bash
dq batch-validate < batch.json
```

```json
{
  "records": [
    {"id": "r1", "country": "US", "city": "NYC", "status": "ok"},
    {"id": "r2", "country": "US", "city": "NYC", "status": "bad"},
    {"id": "r3", "country": "US", "city": "LA", "status": "bad"}
  ],
  "rules": [
    {"id": "uk", "type": "unique_key", "options": {"fields": ["country", "city"]}},
    {"id": "gr", "type": "group_ratio", "options": {"group_fields": ["country", "city"], "value_field": "status", "allowed_values": ["ok"], "min_ratio": 0.5}}
  ]
}
```

标准输出为与 Python 入口完全相同的一行 JSON 报告（`passed`、`summary`、`violations`）；规则按序执行、记录不被修改、不落盘、不访问服务。合法执行即使报告 `passed` 为 `false` 也退出 0；输入有误时退出码为 2，标准输出仅含顶层 `error` 对象（只有 `code`、`message` 两个键），错误码依次为：

- `INVALID_JSON`：输入不是合法 UTF-8 或无法解析为 JSON。
- `INVALID_BATCH_RULE`：批次规则定义非法（含重复/空 id、不受支持的类型、options 键缺失或多余、字段数组为空/含重复字段、`min_ratio` 非 `[0,1]` JSON 数字等）。
- `INVALID_BATCH_INPUT`：载荷不是 JSON 对象、顶层键不止 `records`/`rules`，或记录结构错误。
- 规则错误先于记录错误报告；非法 JSON 优先于一切。

## 测试

```bash
python -m unittest discover -s tests
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
