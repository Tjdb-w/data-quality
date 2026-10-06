"""Tests for data_quality.evaluate_batch_rules batch cross-record rules."""

import copy
import unittest

from data_quality import (
    InvalidBatchInputError,
    InvalidBatchRuleError,
    evaluate_batch_rules,
)


def unique_rule(rule_id="uk", fields=("a", "b")):
    return {
        "id": rule_id,
        "type": "unique_key",
        "options": {"fields": list(fields)},
    }


def ratio_rule(
    rule_id="gr",
    group_fields=("g",),
    value_field="v",
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


class EmptyInputTest(unittest.TestCase):
    def test_empty_records_and_rules_passes(self):
        result = evaluate_batch_rules([], [])
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["summary"],
            {
                "record_count": 0,
                "rule_count": 0,
                "checked_group_count": 0,
                "violation_count": 0,
            },
        )
        self.assertEqual(result["violations"], [])

    def test_records_without_rules_pass(self):
        result = evaluate_batch_rules([{"id": "r1", "a": 1}], [])
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["rule_count"], 0)
        self.assertEqual(result["violations"], [])

    def test_rules_without_records_pass(self):
        result = evaluate_batch_rules([], [unique_rule(), ratio_rule()])
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["record_count"], 0)
        self.assertEqual(result["summary"]["checked_group_count"], 0)


class UniqueKeyRuleTest(unittest.TestCase):
    RULE = unique_rule("uk", ("country", "city"))

    def test_only_later_duplicates_reported(self):
        records = [
            {"id": "r1", "country": "US", "city": "NYC"},
            {"id": "r2", "country": "FR", "city": "PAR"},
            {"id": "r3", "country": "US", "city": "NYC"},
            {"id": "r4", "country": "US", "city": "NYC"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        self.assertTrue(result["passed"] is False)
        self.assertEqual(
            [v["record_index"] for v in result["violations"]], [2, 3]
        )
        for violation in result["violations"]:
            self.assertEqual(violation["rule_id"], "uk")
            self.assertEqual(violation["fields"], ["country", "city"])
            self.assertEqual(violation["value"], ["US", "NYC"])
            self.assertEqual(violation["message"], "is a duplicate unique key")
        self.assertEqual(
            [v["record_id"] for v in result["violations"]], ["r3", "r4"]
        )

    def test_all_null_key_is_skipped(self):
        records = [
            {"id": "r1"},
            {"id": "r2", "country": None, "city": None},
            {"id": "r3"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["checked_group_count"], 0)

    def test_partial_null_key_is_incomplete_and_reproduced_as_json(self):
        records = [
            {"id": "r1", "country": "US", "city": "NYC"},
            {"id": "r2", "country": "US"},
            {"id": "r3", "country": None, "city": "LA"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        self.assertEqual(len(result["violations"]), 2)
        self.assertEqual(
            result["violations"][0],
            {
                "rule_id": "uk",
                "record_index": 1,
                "record_id": "r2",
                "fields": ["country", "city"],
                "value": ["US", None],
                "message": "has an incomplete unique key",
            },
        )
        self.assertEqual(
            result["violations"][1],
            {
                "rule_id": "uk",
                "record_index": 2,
                "record_id": "r3",
                "fields": ["country", "city"],
                "value": [None, "LA"],
                "message": "has an incomplete unique key",
            },
        )

    def test_incomplete_key_does_not_deduplicate_complete_key(self):
        records = [
            {"id": "r1", "country": "US"},
            {"id": "r2", "country": "US", "city": "NYC"},
            {"id": "r3", "country": "US", "city": "NYC"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        messages = [
            (v["record_index"], v["message"]) for v in result["violations"]
        ]
        self.assertEqual(
            messages,
            [(0, "has an incomplete unique key"), (2, "is a duplicate unique key")],
        )

    def test_missing_record_id_is_null(self):
        records = [
            {"country": "US", "city": "NYC"},
            {"country": "US", "city": "NYC"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        violation = result["violations"][0]
        self.assertEqual(violation["record_index"], 1)
        self.assertIsNone(violation["record_id"])

    def test_json_equality_for_keys(self):
        rule = unique_rule("uk", ("k",))
        records = [
            {"id": "a", "k": 1},
            {"id": "b", "k": 1.0},
            {"id": "c", "k": True},
            {"id": "d", "k": {"x": 1, "y": 2}},
            {"id": "e", "k": {"y": 2, "x": 1}},
        ]
        result = evaluate_batch_rules(records, [rule])
        self.assertEqual(
            [v["record_index"] for v in result["violations"]], [1, 4]
        )

    def test_violations_follow_record_order(self):
        records = [
            {"id": "r1", "country": "US", "city": "NYC"},
            {"id": "r2", "country": "US"},
            {"id": "r3", "country": "US", "city": "NYC"},
            {"id": "r4", "country": "FR", "city": "PAR"},
            {"id": "r5", "country": "FR", "city": "PAR"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        self.assertEqual(
            [v["record_index"] for v in result["violations"]], [1, 2, 4]
        )
        self.assertEqual(result["summary"]["violation_count"], 3)


class GroupRatioRuleTest(unittest.TestCase):
    RULE = ratio_rule("gr", ("country", "city"), "status", ("ok",), 0.5)

    def test_group_below_threshold_reported_once(self):
        records = [
            {"id": "r1", "country": "US", "city": "LA", "status": "bad"},
            {"id": "r2", "country": "US", "city": "LA", "status": "bad"},
            {"id": "r3", "country": "US", "city": "LA", "status": "ok"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["violations"]), 1)
        violation = result["violations"][0]
        self.assertEqual(violation["rule_id"], "gr")
        self.assertEqual(violation["record_index"], 0)
        self.assertEqual(violation["record_id"], "r1")
        self.assertEqual(
            violation["group"],
            [
                {"field": "country", "value": "US"},
                {"field": "city", "value": "LA"},
            ],
        )
        self.assertEqual(violation["record_count"], 3)
        self.assertEqual(violation["matched_count"], 1)
        self.assertAlmostEqual(violation["ratio"], 1 / 3)

    def test_samples_cover_every_member_in_record_order(self):
        records = [
            {"id": "r1", "country": "US", "city": "LA", "status": "bad"},
            {"id": "r2", "country": "US", "city": "LA", "status": "ok"},
            {"id": "r3", "country": "US", "city": "LA", "status": "other"},
        ]
        result = evaluate_batch_rules(records, [self.RULE])
        samples = result["violations"][0]["samples"]
        self.assertEqual(
            samples,
            [
                {"record_index": 0, "record_id": "r1", "value": "bad", "matched": False},
                {"record_index": 1, "record_id": "r2", "value": "ok", "matched": True},
                {"record_index": 2, "record_id": "r3", "value": "other", "matched": False},
            ],
        )

    def test_ratio_equal_to_threshold_passes(self):
        records = [
            {"id": "r1", "g": "x", "v": "ok"},
            {"id": "r2", "g": "x", "v": "bad"},
        ]
        result = evaluate_batch_rules(records, [ratio_rule(min_ratio=0.5)])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])

    def test_null_group_and_value_records_are_excluded(self):
        records = [
            {"id": "r1", "g": "x", "v": "ok"},
            {"id": "r2", "g": None, "v": "bad"},
            {"id": "r3", "v": "bad"},
            {"id": "r4", "g": "x", "v": None},
            {"id": "r5", "g": "x"},
        ]
        result = evaluate_batch_rules(records, [ratio_rule(min_ratio=0.99)])
        # Only r1 forms the single-member group and it matches.
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["checked_group_count"], 1)

    def test_checked_group_count_counts_all_actual_groups(self):
        records = [
            {"id": "r1", "g": "a", "v": "ok"},
            {"id": "r2", "g": "b", "v": "bad"},
            {"id": "r3", "g": "b", "v": "bad"},
            {"id": "r4", "g": "c", "v": "bad"},
        ]
        result = evaluate_batch_rules(records, [ratio_rule(min_ratio=0.0)])
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["checked_group_count"], 3)

    def test_groups_report_in_json_ascending_key_order(self):
        records = [
            {"id": "b2", "g": "b", "v": "bad"},
            {"id": "a1", "g": "a", "v": "bad"},
            {"id": "n2", "g": 10, "v": "bad"},
            {"id": "n1", "g": 2, "v": "bad"},
        ]
        result = evaluate_batch_rules(records, [ratio_rule(min_ratio=1.0)])
        keys = [
            [entry["value"] for entry in v["group"]]
            for v in result["violations"]
        ]
        self.assertEqual(keys, [[2], [10], ["a"], ["b"]])

    def test_multi_field_group_sort_uses_fixed_field_order(self):
        records = [
            {"id": "1", "g1": "US", "g2": "LA", "v": "bad"},
            {"id": "2", "g1": "US", "g2": "NYC", "v": "bad"},
            {"id": "3", "g1": "FR", "g2": "PAR", "v": "bad"},
        ]
        rule = ratio_rule(group_fields=("g1", "g2"), min_ratio=1.0)
        result = evaluate_batch_rules(records, [rule])
        keys = [
            [entry["value"] for entry in v["group"]]
            for v in result["violations"]
        ]
        self.assertEqual(keys, [["FR", "PAR"], ["US", "LA"], ["US", "NYC"]])

    def test_group_uses_first_member_as_locator(self):
        records = [
            {"id": "r1", "g": "x", "v": "ok"},
            {"id": "r2", "g": "x", "v": "bad"},
            {"id": "r3", "g": "x", "v": "bad"},
        ]
        result = evaluate_batch_rules(records, [ratio_rule(min_ratio=1.0)])
        violation = result["violations"][0]
        self.assertEqual(violation["record_index"], 0)
        self.assertEqual(violation["record_id"], "r1")
        self.assertEqual(violation["matched_count"], 1)
        self.assertEqual(violation["record_count"], 3)
        self.assertAlmostEqual(violation["ratio"], 1 / 3)

    def test_json_equality_for_values_and_groups(self):
        records = [
            {"id": "r1", "g": 1, "v": 1},
            {"id": "r2", "g": 1.0, "v": 1.0},
            {"id": "r3", "g": True, "v": True},
        ]
        # Two groups: 1/1.0 (two members, both match) and true (does not).
        result = evaluate_batch_rules(
            records, [ratio_rule(allowed_values=(1,), min_ratio=1.0)]
        )
        self.assertEqual(len(result["violations"]), 1)
        violation = result["violations"][0]
        self.assertEqual(violation["group"], [{"field": "g", "value": True}])
        self.assertEqual(violation["record_count"], 1)
        self.assertEqual(violation["matched_count"], 0)
        self.assertEqual(result["summary"]["checked_group_count"], 2)

    def test_empty_allowed_values_match_nothing(self):
        records = [{"id": "r1", "g": "x", "v": "ok"}]
        passing = evaluate_batch_rules(
            records, [ratio_rule(allowed_values=(), min_ratio=0.0)]
        )
        self.assertTrue(passing["passed"])
        failing = evaluate_batch_rules(
            records, [ratio_rule(allowed_values=(), min_ratio=0.01)]
        )
        self.assertFalse(failing["passed"])
        self.assertEqual(
            failing["violations"][0]["samples"][0]["matched"], False
        )

    def test_missing_record_id_in_samples_is_null(self):
        records = [
            {"g": "x", "v": "bad"},
            {"g": "x", "v": "bad"},
        ]
        result = evaluate_batch_rules(records, [ratio_rule(min_ratio=1.0)])
        violation = result["violations"][0]
        self.assertIsNone(violation["record_id"])
        self.assertEqual(
            [sample["record_id"] for sample in violation["samples"]],
            [None, None],
        )


class MixedRulesTest(unittest.TestCase):
    def test_rules_execute_in_order(self):
        records = [
            {"id": "r1", "g": "x", "v": "bad"},
            {"id": "r2", "g": "x", "v": "bad"},
        ]
        rules = [
            ratio_rule("gr-rule", min_ratio=1.0),
            unique_rule("uk-rule", ("g",)),
        ]
        result = evaluate_batch_rules(records, rules)
        self.assertEqual(
            [v["rule_id"] for v in result["violations"]],
            ["gr-rule", "uk-rule"],
        )
        self.assertEqual(result["summary"]["rule_count"], 2)
        # unique_key contributes no groups; one actual group was checked.
        self.assertEqual(result["summary"]["checked_group_count"], 1)
        self.assertEqual(result["summary"]["violation_count"], 2)
        self.assertFalse(result["passed"])

    def test_multiple_group_rules_accumulate_groups(self):
        records = [
            {"id": "r1", "g1": "a", "g2": "x", "v": "bad"},
            {"id": "r2", "g1": "b", "g2": "x", "v": "bad"},
        ]
        rules = [
            ratio_rule("g1-rule", group_fields=("g1",), min_ratio=1.0),
            ratio_rule("g2-rule", group_fields=("g2",), min_ratio=1.0),
        ]
        result = evaluate_batch_rules(records, rules)
        self.assertEqual(result["summary"]["checked_group_count"], 3)
        self.assertEqual(len(result["violations"]), 3)


class RuleValidationTest(unittest.TestCase):
    def assert_invalid_rule(self, rules):
        with self.assertRaises(InvalidBatchRuleError):
            evaluate_batch_rules([], rules)

    def test_rules_must_be_a_list(self):
        self.assert_invalid_rule({"id": "x"})

    def test_rule_must_be_object(self):
        self.assert_invalid_rule(["not-an-object"])

    def test_rule_keys_exact(self):
        base = {"id": "u", "type": "unique_key", "options": {"fields": ["a"]}}
        for bad in (
            {"id": "u", "type": "unique_key"},
            {"id": "u", "options": {"fields": ["a"]}},
            {"type": "unique_key", "options": {"fields": ["a"]}},
        ):
            self.assert_invalid_rule([bad])
        extra = dict(base)
        extra["severity"] = "error"
        self.assert_invalid_rule([extra])

    def test_id_must_be_unique_non_empty_string(self):
        self.assert_invalid_rule(
            [{"id": "", "type": "unique_key", "options": {"fields": ["a"]}}]
        )
        self.assert_invalid_rule(
            [{"id": 1, "type": "unique_key", "options": {"fields": ["a"]}}]
        )
        self.assert_invalid_rule(
            [
                {"id": "u", "type": "unique_key", "options": {"fields": ["a"]}},
                {"id": "u", "type": "unique_key", "options": {"fields": ["b"]}},
            ]
        )

    def test_type_must_be_supported(self):
        self.assert_invalid_rule(
            [{"id": "u", "type": "unique", "options": {"fields": ["a"]}}]
        )
        self.assert_invalid_rule(
            [{"id": "u", "type": 1, "options": {"fields": ["a"]}}]
        )

    def test_options_must_be_object(self):
        self.assert_invalid_rule(
            [{"id": "u", "type": "unique_key", "options": []}]
        )

    def test_unique_key_fields_rules(self):
        ok = {"fields": ["a"]}
        for options in (
            {},
            {"fields": []},
            {"fields": ["a", "a"]},
            {"fields": ["a", ""]},
            {"fields": ["a", 1]},
            {"fields": "a"},
            {"fields": ["a"], "extra": 1},
        ):
            self.assert_invalid_rule(
                [{"id": "u", "type": "unique_key", "options": options}]
            )
        # Sanity: the well-formed variant is accepted.
        evaluate_batch_rules(
            [], [{"id": "u", "type": "unique_key", "options": ok}]
        )

    def test_group_ratio_options_rules(self):
        base = {
            "group_fields": ["g"],
            "value_field": "v",
            "allowed_values": ["ok"],
            "min_ratio": 0.5,
        }

        def rule_with(**overrides):
            options = dict(base)
            options.update(overrides)
            return [{"id": "gr", "type": "group_ratio", "options": options}]

        for bad_group_fields in ([], ["g", "g"], ["g", ""], [1], "g"):
            self.assert_invalid_rule(rule_with(group_fields=bad_group_fields))
        for bad_value_field in ("", 1, None):
            self.assert_invalid_rule(rule_with(value_field=bad_value_field))
        self.assert_invalid_rule(rule_with(allowed_values="ok"))
        for bad_ratio in (True, False, "0.5", -0.01, 1.01, None):
            self.assert_invalid_rule(rule_with(min_ratio=bad_ratio))

        # An empty allowed list is allowed (nothing matches).
        evaluate_batch_rules([], rule_with(allowed_values=[]))
        # Boundary ratios are accepted.
        evaluate_batch_rules([], rule_with(min_ratio=0))
        evaluate_batch_rules([], rule_with(min_ratio=1))

    def test_group_ratio_options_keys_exact(self):
        for options in (
            {"value_field": "v", "allowed_values": ["ok"], "min_ratio": 0.5},
            {"group_fields": ["g"], "allowed_values": ["ok"], "min_ratio": 0.5},
            {"group_fields": ["g"], "value_field": "v", "min_ratio": 0.5},
            {"group_fields": ["g"], "value_field": "v", "allowed_values": ["ok"]},
            {
                "group_fields": ["g"],
                "value_field": "v",
                "allowed_values": ["ok"],
                "min_ratio": 0.5,
                "extra": 1,
            },
        ):
            self.assert_invalid_rule(
                [{"id": "gr", "type": "group_ratio", "options": options}]
            )


class RecordValidationTest(unittest.TestCase):
    def test_records_must_be_a_list(self):
        with self.assertRaises(InvalidBatchInputError):
            evaluate_batch_rules({"id": "r1"}, [])

    def test_records_must_be_objects(self):
        with self.assertRaises(InvalidBatchInputError):
            evaluate_batch_rules([1, "x", None], [])

    def test_rules_validated_before_records(self):
        bad_rule = {"id": "u", "type": "unique_key", "options": {"fields": []}}
        with self.assertRaises(InvalidBatchRuleError):
            evaluate_batch_rules([1], [bad_rule])

    def test_records_are_not_modified(self):
        records = [
            {"id": "r1", "country": "US", "city": None, "status": "ok"},
            {"id": "r2", "country": "US", "city": "LA", "status": "bad"},
        ]
        snapshot = copy.deepcopy(records)
        rules = [unique_rule(), ratio_rule()]
        evaluate_batch_rules(records, rules)
        self.assertEqual(records, snapshot)


if __name__ == "__main__":
    unittest.main()
