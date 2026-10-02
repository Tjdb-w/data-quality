"""validator 核心语义测试。"""

import pytest

from data_quality import validate
from data_quality.validator import (
    InvalidInputError,
    InvalidRuleError,
)


def rule(rule_id, rule_type, **options):
    return {"id": rule_id, "type": rule_type, "options": options}


# ---------- 结果结构 ----------


def test_all_passed_structure():
    result = validate(
        [{"id": "r1", "age": 30}],
        [rule("age-required", "required", field="age")],
    )
    assert result["passed"] is True
    assert result["violations"] == []
    assert result["summary"] == {
        "record_count": 1,
        "violation_count": 0,
        "checked_rule_count": 1,
    }


def test_failed_structure_and_counts():
    records = [
        {"id": "r1", "age": 30},
        {"id": "r2", "age": 10},
        {"id": "r3", "age": 5},
    ]
    result = validate(records, [rule("age-range", "range", field="age", min=18, max=120)])
    assert result["passed"] is False
    assert result["summary"] == {
        "record_count": 3,
        "violation_count": 2,
        "checked_rule_count": 1,
    }


def test_empty_records_and_rules():
    result = validate([], [])
    assert result == {
        "passed": True,
        "summary": {"record_count": 0, "violation_count": 0, "checked_rule_count": 0},
        "violations": [],
    }


# ---------- required ----------


def test_required_missing_and_null():
    records = [{"id": "a"}, {"id": "b", "name": None}]
    result = validate(records, [rule("name-required", "required", field="name")])
    assert len(result["violations"]) == 2
    for v in result["violations"]:
        assert v["rule_id"] == "name-required"
        assert v["field"] == "name"
        assert v["value"] is None
        assert v["message"] == "field is required"
    assert result["violations"][0]["record_index"] == 0
    assert result["violations"][0]["record_id"] == "a"
    assert result["violations"][1]["record_index"] == 1


def test_required_falsy_but_present_values_pass():
    records = [
        {"id": "a", "v": 0},
        {"id": "b", "v": False},
        {"id": "c", "v": ""},
        {"id": "d", "v": []},
    ]
    result = validate(records, [rule("v-required", "required", field="v")])
    assert result["passed"] is True


def test_record_id_defaults_to_null_when_missing():
    result = validate([{"age": None}], [rule("r", "required", field="age")])
    assert result["violations"][0]["record_id"] is None
    assert result["violations"][0]["record_index"] == 0


# ---------- unique ----------


def test_unique_reports_only_later_duplicates():
    records = [
        {"id": "a", "email": "x@example.com"},
        {"id": "b", "email": "x@example.com"},
        {"id": "c", "email": "y@example.com"},
        {"id": "d", "email": "x@example.com"},
    ]
    result = validate(records, [rule("email-unique", "unique", field="email")])
    assert [v["record_index"] for v in result["violations"]] == [1, 3]
    assert [v["record_id"] for v in result["violations"]] == ["b", "d"]
    assert all(v["message"] == "duplicate value" for v in result["violations"])
    assert all(v["value"] == "x@example.com" for v in result["violations"])


def test_unique_ignores_null_and_missing():
    records = [
        {"id": "a", "email": None},
        {"id": "b", "email": None},
        {"id": "c"},
        {"id": "d"},
        {"id": "e", "email": "z@example.com"},
    ]
    result = validate(records, [rule("u", "unique", field="email")])
    assert result["passed"] is True


def test_unique_json_equality_numbers_and_bools():
    # 1 与 1.0 在 JSON 中相等；True 不能与 1 判重。
    records = [
        {"id": "a", "v": 1},
        {"id": "b", "v": 1.0},
        {"id": "c", "v": True},
        {"id": "d", "v": True},
        {"id": "e", "v": False},
        {"id": "f", "v": 0},
    ]
    result = validate(records, [rule("u", "unique", field="v")])
    assert [v["record_index"] for v in result["violations"]] == [1, 3]


def test_unique_json_equality_objects_and_arrays():
    records = [
        {"id": "a", "v": {"a": 1, "b": 2}},
        {"id": "b", "v": {"b": 2, "a": 1}},  # 键顺序不同但 JSON 相等
        {"id": "c", "v": [1, {"x": [2, 3]}]},
        {"id": "d", "v": [1, {"x": [2, 3]}]},
    ]
    result = validate(records, [rule("u", "unique", field="v")])
    assert [v["record_index"] for v in result["violations"]] == [1, 3]


# ---------- range ----------


def test_range_inclusive_boundaries():
    records = [{"id": "a", "n": 18}, {"id": "b", "n": 120}, {"id": "c", "n": 18.0}]
    result = validate(records, [rule("r", "range", field="n", min=18, max=120)])
    assert result["passed"] is True


def test_range_outside_and_non_numbers():
    records = [
        {"id": "a", "n": 17},
        {"id": "b", "n": 121},
        {"id": "c", "n": "18"},
        {"id": "d", "n": None},
        {"id": "e"},
        {"id": "f", "n": True},
    ]
    result = validate(records, [rule("r", "range", field="n", min=18, max=120)])
    assert [v["record_index"] for v in result["violations"]] == [0, 1, 2, 3, 4, 5]
    assert all(v["message"] == "value out of range" for v in result["violations"])
    # 原始 value 保留
    by_index = {v["record_index"]: v["value"] for v in result["violations"]}
    assert by_index[2] == "18"
    assert by_index[3] is None
    assert by_index[4] is None
    assert by_index[5] is True


def test_range_negative_and_float():
    records = [{"id": "a", "n": -0.5}, {"id": "b", "n": -1.5}, {"id": "c", "n": 1.5}]
    result = validate(records, [rule("r", "range", field="n", min=-1, max=1)])
    assert [v["record_index"] for v in result["violations"]] == [1, 2]


# ---------- regex ----------


def test_regex_full_match():
    records = [
        {"id": "a", "code": "abc"},
        {"id": "b", "code": "abc1"},      # 部分匹配不算
        {"id": "c", "code": " abc"},      # 前后多余字符不算
        {"id": "d", "code": ""},
        {"id": "e", "code": "ABC"},
    ]
    result = validate(records, [rule("r", "regex", field="code", pattern="[a-z]+")])
    assert [v["record_index"] for v in result["violations"]] == [1, 2, 3, 4]
    assert all(v["message"] == "value does not match pattern" for v in result["violations"])


def test_regex_non_string_values_violate():
    records = [
        {"id": "a", "code": 123},
        {"id": "b", "code": None},
        {"id": "c"},
        {"id": "d", "code": True},
        {"id": "e", "code": ["a"]},
    ]
    result = validate(records, [rule("r", "regex", field="code", pattern=".*")])
    assert [v["record_index"] for v in result["violations"]] == [0, 1, 2, 3, 4]


def test_regex_anchored_and_unicode_pattern():
    records = [{"id": "a", "v": "数据-01"}, {"id": "b", "v": "数据-01!"}]
    result = validate(
        records, [rule("r", "regex", field="v", pattern=r"数据-\d+")]
    )
    assert [v["record_index"] for v in result["violations"]] == [1]


# ---------- allowed_values ----------


def test_allowed_values_basic():
    records = [
        {"id": "a", "level": "user"},
        {"id": "b", "level": "guest"},
        {"id": "c", "level": "admin"},
    ]
    result = validate(
        records, [rule("r", "allowed_values", field="level", values=["user", "guest"])]
    )
    assert [v["record_index"] for v in result["violations"]] == [2]
    assert result["violations"][0]["message"] == "value is not allowed"


def test_allowed_values_missing_field_judged_as_null():
    # null 不在允许集合：缺失与显式 null 都违规
    result = validate(
        [{"id": "a"}, {"id": "b", "level": None}],
        [rule("r", "allowed_values", field="level", values=["user"])],
    )
    assert [v["record_index"] for v in result["violations"]] == [0, 1]

    # null 在允许集合：缺失与显式 null 都通过
    result2 = validate(
        [{"id": "a"}, {"id": "b", "level": None}],
        [rule("r2", "allowed_values", field="level", values=["user", None])],
    )
    assert result2["passed"] is True


def test_allowed_values_json_equality():
    records = [
        {"id": "a", "n": 1, "o": {"x": 1, "y": 2}},   # 1 与 1.0 相等
        {"id": "b", "n": True, "o": {"x": 1, "y": 2}},  # True 与 1 不相等
        {"id": "c", "n": 1.0, "o": {"x": 1, "y": 2}},
        {"id": "d", "n": 1, "o": {"y": 2, "x": 1}},   # 对象键顺序无关
    ]
    rules = [
        rule("n", "allowed_values", field="n", values=[1.0]),
        rule("o", "allowed_values", field="o", values=[{"x": 1, "y": 2}]),
    ]
    result = validate(records, rules)
    assert [(v["rule_id"], v["record_index"]) for v in result["violations"]] == [
        ("n", 1)
    ]


# ---------- 检查顺序：先规则后记录 ----------


def test_rules_outer_records_inner_order():
    records = [{"id": "a", "n": 999, "name": None}, {"id": "b", "n": 999, "name": None}]
    rules = [
        rule("r1", "range", field="n", min=0, max=10),
        rule("r2", "required", field="name"),
    ]
    result = validate(records, rules)
    assert [(v["rule_id"], v["record_index"]) for v in result["violations"]] == [
        ("r1", 0),
        ("r1", 1),
        ("r2", 0),
        ("r2", 1),
    ]


# ---------- 规则定义错误 ----------


@pytest.mark.parametrize(
    "rules",
    [
        [{"type": "required", "options": {"field": "x"}}],               # 缺 id
        [{"id": "", "type": "required", "options": {"field": "x"}}],     # 空 id
        [{"id": 1, "type": "required", "options": {"field": "x"}}],      # id 非字符串
        [{"id": "r", "options": {"field": "x"}}],                        # 缺 type
        [{"id": "r", "type": "unknown", "options": {"field": "x"}}],     # 未知类型
        [{"id": "r", "type": "required"}],                               # 缺 options
        [{"id": "r", "type": "required", "options": []}],                # options 非对象
        [{"id": "r", "type": "required", "options": {}}],                # 缺 field
        [{"id": "r", "type": "required", "options": {"field": ""}}],     # 空 field
        [{"id": "r", "type": "required", "options": {"field": 1}}],      # field 非字符串
        # range
        [{"id": "r", "type": "range", "options": {"field": "x", "min": 1}}],
        [{"id": "r", "type": "range", "options": {"field": "x", "min": "1", "max": 2}}],
        [{"id": "r", "type": "range", "options": {"field": "x", "min": 2, "max": 1}}],
        [{"id": "r", "type": "range", "options": {"field": "x", "min": True, "max": 1}}],
        # regex
        [{"id": "r", "type": "regex", "options": {"field": "x"}}],
        [{"id": "r", "type": "regex", "options": {"field": "x", "pattern": "([a-"}}],
        [{"id": "r", "type": "regex", "options": {"field": "x", "pattern": 1}}],
        # allowed_values
        [{"id": "r", "type": "allowed_values", "options": {"field": "x"}}],
        [{"id": "r", "type": "allowed_values", "options": {"field": "x", "values": {}}}],
    ],
)
def test_invalid_rules_raise(rules):
    with pytest.raises(InvalidRuleError):
        validate([{"x": 1}], rules)


def test_duplicate_rule_id_raises():
    rules = [
        rule("dup", "required", field="a"),
        rule("dup", "required", field="b"),
    ]
    with pytest.raises(InvalidRuleError, match="duplicate rule id"):
        validate([], rules)


def test_rule_entry_must_be_object():
    with pytest.raises(InvalidRuleError):
        validate([], ["not-an-object"])


def test_rules_must_be_list():
    with pytest.raises(InvalidInputError):
        validate([], {"id": "r"})


def test_records_must_be_list_of_objects():
    with pytest.raises(InvalidInputError):
        validate({"id": "x"}, [])
    with pytest.raises(InvalidInputError):
        validate([[1, 2]], [])
    with pytest.raises(InvalidInputError):
        validate(["x"], [])


def test_errors_are_value_errors_with_codes():
    assert issubclass(InvalidInputError, ValueError)
    assert issubclass(InvalidRuleError, ValueError)
    try:
        validate([], [{"id": "r", "type": "nope", "options": {"field": "x"}}])
    except ValueError as exc:
        assert exc.code == "INVALID_RULE"
    try:
        validate({}, [])
    except ValueError as exc:
        assert exc.code == "INVALID_INPUT"


def test_rules_validated_before_any_check():
    # 即使记录会触发违规，规则非法时仍直接抛错而非返回部分结果。
    with pytest.raises(InvalidRuleError):
        validate([{}], [{"id": "r", "type": "bad", "options": {"field": "x"}}])
