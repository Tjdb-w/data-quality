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


def composite_rule(
    rule_id,
    source_dataset="order_items",
    source_fields=("order_id", "line_no"),
    target_dataset="shipment_lines",
    target_fields=("order_id", "line_no"),
):
    return {
        "rule_id": rule_id,
        "source_dataset": source_dataset,
        "source_fields": list(source_fields),
        "target_dataset": target_dataset,
        "target_fields": list(target_fields),
    }


COMPOSITE_DATASETS = {
    "shipment_lines": [
        {"id": "s1", "order_id": "o1", "line_no": 1},
        {"id": "s2", "order_id": "o2", "line_no": 1},
        {"id": "s3", "order_id": "o3", "line_no": None},
        {"id": "s4", "order_id": "o4"},
    ],
    "order_items": [
        {"id": "i1", "order_id": "o1", "line_no": 1},
        {"id": "i2", "order_id": "o9", "line_no": 9},
        {"id": "i3", "order_id": "o2", "line_no": None},
        {"id": "i4", "line_no": 1},
        {"id": "i5", "order_id": None, "line_no": None},
        {"id": "i6"},
    ],
}


class ValidateCompositeReferencesSuccessTest(unittest.TestCase):
    def test_violation_shapes_and_summary(self):
        result = validate_references(
            COMPOSITE_DATASETS, [composite_rule("ref-c")]
        )
        self.assertEqual(
            result,
            {
                "passed": False,
                "summary": {
                    "dataset_count": 2,
                    "rule_count": 1,
                    "checked_value_count": 4,
                    "violation_count": 3,
                },
                "violations": [
                    {
                        "rule_id": "ref-c",
                        "source": {
                            "dataset": "order_items",
                            "record_index": 1,
                            "record_id": "i2",
                            "field": ["order_id", "line_no"],
                            "value": ["o9", 9],
                        },
                        "target": {
                            "dataset": "shipment_lines",
                            "field": ["order_id", "line_no"],
                        },
                        "message": "has no matching target value",
                    },
                    {
                        "rule_id": "ref-c",
                        "source": {
                            "dataset": "order_items",
                            "record_index": 2,
                            "record_id": "i3",
                            "field": ["order_id", "line_no"],
                            "value": ["o2", None],
                        },
                        "target": {
                            "dataset": "shipment_lines",
                            "field": ["order_id", "line_no"],
                        },
                        "message": "has an incomplete composite reference",
                    },
                    {
                        "rule_id": "ref-c",
                        "source": {
                            "dataset": "order_items",
                            "record_index": 3,
                            "record_id": "i4",
                            "field": ["order_id", "line_no"],
                            "value": [None, 1],
                        },
                        "target": {
                            "dataset": "shipment_lines",
                            "field": ["order_id", "line_no"],
                        },
                        "message": "has an incomplete composite reference",
                    },
                ],
            },
        )

    def test_passed_when_all_composite_keys_match(self):
        datasets = {
            "t": [
                {"a": 1, "b": "x"},
                {"a": 1, "b": "y"},
            ],
            "s": [
                {"a": 1, "b": "x"},
                {"a": 1, "b": "y"},
                {"a": None, "b": None},
                {},
            ],
        }
        result = validate_references(
            datasets, [composite_rule("r1", "s", ("a", "b"), "t", ("a", "b"))]
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["checked_value_count"], 2)

    def test_composite_tuple_matches_positionally(self):
        datasets = {
            "t": [{"a": 1, "b": 2}],
            "s": [{"a": 2, "b": 1}],
        }
        result = validate_references(
            datasets, [composite_rule("r1", "s", ("a", "b"), "t", ("a", "b"))]
        )
        self.assertEqual(result["summary"]["violation_count"], 1)

    def test_composite_json_equality_bool_is_not_number(self):
        datasets = {
            "t": [{"a": 1, "b": 2}],
            "s": [{"a": True, "b": 2}],
        }
        result = validate_references(
            datasets, [composite_rule("r1", "s", ("a", "b"), "t", ("a", "b"))]
        )
        self.assertEqual(result["summary"]["violation_count"], 1)

    def test_composite_json_equality_containers(self):
        datasets = {
            "t": [{"a": {"x": [1, 2]}, "b": 2}],
            "s": [{"a": {"x": [1, 2]}, "b": 2}],
        }
        result = validate_references(
            datasets, [composite_rule("r1", "s", ("a", "b"), "t", ("a", "b"))]
        )
        self.assertTrue(result["passed"])

    def test_target_record_with_null_component_never_matches(self):
        datasets = {
            "t": [{"a": 1, "b": None}],
            "s": [{"a": 1, "b": 3}],
        }
        result = validate_references(
            datasets, [composite_rule("r1", "s", ("a", "b"), "t", ("a", "b"))]
        )
        self.assertEqual(result["summary"]["violation_count"], 1)

    def test_mixed_rule_kinds_execute_in_array_order(self):
        datasets = {
            "a": [{"f": 1, "g": 2}],
            "b": [{"h": 3, "i": 4}],
        }
        rules = [
            composite_rule("r2", "a", ("f", "g"), "b", ("h", "i")),
            rule("r1", "a", "f", "b", "h"),
        ]
        result = validate_references(datasets, rules)
        self.assertEqual(
            [v["rule_id"] for v in result["violations"]], ["r2", "r1"]
        )

    def test_duplicate_rule_id_across_rule_kinds(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references(
                {}, [rule("r1"), composite_rule("r1")]
            )


class ValidateCompositeReferencesRuleErrorTest(unittest.TestCase):
    def test_mixed_rule_shape(self):
        bad = rule("r1")
        bad["source_fields"] = ["a"]
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_composite_missing_key(self):
        bad = composite_rule("r1")
        del bad["target_fields"]
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_composite_extra_key(self):
        bad = composite_rule("r1")
        bad["extra"] = 1
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_fields_not_array(self):
        bad = composite_rule("r1", source_fields="order_id")
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_fields_empty_array(self):
        bad = composite_rule("r1", source_fields=[], target_fields=[])
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_fields_empty_string_element(self):
        bad = composite_rule("r1", source_fields=("a", ""))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_fields_non_string_element(self):
        bad = composite_rule("r1", target_fields=("a", 1))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_fields_duplicate_element(self):
        bad = composite_rule("r1", source_fields=("a", "a"))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_fields_length_mismatch(self):
        bad = composite_rule("r1", source_fields=("a", "b"), target_fields=("a",))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_composite_empty_dataset_name(self):
        bad = composite_rule("r1", source_dataset="")
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_composite_unknown_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references(
                {"order_items": []}, [composite_rule("r1")]
            )


if __name__ == "__main__":
    unittest.main()
