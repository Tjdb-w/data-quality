"""Tests for data_quality.profile_records field profiling."""

import copy
import unittest

from data_quality import (
    InvalidProfileInputError,
    profile_records,
    validate,
)

ZERO_TYPES = {
    "null": 0,
    "boolean": 0,
    "number": 0,
    "string": 0,
    "array": 0,
    "object": 0,
}


def rule(rule_id, rule_type, **options):
    return {"id": rule_id, "type": rule_type, "options": dict(options)}


def by_id(result):
    return {candidate["id"]: candidate for candidate in result["rule_candidates"]}


class EmptyInputTest(unittest.TestCase):
    def test_empty_records_without_fields(self):
        result = profile_records([])
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["fields"], [])
        self.assertEqual(result["rule_candidates"], [])

    def test_empty_records_with_fields_gives_zero_profiles(self):
        result = profile_records([], ["a", "b"])
        self.assertEqual(result["record_count"], 0)
        self.assertEqual([f["field"] for f in result["fields"]], ["a", "b"])
        for profile in result["fields"]:
            self.assertEqual(profile["present_count"], 0)
            self.assertEqual(profile["missing_count"], 0)
            self.assertEqual(profile["null_count"], 0)
            self.assertEqual(profile["non_null_count"], 0)
            self.assertEqual(profile["distinct_non_null_count"], 0)
            self.assertEqual(profile["type_counts"], dict(ZERO_TYPES))
            self.assertIsNone(profile["numeric_min"])
            self.assertIsNone(profile["numeric_max"])
        self.assertEqual(result["rule_candidates"], [])


class FieldOrderTest(unittest.TestCase):
    def test_default_order_is_first_appearance_of_top_level_keys(self):
        records = [
            {"b": 1, "a": 1},
            {"c": 1},
            {"a": 2, "b": 2},
        ]
        result = profile_records(records)
        self.assertEqual(
            [f["field"] for f in result["fields"]], ["b", "a", "c"]
        )

    def test_given_field_order_is_preserved(self):
        records = [{"a": 1, "b": 1}, {"c": 1}]
        result = profile_records(records, ["c", "a", "b"])
        self.assertEqual(
            [f["field"] for f in result["fields"]], ["c", "a", "b"]
        )

    def test_only_top_level_keys_are_profiled(self):
        records = [{"outer": {"inner": 1}, "arr": [{"x": 2}]}]
        result = profile_records(records)
        self.assertEqual(
            [f["field"] for f in result["fields"]], ["outer", "arr"]
        )
        outer = result["fields"][0]
        self.assertEqual(outer["type_counts"]["object"], 1)
        self.assertEqual(outer["non_null_count"], 1)
        self.assertEqual(outer["distinct_non_null_count"], 1)


class ProfileCountsTest(unittest.TestCase):
    def test_present_missing_null_counts(self):
        records = [
            {"f": 1},
            {"f": None},
            {},
            {"f": "x"},
        ]
        profile = profile_records(records)["fields"][0]
        self.assertEqual(profile["field"], "f")
        self.assertEqual(profile["present_count"], 3)
        self.assertEqual(profile["missing_count"], 1)
        self.assertEqual(profile["null_count"], 1)
        self.assertEqual(profile["non_null_count"], 2)

    def test_type_counts_cover_six_types_and_boolean_is_not_number(self):
        records = [
            {"f": None},
            {"f": True},
            {"f": False},
            {"f": 1},
            {"f": 2.5},
            {"f": "s"},
            {"f": [1]},
            {"f": {"k": 1}},
        ]
        profile = profile_records(records, ["f"])["fields"][0]
        self.assertEqual(
            profile["type_counts"],
            {
                "null": 1,
                "boolean": 2,
                "number": 2,
                "string": 1,
                "array": 1,
                "object": 1,
            },
        )
        self.assertIsNone(profile["numeric_min"])
        self.assertIsNone(profile["numeric_max"])

    def test_distinct_uses_validate_json_equality(self):
        records = [
            {"f": {"a": 1, "b": [1, 2]}},
            {"f": {"b": [1, 2], "a": 1}},
            {"f": [1, {"x": True}]},
            {"f": [1, {"x": True}]},
            {"f": 1},
            {"f": True},
            {"f": None},
            {"f": None},
        ]
        profile = profile_records(records, ["f"])["fields"][0]
        # 2 equal objects count once, 2 equal arrays count once, 1 and
        # true differ, nulls never enter the distinct count.
        self.assertEqual(profile["distinct_non_null_count"], 4)

    def test_numeric_min_max(self):
        profile = profile_records(
            [{"f": 3}, {"f": -2}, {"f": 7}], ["f"]
        )["fields"][0]
        self.assertEqual(profile["numeric_min"], -2)
        self.assertEqual(profile["numeric_max"], 7)

    def test_numeric_min_max_null_when_mixed_types(self):
        profile = profile_records(
            [{"f": 3}, {"f": "7"}, {"f": True}], ["f"]
        )["fields"][0]
        self.assertIsNone(profile["numeric_min"])
        self.assertIsNone(profile["numeric_max"])

    def test_numeric_min_max_null_when_all_null_or_missing(self):
        profile = profile_records(
            [{"f": None}, {}, {"f": None}], ["f"]
        )["fields"][0]
        self.assertIsNone(profile["numeric_min"])
        self.assertIsNone(profile["numeric_max"])


class RuleCandidatesTest(unittest.TestCase):
    def _candidate_types(self, result, field):
        return [
            c["type"]
            for c in result["rule_candidates"]
            if c["options"]["field"] == field
        ]

    def test_full_set_order_and_ids(self):
        result = profile_records(
            [{"f": 1}, {"f": 2}, {"f": 3}], ["f"]
        )
        self.assertEqual(self._candidate_types(result, "f"),
                         ["required", "unique", "range", "allowed_values"])
        candidates = by_id(result)
        self.assertEqual(
            candidates["profile:f:required"],
            rule("profile:f:required", "required", field="f"),
        )
        self.assertEqual(
            candidates["profile:f:unique"],
            rule("profile:f:unique", "unique", field="f"),
        )
        self.assertEqual(
            candidates["profile:f:range"],
            rule("profile:f:range", "range", field="f", min=1, max=3),
        )
        self.assertEqual(
            candidates["profile:f:allowed_values"],
            rule(
                "profile:f:allowed_values",
                "allowed_values",
                field="f",
                values=[1, 2, 3],
            ),
        )

    def test_no_required_when_all_missing_or_null(self):
        for records in ([{}], [{"f": None}], [{}, {"f": None}]):
            with self.subTest(records=records):
                result = profile_records(records, ["f"])
                self.assertNotIn("profile:f:required", by_id(result))

    def test_required_when_any_non_null_value(self):
        result = profile_records([{"f": None}, {"f": 0}, {}], ["f"])
        self.assertIn("profile:f:required", by_id(result))
        # False/0/empty-string still count as present non-null values.
        result = profile_records([{"f": False}], ["f"])
        self.assertIn("profile:f:required", by_id(result))

    def test_unique_needs_two_distinct_non_null_values(self):
        result = profile_records([{"f": 1}, {"f": 1}], ["f"])
        self.assertNotIn("profile:f:unique", by_id(result))
        result = profile_records([{"f": 1}, {"f": None}], ["f"])
        self.assertNotIn("profile:f:unique", by_id(result))
        result = profile_records([{"f": 1}, {"f": True}], ["f"])
        self.assertIn("profile:f:unique", by_id(result))

    def test_range_blocked_by_missing_null_boolean_or_string(self):
        for records in (
            [{"f": 1}, {}],
            [{"f": 1}, {"f": None}],
            [{"f": 1}, {"f": True}],
            [{"f": 1}, {"f": "2"}],
        ):
            with self.subTest(records=records):
                result = profile_records(records, ["f"])
                self.assertNotIn("profile:f:range", by_id(result))

    def test_range_min_max_follow_data(self):
        result = profile_records([{"f": 5}, {"f": 5}], ["f"])
        range_rule = by_id(result)["profile:f:range"]
        self.assertEqual(range_rule["options"]["min"], 5)
        self.assertEqual(range_rule["options"]["max"], 5)
        # Equal values are not unique but still rangeable.
        self.assertNotIn("profile:f:unique", by_id(result))

    def test_allowed_values_includes_null_in_first_appearance_order(self):
        records = [
            {"f": "b"},
            {"f": None},
            {"f": "a"},
            {"f": "b"},
            {"f": None},
        ]
        result = profile_records(records, ["f"])
        candidate = by_id(result)["profile:f:allowed_values"]
        self.assertEqual(candidate["options"]["values"], ["b", None, "a"])

    def test_allowed_values_blocked_by_missing(self):
        result = profile_records([{"f": "a"}, {}], ["f"])
        self.assertNotIn("profile:f:allowed_values", by_id(result))

    def test_allowed_values_limited_to_twenty_distinct(self):
        result = profile_records(
            [{"f": i} for i in range(20)], ["f"]
        )
        self.assertIn("profile:f:allowed_values", by_id(result))
        result = profile_records(
            [{"f": i} for i in range(21)], ["f"]
        )
        self.assertNotIn("profile:f:allowed_values", by_id(result))

    def test_candidates_follow_field_order(self):
        result = profile_records(
            [{"a": 1, "b": 2}], ["b", "a"]
        )
        fields = [c["options"]["field"] for c in result["rule_candidates"]]
        self.assertEqual(fields[0], "b")
        self.assertEqual(fields[-1], "a")

    def test_candidates_are_valid_validate_rules(self):
        records = [
            {"id": "r1", "age": 1},
            {"id": "r2", "age": 42},
            {"id": "r3", "age": 7},
        ]
        result = profile_records(records)
        validated = validate(records, result["rule_candidates"])
        self.assertIn("passed", validated)
        self.assertEqual(
            validated["summary"]["checked_rule_count"],
            len(result["rule_candidates"]),
        )


class InputValidationTest(unittest.TestCase):
    def assert_invalid(self, records, fields=None):
        with self.assertRaises(InvalidProfileInputError) as ctx:
            profile_records(records, fields)
        self.assertEqual(ctx.exception.code, "INVALID_PROFILE_INPUT")

    def test_records_must_be_list_of_objects(self):
        self.assert_invalid({})
        self.assert_invalid("nope")
        self.assert_invalid([1])
        self.assert_invalid([["x"]])
        self.assert_invalid([{"a": 1}, "x"])

    def test_fields_must_be_unique_non_empty_strings(self):
        self.assert_invalid([], {})
        self.assert_invalid([], "")
        self.assert_invalid([], [1])
        self.assert_invalid([], [""])
        self.assert_invalid([], [None])
        self.assert_invalid([{"a": 1}], ["a", "a"])

    def test_no_partial_profile_and_input_not_mutated(self):
        records = [{"a": 1}, {"a": None}, {"b": "x"}]
        snapshot = copy.deepcopy(records)
        result = profile_records(records)
        self.assertEqual(records, snapshot)
        self.assertEqual(set(result), {"record_count", "fields", "rule_candidates"})
        self.assertEqual(result["record_count"], 3)


if __name__ == "__main__":
    unittest.main()
