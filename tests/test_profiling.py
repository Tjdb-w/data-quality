"""Tests for data_quality.profile_records and rule candidates."""

import unittest

from data_quality import InvalidProfileInputError, profile_records, validate


class ProfileStructureTest(unittest.TestCase):
    def test_basic_counts_and_types(self):
        records = [
            {"id": "a", "name": "x", "age": 3, "active": True},
            {"id": "b", "name": None, "age": 42, "active": False, "tags": [1]},
            {"id": "c", "age": 7, "meta": {"k": "v"}},
        ]
        result = profile_records(records)
        self.assertEqual(result["record_count"], 3)
        fields = {p["field"]: p for p in result["fields"]}
        # Default order is top-level key first-appearance order.
        self.assertEqual(
            [p["field"] for p in result["fields"]],
            ["id", "name", "age", "active", "tags", "meta"],
        )

        name = fields["name"]
        self.assertEqual(name["present_count"], 2)
        self.assertEqual(name["missing_count"], 1)
        self.assertEqual(name["null_count"], 1)
        self.assertEqual(name["non_null_count"], 1)
        self.assertEqual(name["distinct_non_null_count"], 1)
        self.assertEqual(
            name["type_counts"],
            {"null": 1, "boolean": 0, "number": 0, "string": 1,
             "array": 0, "object": 0},
        )
        self.assertIsNone(name["numeric_min"])
        self.assertIsNone(name["numeric_max"])

        age = fields["age"]
        self.assertEqual(age["present_count"], 3)
        self.assertEqual(age["missing_count"], 0)
        self.assertEqual(age["null_count"], 0)
        self.assertEqual(age["non_null_count"], 3)
        self.assertEqual(age["distinct_non_null_count"], 3)
        self.assertEqual(age["type_counts"]["number"], 3)
        self.assertEqual(age["numeric_min"], 3)
        self.assertEqual(age["numeric_max"], 42)

        self.assertEqual(fields["active"]["type_counts"]["boolean"], 2)
        self.assertEqual(fields["active"]["type_counts"]["number"], 0)
        self.assertIsNone(fields["active"]["numeric_min"])
        self.assertEqual(fields["tags"]["type_counts"]["array"], 1)
        self.assertEqual(fields["meta"]["type_counts"]["object"], 1)

    def test_explicit_fields_order_and_top_level_only(self):
        records = [
            {"a": 1, "nested": {"inner": 2}},
            {"b": 2, "nested": {"other": 3}},
        ]
        result = profile_records(records, ["nested", "a", "ghost"])
        self.assertEqual(
            [p["field"] for p in result["fields"]],
            ["nested", "a", "ghost"],
        )
        by_field = {p["field"]: p for p in result["fields"]}
        # Nested keys are never profiled; "nested" counts as objects.
        self.assertEqual(by_field["nested"]["type_counts"]["object"], 2)
        ghost = by_field["ghost"]
        self.assertEqual(ghost["present_count"], 0)
        self.assertEqual(ghost["missing_count"], 2)
        self.assertEqual(ghost["null_count"], 0)
        self.assertEqual(ghost["non_null_count"], 0)
        self.assertEqual(ghost["distinct_non_null_count"], 0)
        self.assertEqual(
            ghost["type_counts"],
            {"null": 0, "boolean": 0, "number": 0, "string": 0,
             "array": 0, "object": 0},
        )
        self.assertIsNone(ghost["numeric_min"])
        self.assertIsNone(ghost["numeric_max"])

    def test_empty_records_without_fields(self):
        result = profile_records([])
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["fields"], [])
        self.assertEqual(result["rule_candidates"], [])

    def test_empty_records_with_fields_gives_all_zero_profiles(self):
        result = profile_records([], ["a", "b"])
        self.assertEqual(result["record_count"], 0)
        self.assertEqual([p["field"] for p in result["fields"]], ["a", "b"])
        for profile in result["fields"]:
            self.assertEqual(profile["present_count"], 0)
            self.assertEqual(profile["missing_count"], 0)
            self.assertEqual(profile["null_count"], 0)
            self.assertEqual(profile["non_null_count"], 0)
            self.assertEqual(profile["distinct_non_null_count"], 0)
            self.assertEqual(
                profile["type_counts"],
                {"null": 0, "boolean": 0, "number": 0, "string": 0,
                 "array": 0, "object": 0},
            )
            self.assertIsNone(profile["numeric_min"])
            self.assertIsNone(profile["numeric_max"])
        self.assertEqual(result["rule_candidates"], [])


class NumericProfileTest(unittest.TestCase):
    def test_booleans_are_not_numbers(self):
        result = profile_records([{"v": 1}, {"v": True}])
        profile = result["fields"][0]
        self.assertEqual(profile["type_counts"]["number"], 1)
        self.assertEqual(profile["type_counts"]["boolean"], 1)
        self.assertIsNone(profile["numeric_min"])
        self.assertIsNone(profile["numeric_max"])

    def test_null_among_numbers_clears_min_max(self):
        result = profile_records([{"v": 5}, {"v": None}, {"v": 9}])
        profile = result["fields"][0]
        self.assertEqual(profile["null_count"], 1)
        self.assertEqual(profile["non_null_count"], 2)
        self.assertIsNone(profile["numeric_min"])
        self.assertIsNone(profile["numeric_max"])

    def test_float_min_max(self):
        result = profile_records(
            [{"v": 2.5}, {"v": -0.25}, {"v": 10.0}]
        )
        profile = result["fields"][0]
        self.assertEqual(profile["numeric_min"], -0.25)
        self.assertEqual(profile["numeric_max"], 10.0)


class DistinctCountTest(unittest.TestCase):
    def test_bool_distinct_from_number(self):
        result = profile_records(
            [{"v": 1}, {"v": True}, {"v": 0}, {"v": False}]
        )
        profile = result["fields"][0]
        self.assertEqual(profile["non_null_count"], 4)
        self.assertEqual(profile["distinct_non_null_count"], 4)

    def test_structural_equality(self):
        result = profile_records(
            [
                {"v": {"a": 1, "b": 2}},
                {"v": {"b": 2, "a": 1}},
                {"v": [1, [2, 3]]},
                {"v": [1, [2, 3]]},
                {"v": None},
            ]
        )
        profile = result["fields"][0]
        self.assertEqual(profile["null_count"], 1)
        self.assertEqual(profile["non_null_count"], 4)
        self.assertEqual(profile["distinct_non_null_count"], 2)


class RuleCandidateTest(unittest.TestCase):
    def _ids(self, result, field):
        return [
            rule["id"]
            for rule in result["rule_candidates"]
            if rule["options"]["field"] == field
        ]

    def test_clean_numeric_field_gets_all_four(self):
        records = [{"age": 3}, {"age": 42}]
        result = profile_records(records)
        ids = self._ids(result, "age")
        self.assertEqual(
            ids,
            [
                "profile:age:required",
                "profile:age:unique",
                "profile:age:range",
                "profile:age:allowed_values",
            ],
        )
        rules = result["rule_candidates"]
        self.assertEqual(rules[0], {
            "id": "profile:age:required",
            "type": "required",
            "options": {"field": "age"},
        })
        self.assertEqual(rules[2], {
            "id": "profile:age:range",
            "type": "range",
            "options": {"field": "age", "min": 3, "max": 42},
        })
        self.assertEqual(
            rules[3]["options"]["values"], [3, 42]
        )
        # The candidates are valid, compilable validate rules that pass.
        validated = validate(records, rules)
        self.assertTrue(validated["passed"], validated["violations"])

    def test_required_conditions(self):
        missing = profile_records([{"v": 1}, {}])
        self.assertNotIn("profile:v:required", self._ids(missing, "v"))
        null = profile_records([{"v": 1}, {"v": None}])
        self.assertNotIn("profile:v:required", self._ids(null, "v"))
        ok = profile_records([{"v": 0}, {"v": ""}, {"v": False}])
        self.assertIn("profile:v:required", self._ids(ok, "v"))

    def test_unique_conditions(self):
        # Fewer than two non-null values.
        one = profile_records([{"v": 1}])
        self.assertNotIn("profile:v:unique", self._ids(one, "v"))
        only_nulls = profile_records([{"v": None}, {"v": None}])
        self.assertNotIn("profile:v:unique", self._ids(only_nulls, "v"))
        # Duplicates.
        dupes = profile_records([{"v": 1}, {"v": 1}])
        self.assertNotIn("profile:v:unique", self._ids(dupes, "v"))
        # Nulls do not participate; two distinct non-null values.
        mixed = profile_records([{"v": 1}, {"v": None}, {"v": 2}])
        self.assertIn("profile:v:unique", self._ids(mixed, "v"))

    def test_range_conditions(self):
        # Missing -> no range.
        missing = profile_records([{"v": 1}, {}])
        self.assertNotIn("profile:v:range", self._ids(missing, "v"))
        # Null -> no range.
        null = profile_records([{"v": 1}, {"v": None}])
        self.assertNotIn("profile:v:range", self._ids(null, "v"))
        # Booleans -> no range.
        booleans = profile_records([{"v": 1}, {"v": True}])
        self.assertNotIn("profile:v:range", self._ids(booleans, "v"))
        # Strings -> no range.
        strings = profile_records([{"v": "a"}, {"v": "b"}])
        self.assertNotIn("profile:v:range", self._ids(strings, "v"))

    def test_allowed_values_conditions(self):
        # Missing -> no candidate.
        missing = profile_records([{"v": "a"}, {}])
        self.assertNotIn(
            "profile:v:allowed_values", self._ids(missing, "v")
        )
        # Null is allowed as a value; first-appearance order preserved.
        with_null = profile_records(
            [{"v": None}, {"v": "b"}, {"v": None}, {"v": "a"}, {"v": "b"}]
        )
        rule = next(
            r for r in with_null["rule_candidates"]
            if r["id"] == "profile:v:allowed_values"
        )
        self.assertEqual(rule["options"]["values"], [None, "b", "a"])
        # required must not be generated, but allowed_values still is.
        self.assertNotIn("profile:v:required", self._ids(with_null, "v"))

    def test_allowed_values_over_twenty(self):
        records = [{"v": i} for i in range(21)]
        result = profile_records(records)
        self.assertNotIn(
            "profile:v:allowed_values", self._ids(result, "v")
        )
        # Exactly 20 distinct values still generates the candidate.
        records = [{"v": i} for i in range(20)]
        result = profile_records(records)
        self.assertIn(
            "profile:v:allowed_values", self._ids(result, "v")
        )

    def test_candidates_grouped_by_field_order(self):
        records = [
            {"a": 1, "b": 10},
            {"a": 2, "b": 20},
        ]
        result = profile_records(records, ["b", "a"])
        fields = [r["options"]["field"] for r in result["rule_candidates"]]
        self.assertEqual(fields[:4], ["b", "b", "b", "b"])
        self.assertEqual(fields[4:], ["a", "a", "a", "a"])

    def test_missing_field_only_allows_unique(self):
        # A missing value suppresses required/range/allowed_values but
        # unique needs only two pairwise-distinct non-null values.
        records = [{"v": "a"}, {"v": 1}, {}]
        result = profile_records(records)
        self.assertEqual(self._ids(result, "v"), ["profile:v:unique"])

    def test_fully_mixed_field_generates_nothing(self):
        # Only one non-null value (plus a null and a missing) -> no unique,
        # and every other rule needs no missing values.
        records = [{"v": "a"}, {"v": None}, {}]
        result = profile_records(records)
        self.assertEqual(self._ids(result, "v"), [])


class ProfileInputValidationTest(unittest.TestCase):
    def test_records_not_list(self):
        with self.assertRaises(InvalidProfileInputError):
            profile_records({"a": 1})

    def test_record_not_object(self):
        with self.assertRaises(InvalidProfileInputError):
            profile_records([{"a": 1}, 2])

    def test_fields_not_list(self):
        with self.assertRaises(InvalidProfileInputError):
            profile_records([], "a")

    def test_fields_elements(self):
        with self.assertRaises(InvalidProfileInputError):
            profile_records([], ["a", 1])
        with self.assertRaises(InvalidProfileInputError):
            profile_records([], [""])
        with self.assertRaises(InvalidProfileInputError):
            profile_records([], [None])

    def test_duplicate_fields(self):
        with self.assertRaises(InvalidProfileInputError):
            profile_records([], ["a", "a"])

    def test_error_code(self):
        self.assertEqual(InvalidProfileInputError.code, "INVALID_PROFILE_INPUT")

    def test_input_not_mutated(self):
        records = [{"v": 1}, {"v": None}]
        records[0]["v"] = 1
        snapshot = repr(records)
        profile_records(records)
        self.assertEqual(repr(records), snapshot)


if __name__ == "__main__":
    unittest.main()
