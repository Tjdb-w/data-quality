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


def composite_rule(
    rule_id,
    source_dataset="order_lines",
    source_fields=("tenant", "sku"),
    target_dataset="products",
    target_fields=("tenant_id", "sku"),
):
    return {
        "rule_id": rule_id,
        "source_dataset": source_dataset,
        "source_fields": list(source_fields),
        "target_dataset": target_dataset,
        "target_fields": list(target_fields),
    }


COMPOSITE_DATASETS = {
    "products": [
        {"id": "p1", "tenant_id": "t1", "sku": "a"},
        {"id": "p2", "tenant_id": "t2", "sku": "b"},
    ],
    "order_lines": [
        {"id": "l1", "tenant": "t1", "sku": "a"},
        {"id": "l2", "tenant": "t9", "sku": "a"},
        {"id": "l3", "tenant": None, "sku": None},
        {"id": "l4"},
        {"id": "l5", "tenant": "t2"},
    ],
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


class ValidateCompositeReferencesSuccessTest(unittest.TestCase):
    def test_violation_shapes_and_summary(self):
        result = validate_references(
            COMPOSITE_DATASETS, [composite_rule("ref-c1")]
        )
        self.assertEqual(
            result,
            {
                "passed": False,
                "summary": {
                    "dataset_count": 2,
                    "rule_count": 1,
                    "checked_value_count": 3,
                    "violation_count": 2,
                },
                "violations": [
                    {
                        "rule_id": "ref-c1",
                        "source": {
                            "dataset": "order_lines",
                            "record_index": 1,
                            "record_id": "l2",
                            "field": ["tenant", "sku"],
                            "value": ["t9", "a"],
                        },
                        "target": {
                            "dataset": "products",
                            "field": ["tenant_id", "sku"],
                        },
                        "message": "has no matching target value",
                    },
                    {
                        "rule_id": "ref-c1",
                        "source": {
                            "dataset": "order_lines",
                            "record_index": 4,
                            "record_id": "l5",
                            "field": ["tenant", "sku"],
                            "value": ["t2", None],
                        },
                        "target": {
                            "dataset": "products",
                            "field": ["tenant_id", "sku"],
                        },
                        "message": "has an incomplete composite reference",
                    },
                ],
            },
        )

    def test_passed_when_all_tuples_match(self):
        datasets = {
            "products": [
                {"tenant_id": "t1", "sku": "a"},
                {"tenant_id": "t1", "sku": "b"},
            ],
            "order_lines": [
                {"tenant": "t1", "sku": "a"},
                {"tenant": "t1", "sku": "b"},
                {"tenant": None},
                {},
            ],
        }
        result = validate_references(datasets, [composite_rule("ref-c1")])
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["checked_value_count"], 2)

    def test_incomplete_key_fills_missing_entries_with_null(self):
        datasets = {
            "products": [{"tenant_id": "t1", "sku": "a"}],
            "order_lines": [{"sku": "a"}],
        }
        result = validate_references(datasets, [composite_rule("ref-c1")])
        violation = result["violations"][0]
        self.assertEqual(
            violation["message"], "has an incomplete composite reference"
        )
        self.assertEqual(violation["source"]["value"], [None, "a"])
        self.assertIsNone(violation["source"]["record_id"])

    def test_incomplete_key_is_not_matched_against_target(self):
        datasets = {
            "products": [{"tenant_id": None, "sku": "a"}],
            "order_lines": [{"sku": "a"}],
        }
        result = validate_references(datasets, [composite_rule("ref-c1")])
        self.assertEqual(
            result["violations"][0]["message"],
            "has an incomplete composite reference",
        )

    def test_target_record_with_missing_or_null_field_never_matches(self):
        datasets = {
            "products": [
                {"tenant_id": "t1"},
                {"tenant_id": None, "sku": "a"},
            ],
            "order_lines": [{"tenant": "t1", "sku": "a"}],
        }
        result = validate_references(datasets, [composite_rule("ref-c1")])
        self.assertEqual(result["summary"]["violation_count"], 1)
        self.assertEqual(
            result["violations"][0]["message"],
            "has no matching target value",
        )

    def test_tuple_positions_are_compared_in_declared_order(self):
        datasets = {
            "products": [{"tenant_id": "b", "sku": "t1"}],
            "order_lines": [{"tenant": "t1", "sku": "b"}],
        }
        result = validate_references(
            datasets,
            [composite_rule("ref-c1", target_fields=("sku", "tenant_id"))],
        )
        self.assertTrue(result["passed"])

    def test_json_equality_bool_is_not_number_in_tuple(self):
        datasets = {
            "products": [{"tenant_id": 1, "sku": "a"}],
            "order_lines": [{"tenant": True, "sku": "a"}],
        }
        result = validate_references(datasets, [composite_rule("ref-c1")])
        self.assertEqual(result["summary"]["violation_count"], 1)

    def test_json_equality_containers_in_tuple(self):
        datasets = {
            "products": [{"tenant_id": {"x": [1, 2]}, "sku": "a"}],
            "order_lines": [{"tenant": {"x": [1, 2]}, "sku": "a"}],
        }
        result = validate_references(datasets, [composite_rule("ref-c1")])
        self.assertTrue(result["passed"])

    def test_mixed_rule_shapes_execute_in_array_order(self):
        datasets = {
            "a": [{"f": 1, "g": 2}],
            "b": [{"h": 3}],
            "c": [{"i": 4, "j": 5}],
        }
        rules = [
            composite_rule(
                "r2", "a", ("f", "g"), "c", ("i", "j")
            ),
            rule("r1", "a", "f", "b", "h"),
        ]
        result = validate_references(datasets, rules)
        self.assertEqual(
            [v["rule_id"] for v in result["violations"]], ["r2", "r1"]
        )
        self.assertEqual(result["summary"]["rule_count"], 2)
        self.assertEqual(result["summary"]["checked_value_count"], 2)


class ValidateCompositeReferencesRuleErrorTest(unittest.TestCase):
    def test_mixed_rule_shape(self):
        bad = composite_rule("r1")
        bad["source_field"] = "tenant"
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_mixed_rule_shape_reverse(self):
        bad = rule("r1")
        bad["target_fields"] = ["id"]
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_source_fields_not_array(self):
        bad = composite_rule("r1", source_fields="tenant")
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_field_array_empty(self):
        bad = composite_rule("r1", source_fields=(), target_fields=())
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_field_arrays_of_unequal_length(self):
        bad = composite_rule("r1", target_fields=("tenant_id",))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_field_array_duplicate_entries(self):
        bad = composite_rule("r1", source_fields=("tenant", "tenant"))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_field_array_empty_string_entry(self):
        bad = composite_rule("r1", source_fields=("tenant", ""))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_field_array_non_string_entry(self):
        bad = composite_rule("r1", target_fields=("tenant_id", None))
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references({}, [bad])

    def test_duplicate_rule_id_across_shapes(self):
        with self.assertRaises(InvalidReferenceRuleError):
            validate_references(
                {}, [rule("r1"), composite_rule("r1")]
            )


class ValidateCompositeReferencesUnknownDatasetTest(unittest.TestCase):
    def test_unknown_source_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references(
                {"products": []},
                [composite_rule("r1", source_dataset="nope")],
            )

    def test_unknown_target_dataset(self):
        with self.assertRaises(UnknownReferenceDatasetError):
            validate_references(
                {"order_lines": []},
                [composite_rule("r1", target_dataset="nope")],
            )


if __name__ == "__main__":
    unittest.main()
