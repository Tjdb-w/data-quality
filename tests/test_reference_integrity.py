"""Tests for data_quality.validate_references cross-dataset checks."""

import unittest

from data_quality import (
    InvalidReferenceInputError,
    InvalidReferenceRuleError,
    UnknownReferenceDatasetError,
    validate_references,
)


def ref_rule(
    rule_id="fk",
    source_dataset="orders",
    source_field="customer_id",
    target_dataset="customers",
    target_field="id",
):
    return {
        "rule_id": rule_id,
        "source_dataset": source_dataset,
        "source_field": source_field,
        "target_dataset": target_dataset,
        "target_field": target_field,
    }


class ReferenceValidationTest(unittest.TestCase):
    def test_match_miss_skip_and_counts(self):
        datasets = {
            "orders": [
                {"id": "o1", "customer_id": "c1"},
                {"id": "o2", "customer_id": "cX"},
                {"id": "o3"},
                {"id": "o4", "customer_id": None},
                {"customer_id": "c2"},
            ],
            "customers": [{"id": "c1"}, {"id": "c2"}],
        }
        result = validate_references(datasets, [ref_rule()])

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["summary"],
            {
                "dataset_count": 2,
                "rule_count": 1,
                "checked_value_count": 3,
                "violation_count": 1,
            },
        )
        (violation,) = result["violations"]
        self.assertEqual(
            violation,
            {
                "rule_id": "fk",
                "source": {
                    "dataset": "orders",
                    "record_index": 1,
                    "record_id": "o2",
                    "field": "customer_id",
                    "value": "cX",
                },
                "target": {"dataset": "customers", "field": "id"},
                "message": "has no matching target value",
            },
        )
        # Exactly the documented keys are present.
        self.assertEqual(
            set(violation), {"rule_id", "source", "target", "message"}
        )
        self.assertEqual(
            set(violation["source"]),
            {"dataset", "record_index", "record_id", "field", "value"},
        )
        self.assertEqual(set(violation["target"]), {"dataset", "field"})
        self.assertEqual(set(result["summary"]),
                         {"dataset_count", "rule_count",
                          "checked_value_count", "violation_count"})
        self.assertEqual(set(result), {"passed", "summary", "violations"})

    def test_all_matching_passes(self):
        datasets = {
            "orders": [{"id": "o1", "customer_id": "c1"}],
            "customers": [{"id": "c1"}, {"id": "c2"}],
        }
        result = validate_references(datasets, [ref_rule()])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["checked_value_count"], 1)
        self.assertEqual(result["summary"]["violation_count"], 0)

    def test_record_id_null_without_id(self):
        datasets = {
            "orders": [{"customer_id": 9}],
            "customers": [{"id": "1"}],
        }
        result = validate_references(datasets, [ref_rule()])
        self.assertIsNone(result["violations"][0]["source"]["record_id"])
        self.assertEqual(result["violations"][0]["source"]["record_index"], 0)

    def test_empty_datasets_and_rules(self):
        result = validate_references({}, [])
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["summary"],
            {
                "dataset_count": 0,
                "rule_count": 0,
                "checked_value_count": 0,
                "violation_count": 0,
            },
        )

    def test_rules_run_in_array_order(self):
        # "r-a" references the unknown-missing values first; verify the
        # violations come out grouped by rule order then record order.
        datasets = {
            "orders": [{"id": "o1", "a": "x", "b": "y"}],
            "targets": [{"a": None, "b": None}],
        }
        rules = [
            ref_rule("r-b", source_field="b", target_dataset="targets",
                     target_field="b"),
            ref_rule("r-a", source_field="a", target_dataset="targets",
                     target_field="a"),
        ]
        result = validate_references(datasets, rules)
        self.assertEqual(
            [v["rule_id"] for v in result["violations"]], ["r-b", "r-a"]
        )

    def test_json_equality_semantics(self):
        datasets = {
            "orders": [
                {"id": 1, "val": {"k": 1, "j": [1, 2]}},
                {"id": 2, "val": True},
                {"id": 3, "val": 1},
                {"id": 4, "val": None},
            ],
            "targets": [
                {"v": {"j": [1, 2], "k": 1}},
                {"v": 1},
            ],
        }
        result = validate_references(
            datasets,
            [ref_rule(source_field="val", target_dataset="targets",
                      target_field="v")],
        )
        # Object matches structurally; true must NOT match numeric 1;
        # numeric 1 matches; null is skipped.
        indices = [v["source"]["record_index"] for v in result["violations"]]
        self.assertEqual(indices, [1])
        self.assertIs(result["violations"][0]["source"]["value"], True)
        self.assertEqual(result["summary"]["checked_value_count"], 3)

    def test_missing_target_field_provides_no_match(self):
        datasets = {
            "orders": [{"id": "o1", "customer_id": "c1"}],
            "customers": [{"other": "c1"}],
        }
        result = validate_references(datasets, [ref_rule()])
        self.assertEqual(len(result["violations"]), 1)

    def test_null_target_value_does_not_match_non_null_source(self):
        datasets = {
            "orders": [{"id": "o1", "customer_id": "c1"}],
            "customers": [{"id": None}],
        }
        result = validate_references(datasets, [ref_rule()])
        self.assertEqual(len(result["violations"]), 1)

    def test_multiple_violations_record_order(self):
        datasets = {
            "orders": [
                {"id": "a", "customer_id": "z1"},
                {"id": "b", "customer_id": "c1"},
                {"id": "c", "customer_id": "z2"},
            ],
            "customers": [{"id": "c1"}],
        }
        result = validate_references(datasets, [ref_rule()])
        self.assertEqual(
            [v["source"]["record_index"] for v in result["violations"]],
            [0, 2],
        )

    def test_self_dataset_reference_is_allowed(self):
        datasets = {
            "nodes": [
                {"id": "a", "parent": "a"},
                {"id": "b", "parent": "missing"},
            ],
        }
        result = validate_references(
            datasets,
            [ref_rule(source_dataset="nodes", source_field="parent",
                      target_dataset="nodes", target_field="id")],
        )
        self.assertEqual(len(result["violations"]), 1)
        self.assertEqual(result["violations"][0]["source"]["record_id"], "b")


class InvalidInputTest(unittest.TestCase):
    def test_datasets_not_object(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references([], [])

    def test_dataset_records_not_list(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references({"orders": {}}, [])

    def test_record_not_object(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references({"orders": ["x"]}, [])

    def test_empty_dataset_key(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references({"": []}, [])


class InvalidRuleTest(unittest.TestCase):
    def test_rules_not_list(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({"a": []}, {})

    def test_rule_not_object(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({"a": []}, ["x"])

    def test_missing_key(self):
        rule = ref_rule()
        del rule["target_field"]
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({"a": [], "b": []}, [rule])

    def test_extra_key(self):
        rule = ref_rule()
        rule["extra"] = 1
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({"a": [], "b": []}, [rule])

    def test_empty_field(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references(
                {"a": [], "b": []}, [ref_rule(source_field="")]
            )

    def test_non_string_field(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references(
                {"a": [], "b": []}, [ref_rule(rule_id=7)]
            )

    def test_duplicate_rule_id(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references(
                {"a": [], "b": []}, [ref_rule("dup"), ref_rule("dup")]
            )


class UnknownDatasetTest(unittest.TestCase):
    def test_unknown_source_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references({"b": []}, [ref_rule(source_dataset="a")])

    def test_unknown_target_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references({"a": []}, [ref_rule(target_dataset="b")])

    def test_structural_errors_take_priority(self):
        # A malformed rule is reported even though its dataset would also
        # be unknown.
        rule = ref_rule(source_dataset="a", target_dataset="b")
        del rule["rule_id"]
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [rule])


if __name__ == "__main__":
    unittest.main()
