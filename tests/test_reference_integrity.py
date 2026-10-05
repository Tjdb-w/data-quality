"""Unit tests for cross-dataset reference integrity validation."""

import unittest

from data_quality.reference_integrity import (
    InvalidReferenceInputError,
    InvalidReferenceRuleError,
    UnknownReferenceDatasetError,
    validate_references,
)


def rule(
    rule_id,
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


DATASETS = {
    "customers": [
        {"id": "c1", "name": "甲"},
        {"id": "c2", "name": "乙"},
    ],
    "orders": [
        {"id": "o1", "customer_id": "c1"},
        {"id": "o2", "customer_id": "c9"},
        {"id": "o3", "customer_id": None},
        {"id": "o4"},
    ],
}


class ValidateReferencesSuccessTest(unittest.TestCase):
    def test_violation_shape_and_summary(self):
        result = validate_references(DATASETS, [rule("ref-1")])
        self.assertEqual(
            result,
            {
                "passed": False,
                "summary": {
                    "dataset_count": 2,
                    "rule_count": 1,
                    "checked_value_count": 2,
                    "violation_count": 1,
                },
                "violations": [
                    {
                        "rule_id": "ref-1",
                        "source": {
                            "dataset": "orders",
                            "record_index": 1,
                            "record_id": "o2",
                            "field": "customer_id",
                            "value": "c9",
                        },
                        "target": {"dataset": "customers", "field": "id"},
                        "message": "has no matching target value",
                    }
                ],
            },
        )

    def test_passed_when_all_match(self):
        datasets = {
            "customers": [{"id": "c1"}, {"id": "c2"}],
            "orders": [
                {"customer_id": "c1"},
                {"customer_id": "c2"},
                {"customer_id": None},
                {},
            ],
        }
        result = validate_references(datasets, [rule("ref-1")])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["checked_value_count"], 2)

    def test_record_id_defaults_to_null(self):
        datasets = {
            "customers": [{"id": "c1"}],
            "orders": [{"customer_id": "nope"}],
        }
        result = validate_references(datasets, [rule("ref-1")])
        self.assertIsNone(result["violations"][0]["source"]["record_id"])

    def test_rules_execute_in_array_order(self):
        datasets = {
            "a": [{"f": 1}],
            "b": [{"g": 2}],
            "c": [{"h": 3}],
        }
        rules = [
            rule("r2", "b", "g", "a", "f"),
            rule("r1", "a", "f", "c", "h"),
        ]
        result = validate_references(datasets, rules)
        self.assertEqual(
            [v["rule_id"] for v in result["violations"]], ["r2", "r1"]
        )

    def test_json_equality_bool_is_not_number(self):
        datasets = {
            "a": [{"f": True}],
            "b": [{"g": 1}],
        }
        result = validate_references(
            datasets, [rule("r1", "a", "f", "b", "g")]
        )
        self.assertEqual(result["summary"]["violation_count"], 1)

    def test_json_equality_containers(self):
        datasets = {
            "a": [{"f": {"x": [1, 2]}}],
            "b": [{"g": {"x": [1, 2]}}, {"g": {"x": [2, 1]}}],
        }
        result = validate_references(
            datasets, [rule("r1", "a", "f", "b", "g")]
        )
        self.assertTrue(result["passed"])

    def test_target_record_missing_field_never_matches(self):
        datasets = {
            "a": [{"f": None}],
            "b": [{"other": 1}],
        }
        result = validate_references(
            datasets, [rule("r1", "a", "f", "b", "g")]
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["checked_value_count"], 0)

    def test_zero_source_value_is_checked(self):
        datasets = {
            "a": [{"f": 0}],
            "b": [{"g": 1}],
        }
        result = validate_references(
            datasets, [rule("r1", "a", "f", "b", "g")]
        )
        self.assertEqual(result["summary"]["checked_value_count"], 1)
        self.assertEqual(result["summary"]["violation_count"], 1)


class ValidateReferencesInputErrorTest(unittest.TestCase):
    def test_datasets_not_object(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references([], [])

    def test_dataset_not_array(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references({"a": {}}, [])

    def test_record_not_object(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references({"a": [[1]]}, [])

    def test_rules_not_array(self):
        with self.assertRaises(InvalidReferenceInputError):
            validate_references({}, {})


class ValidateReferencesRuleErrorTest(unittest.TestCase):
    def test_rule_not_object(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, ["r1"])

    def test_rule_missing_key(self):
        bad = rule("r1")
        del bad["target_field"]
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_rule_extra_key(self):
        bad = rule("r1")
        bad["extra"] = 1
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_rule_empty_field(self):
        bad = rule("r1", source_field="")
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_rule_non_string_field(self):
        bad = rule("r1", target_dataset=None)
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_duplicate_rule_id(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references(
                {}, [rule("r1"), rule("r1", source_field="other")]
            )


class ValidateReferencesUnknownDatasetTest(unittest.TestCase):
    def test_unknown_source_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references(
                {"customers": []}, [rule("r1", source_dataset="nope")]
            )

    def test_unknown_target_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references(
                {"orders": []}, [rule("r1", target_dataset="nope")]
            )


if __name__ == "__main__":
    unittest.main()
