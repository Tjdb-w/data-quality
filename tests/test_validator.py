"""Tests for data_quality.validate and the five first-version rules."""

import unittest

from data_quality import (
    InvalidInputError,
    InvalidRuleError,
    validate,
)


def rule(rule_id, rule_type, **options):
    return {"id": rule_id, "type": rule_type, "options": dict(options)}


class RequiredRuleTest(unittest.TestCase):
    RULE = rule("r-required", "required", field="name")

    def test_missing_and_null_violate(self):
        result = validate(
            [{"id": "a"}, {"id": "b", "name": None}, {"id": "c", "name": "x"}],
            [self.RULE],
        )
        self.assertFalse(result["passed"])
        indices = [v["record_index"] for v in result["violations"]]
        self.assertEqual(indices, [0, 1])
        for violation in result["violations"]:
            self.assertEqual(violation["rule_id"], "r-required")
            self.assertEqual(violation["field"], "name")
            self.assertIsNone(violation["value"])
            self.assertEqual(violation["message"], "is required but is missing or null")

    def test_falsey_present_values_pass(self):
        result = validate(
            [
                {"id": 1, "name": 0},
                {"id": 2, "name": ""},
                {"id": 3, "name": False},
            ],
            [self.RULE],
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])


class UniqueRuleTest(unittest.TestCase):
    RULE = rule("r-unique", "unique", field="code")

    def test_only_later_duplicates_reported(self):
        records = [
            {"id": "a", "code": "x"},
            {"id": "b", "code": "y"},
            {"id": "c", "code": "x"},
            {"id": "d", "code": "x"},
        ]
        result = validate(records, [self.RULE])
        indices = [v["record_index"] for v in result["violations"]]
        self.assertEqual(indices, [2, 3])
        self.assertEqual([v["value"] for v in result["violations"]], ["x", "x"])
        self.assertEqual(
            [v["record_id"] for v in result["violations"]], ["c", "d"]
        )

    def test_nulls_are_ignored(self):
        result = validate(
            [
                {"id": "a", "code": None},
                {"id": "b"},
                {"id": "c", "code": None},
            ],
            [self.RULE],
        )
        self.assertTrue(result["passed"])

    def test_json_equality_for_objects_and_arrays(self):
        result = validate(
            [
                {"id": 1, "code": {"a": 1, "b": [1, 2]}},
                {"id": 2, "code": {"b": [1, 2], "a": 1}},
                {"id": 3, "code": [1, {"x": True}]},
                {"id": 4, "code": [1, {"x": True}]},
            ],
            [self.RULE],
        )
        self.assertEqual(
            [v["record_index"] for v in result["violations"]], [1, 3]
        )

    def test_boolean_is_not_equal_to_number(self):
        result = validate(
            [{"id": 1, "code": 1}, {"id": 2, "code": True}],
            [self.RULE],
        )
        self.assertTrue(result["passed"])


class RangeRuleTest(unittest.TestCase):
    RULE = rule("r-range", "range", field="age", min=0, max=100)

    def test_inclusive_bounds(self):
        result = validate(
            [{"id": "a", "age": 0}, {"id": "b", "age": 100}],
            [self.RULE],
        )
        self.assertTrue(result["passed"])

    def test_out_of_range_and_non_numbers_violate(self):
        result = validate(
            [
                {"id": "a", "age": -1},
                {"id": "b", "age": 101},
                {"id": "c", "age": "42"},
                {"id": "d", "age": None},
                {"id": "e"},
                {"id": "f", "age": True},
            ],
            [self.RULE],
        )
        self.assertEqual(
            [v["record_index"] for v in result["violations"]],
            [0, 1, 2, 3, 4, 5],
        )
        self.assertEqual(result["violations"][2]["value"], "42")
        self.assertIsNone(result["violations"][4]["value"])
        self.assertTrue(result["violations"][5]["value"])


class RegexRuleTest(unittest.TestCase):
    RULE = rule("r-regex", "regex", field="sku", pattern=r"[A-Z]{2}-\d+")

    def test_full_match_required(self):
        result = validate(
            [{"id": 1, "sku": "AB-12"}],
            [rule("p", "regex", field="sku", pattern=r"[A-Z]{2}-\d+")],
        )
        self.assertTrue(result["passed"])

        result = validate(
            [
                {"id": 1, "sku": "AB-12x"},
                {"id": 2, "sku": "xAB-12"},
                {"id": 3, "sku": 12},
                {"id": 4, "sku": None},
                {"id": 5},
            ],
            [self.RULE],
        )
        self.assertEqual(
            [v["record_index"] for v in result["violations"]],
            [0, 1, 2, 3, 4],
        )
        self.assertEqual(
            result["violations"][0]["message"],
            "does not match the required pattern",
        )

    def test_anchors_are_respected(self):
        result = validate(
            [{"id": 1, "sku": "abc"}],
            [rule("p", "regex", field="sku", pattern=r"^abc$")],
        )
        self.assertTrue(result["passed"])


class AllowedValuesRuleTest(unittest.TestCase):
    RULE = rule("r-allowed", "allowed_values", field="color", values=["red", "green"])

    def test_membership(self):
        result = validate(
            [
                {"id": 1, "color": "red"},
                {"id": 2, "color": "blue"},
                {"id": 3, "color": None},
                {"id": 4},
            ],
            [self.RULE],
        )
        self.assertEqual(
            [v["record_index"] for v in result["violations"]], [1, 2, 3]
        )
        # Missing field is judged as null: the reported value is null.
        self.assertIsNone(result["violations"][2]["value"])

    def test_null_can_be_allowed(self):
        result = validate(
            [{"id": 1, "color": None}, {"id": 2}],
            [rule("p", "allowed_values", field="color", values=["red", None])],
        )
        self.assertTrue(result["passed"])

    def test_structured_values_use_json_equality(self):
        result = validate(
            [{"id": 1, "meta": {"a": 1, "b": 2}}],
            [rule(
                "p",
                "allowed_values",
                field="meta",
                values=[{"b": 2, "a": 1}],
            )],
        )
        self.assertTrue(result["passed"])


class OrderingAndResultShapeTest(unittest.TestCase):
    def test_rules_checked_before_records_rule_major_order(self):
        rules = [
            rule("r1", "required", field="a"),
            rule("r2", "range", field="b", min=0, max=1),
        ]
        records = [
            {"id": "x", "a": None, "b": 5},
            {"id": "y", "a": None, "b": 5},
        ]
        result = validate(records, rules)
        order = [(v["rule_id"], v["record_index"]) for v in result["violations"]]
        self.assertEqual(
            order,
            [("r1", 0), ("r1", 1), ("r2", 0), ("r2", 1)],
        )

    def test_summary_and_missing_record_id(self):
        records = [
            {"name": "ok", "age": 30},
            {"age": 30},  # missing name, no record id
        ]
        result = validate(records, [rule("r", "required", field="name")])
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["summary"],
            {"record_count": 2, "violation_count": 1, "checked_rule_count": 1},
        )
        violation = result["violations"][0]
        self.assertEqual(violation["record_index"], 1)
        self.assertIsNone(violation["record_id"])

    def test_id_field_is_usable_as_rule_field(self):
        result = validate(
            [{"id": "a"}, {"id": None}],
            [rule("r", "required", field="id")],
        )
        self.assertEqual(
            [v["record_index"] for v in result["violations"]], [1]
        )

    def test_empty_inputs_pass(self):
        result = validate([], [])
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["summary"],
            {"record_count": 0, "violation_count": 0, "checked_rule_count": 0},
        )


class InvalidInputTest(unittest.TestCase):
    def test_records_must_be_list(self):
        with self.assertRaises(InvalidInputError):
            validate({}, [])
        with self.assertRaises(ValueError):
            validate({}, [])

    def test_records_must_be_objects(self):
        with self.assertRaises(InvalidInputError):
            validate([1, 2], [])
        with self.assertRaises(InvalidInputError):
            validate(["nope"], [])


class InvalidRuleTest(unittest.TestCase):
    def test_rules_must_be_list(self):
        with self.assertRaises(InvalidRuleError):
            validate([], {})

    def test_rule_must_be_object(self):
        with self.assertRaises(InvalidRuleError):
            validate([], ["nope"])

    def test_id_required_non_empty_unique(self):
        with self.assertRaises(InvalidRuleError):
            validate([], [{"type": "required", "options": {"field": "a"}}])
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("", "required", field="a")])
        with self.assertRaises(InvalidRuleError):
            validate(
                [],
                [
                    rule("dup", "required", field="a"),
                    rule("dup", "required", field="b"),
                ]
            )

    def test_unknown_type(self):
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("r", "between", field="a")])

    def test_options_and_field_required(self):
        with self.assertRaises(InvalidRuleError):
            validate([], [{"id": "r", "type": "required"}])
        with self.assertRaises(InvalidRuleError):
            validate([], [{"id": "r", "type": "required", "options": {}}])

    def test_range_min_max_rules(self):
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("r", "range", field="a", min=5, max=4)])
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("r", "range", field="a", min="0", max=4)])
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("r", "range", field="a", min=0)])

    def test_regex_pattern_must_be_valid(self):
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("r", "regex", field="a", pattern="(")])
        with self.assertRaises(InvalidRuleError):
            validate([], [rule("r", "regex", field="a", pattern=123)])

    def test_allowed_values_must_be_list(self):
        with self.assertRaises(InvalidRuleError):
            validate(
                [], [rule("r", "allowed_values", field="a", values="red")]
            )

    def test_unknown_option_rejected(self):
        with self.assertRaises(InvalidRuleError):
            validate(
                [],
                [rule("r", "required", field="a", unexpected=True)],
            )


if __name__ == "__main__":
    unittest.main()
