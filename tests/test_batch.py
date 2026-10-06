"""Unit tests for :mod:`data_quality.batch`."""

import unittest

from data_quality import (
    InvalidBatchInputError,
    InvalidBatchRuleError,
    evaluate_batch_rules,
)


def unique_key_rule(rule_id="uk", fields=("a", "b")):
    return {
        "id": rule_id,
        "type": "unique_key",
        "options": {"fields": list(fields)},
    }


def group_ratio_rule(
    rule_id="gr",
    group_fields=("region",),
    value_field="status",
    allowed_values=("ok",),
    min_ratio=0.5,
):
    return {
        "id": rule_id,
        "type": "group_ratio",
        "options": {
            "group_fields": list(group_fields),
            "value_field": value_field,
            "allowed_values": list(allowed_values),
            "min_ratio": min_ratio,
        },
    }


class UniqueKeyRuleTest(unittest.TestCase):
    def test_passes_when_all_keys_unique(self):
        records = [
            {"id": "r1", "a": 1, "b": "x"},
            {"id": "r2", "a": 1, "b": "y"},
            {"id": "r3", "a": 2, "b": "x"},
        ]
        result = evaluate_batch_rules(records, [unique_key_rule()])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(
            result["summary"],
            {
                "record_count": 3,
                "rule_count": 1,
                "checked_group_count": 0,
                "violation_count": 0,
            },
        )

    def test_only_later_records_are_reported_as_duplicates(self):
        records = [
            {"id": "r1", "a": 1, "b": "x"},
            {"id": "r2", "a": 1, "b": "x"},
            {"id": "r3", "a": 1, "b": "x"},
        ]
        result = evaluate_batch_rules(records, [unique_key_rule()])
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["violations"],
            [
                {
                    "rule_id": "uk",
                    "record_index": 1,
                    "record_id": "r2",
                    "fields": ["a", "b"],
                    "value": [1, "x"],
                    "message": "is a duplicate unique key",
                },
                {
                    "rule_id": "uk",
                    "record_index": 2,
                    "record_id": "r3",
                    "fields": ["a", "b"],
                    "value": [1, "x"],
                    "message": "is a duplicate unique key",
                },
            ],
        )

    def test_all_null_key_is_skipped(self):
        records = [{"id": "r1"}, {"id": "r2", "a": None, "b": None}]
        result = evaluate_batch_rules(records, [unique_key_rule()])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])

    def test_partial_null_key_is_incomplete(self):
        records = [
            {"id": "r1", "a": 1},
            {"a": 2, "b": None},
        ]
        result = evaluate_batch_rules(records, [unique_key_rule()])
        self.assertEqual(
            result["violations"],
            [
                {
                    "rule_id": "uk",
                    "record_index": 0,
                    "record_id": "r1",
                    "fields": ["a", "b"],
                    "value": [1, None],
                    "message": "has an incomplete unique key",
                },
                {
                    "rule_id": "uk",
                    "record_index": 1,
                    "record_id": None,
                    "fields": ["a", "b"],
                    "value": [2, None],
                    "message": "has an incomplete unique key",
                },
            ],
        )

    def test_incomplete_keys_do_not_participate_in_dedup(self):
        records = [
            {"id": "r1", "a": 1},
            {"id": "r2", "a": 1},
        ]
        result = evaluate_batch_rules(records, [unique_key_rule()])
        messages = [v["message"] for v in result["violations"]]
        self.assertEqual(
            messages,
            ["has an incomplete unique key", "has an incomplete unique key"],
        )

    def test_keys_use_json_equality(self):
        # Booleans never equal numbers; containers compare structurally.
        records = [
            {"id": "r1", "a": True, "b": 1},
            {"id": "r2", "a": 1, "b": 1},
            {"id": "r3", "a": {"k": [1, 2]}, "b": 0},
            {"id": "r4", "a": {"k": [1, 2]}, "b": 0},
        ]
        result = evaluate_batch_rules(records, [unique_key_rule()])
        self.assertEqual(len(result["violations"]), 1)
        self.assertEqual(result["violations"][0]["record_index"], 3)
        self.assertEqual(
            result["violations"][0]["message"], "is a duplicate unique key"
        )

    def test_records_are_not_modified(self):
        records = [{"id": "r1", "a": 1, "b": 1}]
        snapshot = [dict(record) for record in records]
        evaluate_batch_rules(records, [unique_key_rule()])
        self.assertEqual(records, snapshot)


class GroupRatioRuleTest(unittest.TestCase):
    def test_group_below_threshold_is_reported(self):
        records = [
            {"id": "r1", "region": "cn", "status": "ok"},
            {"id": "r2", "region": "cn", "status": "bad"},
            {"id": "r3", "region": "cn", "status": "bad"},
            {"id": "r4", "region": "us", "status": "ok"},
        ]
        result = evaluate_batch_rules(
            records, [group_ratio_rule(min_ratio=0.5)]
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["summary"]["checked_group_count"], 2)
        self.assertEqual(result["summary"]["violation_count"], 1)
        self.assertEqual(
            result["violations"],
            [
                {
                    "rule_id": "gr",
                    "record_index": None,
                    "record_id": None,
                    "group": {"region": "cn"},
                    "record_count": 3,
                    "matched_count": 1,
                    "ratio": 1 / 3,
                    "samples": [
                        {
                            "record_index": 0,
                            "record_id": "r1",
                            "value": "ok",
                            "matched": True,
                        },
                        {
                            "record_index": 1,
                            "record_id": "r2",
                            "value": "bad",
                            "matched": False,
                        },
                        {
                            "record_index": 2,
                            "record_id": "r3",
                            "value": "bad",
                            "matched": False,
                        },
                    ],
                }
            ],
        )

    def test_ratio_equal_to_threshold_passes(self):
        records = [
            {"region": "cn", "status": "ok"},
            {"region": "cn", "status": "bad"},
        ]
        result = evaluate_batch_rules(
            records, [group_ratio_rule(min_ratio=0.5)]
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["checked_group_count"], 1)

    def test_missing_group_and_value_fields_read_as_null(self):
        records = [
            {"id": "r1"},
            {"id": "r2", "status": "ok"},
        ]
        result = evaluate_batch_rules(
            records, [group_ratio_rule(min_ratio=0.9)]
        )
        # Both records share the all-null group; one of two matches.
        self.assertEqual(result["summary"]["checked_group_count"], 1)
        self.assertEqual(len(result["violations"]), 1)
        violation = result["violations"][0]
        self.assertEqual(violation["group"], {"region": None})
        self.assertEqual(violation["record_count"], 2)
        self.assertEqual(violation["matched_count"], 1)
        self.assertEqual(violation["ratio"], 0.5)
        self.assertEqual(
            violation["samples"][0],
            {
                "record_index": 0,
                "record_id": "r1",
                "value": None,
                "matched": False,
            },
        )

    def test_null_can_be_an_allowed_value(self):
        records = [{"id": "r1", "region": "cn"}]
        result = evaluate_batch_rules(
            records, [group_ratio_rule(allowed_values=[None], min_ratio=1)]
        )
        self.assertTrue(result["passed"])

    def test_groups_are_ordered_by_json_ascending_key(self):
        records = [
            {"region": "b", "status": "x"},
            {"region": None, "status": "x"},
            {"region": "a", "status": "x"},
            {"region": 10, "status": "x"},
            {"region": 2, "status": "x"},
            {"region": True, "status": "x"},
        ]
        result = evaluate_batch_rules(
            records, [group_ratio_rule(min_ratio=1)]
        )
        regions = [v["group"]["region"] for v in result["violations"]]
        self.assertEqual(regions, [None, True, 2, 10, "a", "b"])

    def test_group_keys_use_json_equality(self):
        records = [
            {"region": 1, "status": "bad"},
            {"region": 1.0, "status": "bad"},
        ]
        result = evaluate_batch_rules(
            records, [group_ratio_rule(min_ratio=1)]
        )
        self.assertEqual(result["summary"]["checked_group_count"], 1)
        self.assertEqual(result["violations"][0]["record_count"], 2)

    def test_unique_key_rules_do_not_add_checked_groups(self):
        records = [{"id": "r1", "a": 1, "b": 1, "region": "cn"}]
        result = evaluate_batch_rules(
            records, [unique_key_rule(), group_ratio_rule(min_ratio=1)]
        )
        self.assertEqual(result["summary"]["checked_group_count"], 1)
        self.assertEqual(result["summary"]["rule_count"], 2)


class EmptyInputTest(unittest.TestCase):
    def test_empty_records_yield_no_violations(self):
        result = evaluate_batch_rules(
            [], [unique_key_rule(), group_ratio_rule()]
        )
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["summary"],
            {
                "record_count": 0,
                "rule_count": 2,
                "checked_group_count": 0,
                "violation_count": 0,
            },
        )

    def test_empty_rules_pass(self):
        result = evaluate_batch_rules([{"id": "r1"}], [])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["rule_count"], 0)


class RuleValidationTest(unittest.TestCase):
    def assert_rule_error(self, rules, records=None):
        with self.assertRaises(InvalidBatchRuleError):
            evaluate_batch_rules(
                [{"id": "r1"}] if records is None else records, rules
            )

    def test_rules_must_be_a_list(self):
        self.assert_rule_error({})

    def test_rule_must_be_an_object(self):
        self.assert_rule_error(["nope"])

    def test_rule_keys_are_exact(self):
        self.assert_rule_error([{"id": "x", "type": "unique_key"}])
        self.assert_rule_error(
            [
                {
                    "id": "x",
                    "type": "unique_key",
                    "options": {"fields": ["a"]},
                    "extra": 1,
                }
            ]
        )

    def test_id_must_be_non_empty_and_unique(self):
        self.assert_rule_error(
            [{"id": "", "type": "unique_key", "options": {"fields": ["a"]}}]
        )
        self.assert_rule_error(
            [
                {"id": "x", "type": "unique_key", "options": {"fields": ["a"]}},
                {"id": "x", "type": "unique_key", "options": {"fields": ["a"]}},
            ]
        )

    def test_type_must_be_supported(self):
        self.assert_rule_error(
            [{"id": "x", "type": "unique", "options": {"fields": ["a"]}}]
        )

    def test_unique_key_fields_must_be_non_empty_and_distinct(self):
        self.assert_rule_error(
            [{"id": "x", "type": "unique_key", "options": {"fields": []}}]
        )
        self.assert_rule_error(
            [
                {
                    "id": "x",
                    "type": "unique_key",
                    "options": {"fields": ["a", "a"]},
                }
            ]
        )
        self.assert_rule_error(
            [
                {
                    "id": "x",
                    "type": "unique_key",
                    "options": {"fields": ["a", ""]},
                }
            ]
        )

    def test_unique_key_options_keys_are_exact(self):
        self.assert_rule_error(
            [
                {
                    "id": "x",
                    "type": "unique_key",
                    "options": {"fields": ["a"], "other": 1},
                }
            ]
        )

    def test_group_ratio_options_keys_are_exact(self):
        rule = group_ratio_rule()
        del rule["options"]["min_ratio"]
        self.assert_rule_error([rule])
        rule = group_ratio_rule()
        rule["options"]["extra"] = 1
        self.assert_rule_error([rule])

    def test_group_ratio_options_values(self):
        rule = group_ratio_rule(group_fields=[])
        self.assert_rule_error([rule])
        rule = group_ratio_rule(group_fields=["a", "a"])
        self.assert_rule_error([rule])
        rule = group_ratio_rule(value_field="")
        self.assert_rule_error([rule])
        rule = group_ratio_rule(allowed_values=[])
        self.assert_rule_error([rule])
        rule = group_ratio_rule(min_ratio=True)
        self.assert_rule_error([rule])
        rule = group_ratio_rule(min_ratio=-0.1)
        self.assert_rule_error([rule])
        rule = group_ratio_rule(min_ratio=1.1)
        self.assert_rule_error([rule])


class RecordValidationTest(unittest.TestCase):
    def test_records_must_be_a_list(self):
        with self.assertRaises(InvalidBatchInputError):
            evaluate_batch_rules({"a": 1}, [])

    def test_records_must_be_objects(self):
        with self.assertRaises(InvalidBatchInputError):
            evaluate_batch_rules([{"a": 1}, 2], [])

    def test_rules_are_validated_before_records(self):
        with self.assertRaises(InvalidBatchRuleError):
            evaluate_batch_rules("not-a-list", "not-a-list-either")


if __name__ == "__main__":
    unittest.main()
