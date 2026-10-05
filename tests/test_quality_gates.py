"""Unit tests for dataset-level ratio quality gates."""

import unittest

from data_quality import (
    InvalidInputError,
    InvalidQualityGateRuleError,
    InvalidRuleError,
    UnknownQualityGateSourceError,
    evaluate_quality_gates,
)


def gate(
    rule_id,
    source_rule_id,
    max_failed_ratio=0.0,
    severity="error",
):
    return {
        "rule_id": rule_id,
        "source_rule_id": source_rule_id,
        "max_failed_ratio": max_failed_ratio,
        "severity": severity,
    }


RECORDS = [
    {"id": "r1", "age": 5},
    {"id": "r2", "age": 200},
    {"id": "r3", "age": None},
    {"age": 10},
]

RULES = [
    {"id": "age-req", "type": "required", "options": {"field": "age"}},
    {
        "id": "age-range",
        "type": "range",
        "options": {"field": "age", "min": 0, "max": 120},
    },
]


class EvaluateQualityGatesSuccessTest(unittest.TestCase):
    def test_passed_failed_and_report_shape(self):
        result = evaluate_quality_gates(
            "dwd_orders",
            RECORDS,
            RULES,
            [
                gate("g-passed", "age-req", max_failed_ratio=0.25),
                gate("g-failed", "age-range", max_failed_ratio=0.1),
            ],
        )
        self.assertEqual(result["dataset"], "dwd_orders")
        self.assertEqual(result["record_count"], 4)
        self.assertEqual(
            result["results"],
            [
                {
                    "rule_id": "g-passed",
                    "source_rule_id": "age-req",
                    "status": "PASSED",
                    "failed_count": 1,
                    "record_count": 4,
                    "ratio": 0.25,
                    "samples": [
                        {
                            "record_index": 2,
                            "record_id": "r3",
                            "field": "age",
                            "value": None,
                            "message": "is required but is missing or null",
                        }
                    ],
                },
                {
                    "rule_id": "g-failed",
                    "source_rule_id": "age-range",
                    "status": "FAILED",
                    "failed_count": 2,
                    "record_count": 4,
                    "ratio": 0.5,
                    "samples": [
                        {
                            "record_index": 1,
                            "record_id": "r2",
                            "field": "age",
                            "value": 200,
                            "message": "is out of the allowed range",
                        },
                        {
                            "record_index": 2,
                            "record_id": "r3",
                            "field": "age",
                            "value": None,
                            "message": "is out of the allowed range",
                        },
                    ],
                },
            ],
        )

    def test_ratio_equal_to_threshold_is_passed(self):
        records = [{"id": "r1", "f": None}, {"id": "r2", "f": 1}]
        result = evaluate_quality_gates(
            "d", records, [{"id": "rule-1", "type": "required",
                            "options": {"field": "f"}}],
            [gate("g1", "rule-1", max_failed_ratio=0.5)],
        )
        self.assertEqual(result["results"][0]["status"], "PASSED")
        self.assertEqual(result["results"][0]["ratio"], 0.5)

    def test_zero_failures_passes_with_ratio_zero(self):
        result = evaluate_quality_gates(
            "d", [{"id": "r1", "age": 5}], RULES,
            [gate("g1", "age-req", max_failed_ratio=0.0)],
        )
        gate_result = result["results"][0]
        self.assertEqual(gate_result["status"], "PASSED")
        self.assertEqual(gate_result["failed_count"], 0)
        self.assertEqual(gate_result["ratio"], 0.0)
        self.assertEqual(gate_result["samples"], [])

    def test_all_records_fail_ratio_one(self):
        records = [{"id": "r1"}, {"id": "r2"}]
        result = evaluate_quality_gates(
            "d", records, [{"id": "rule-1", "type": "required",
                            "options": {"field": "age"}}],
            [gate("g1", "rule-1", max_failed_ratio=1.0)],
        )
        gate_result = result["results"][0]
        self.assertEqual(gate_result["status"], "PASSED")
        self.assertEqual(gate_result["ratio"], 1.0)
        self.assertEqual(gate_result["failed_count"], 2)

    def test_results_keep_gate_order(self):
        result = evaluate_quality_gates(
            "d", RECORDS, RULES,
            [
                gate("g2", "age-range", max_failed_ratio=0.9),
                gate("g1", "age-req", max_failed_ratio=0.0),
            ],
        )
        self.assertEqual(
            [item["rule_id"] for item in result["results"]], ["g2", "g1"]
        )

    def test_samples_keep_record_order(self):
        records = [
            {"id": "r1", "age": None},
            {"id": "r2", "age": 1},
            {"id": "r3", "age": None},
            {"id": "r4", "age": 2},
            {"id": "r5", "age": None},
        ]
        result = evaluate_quality_gates(
            "d", records, [{"id": "rule-1", "type": "required",
                            "options": {"field": "age"}}],
            [gate("g1", "rule-1", max_failed_ratio=0.1)],
        )
        samples = result["results"][0]["samples"]
        self.assertEqual(
            [sample["record_index"] for sample in samples], [0, 2, 4]
        )
        self.assertEqual(
            [sample["record_id"] for sample in samples],
            ["r1", "r3", "r5"],
        )

    def test_sample_has_exactly_five_keys(self):
        result = evaluate_quality_gates(
            "d", RECORDS, RULES,
            [gate("g1", "age-req", max_failed_ratio=0.0)],
        )
        sample = result["results"][0]["samples"][0]
        self.assertEqual(
            set(sample),
            {"record_index", "record_id", "field", "value", "message"},
        )

    def test_record_id_null_when_record_has_no_id(self):
        records = [{"age": None}]
        result = evaluate_quality_gates(
            "d", records, RULES,
            [gate("g1", "age-req", max_failed_ratio=0.0)],
        )
        self.assertIsNone(result["results"][0]["samples"][0]["record_id"])

    def test_every_severity_accepted(self):
        for severity in ("error", "warning", "info"):
            result = evaluate_quality_gates(
                "d", [{"age": 1}], RULES,
                [gate("g1", "age-req", max_failed_ratio=0.0,
                      severity=severity)],
            )
            self.assertEqual(result["results"][0]["status"], "PASSED")

    def test_threshold_boundaries_zero_and_one_are_numbers(self):
        for ratio in (0, 1, 0.0, 1.0):
            result = evaluate_quality_gates(
                "d", [{"age": 1}], RULES,
                [gate("g1", "age-req", max_failed_ratio=ratio)],
            )
            self.assertEqual(result["results"][0]["status"], "PASSED")


class EvaluateQualityGatesEmptyDatasetTest(unittest.TestCase):
    def test_empty_records_skips_every_gate(self):
        result = evaluate_quality_gates(
            "dwd", [], RULES,
            [
                gate("g1", "age-req", max_failed_ratio=0.0),
                gate("g2", "age-range", max_failed_ratio=0.5,
                     severity="warning"),
            ],
        )
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(len(result["results"]), 2)
        for item in result["results"]:
            self.assertEqual(item["status"], "SKIPPED_EMPTY_DATASET")
            self.assertEqual(item["failed_count"], 0)
            self.assertEqual(item["record_count"], 0)
            self.assertIsNone(item["ratio"])
            self.assertEqual(item["samples"], [])

    def test_empty_records_keeps_gate_order_and_identity(self):
        result = evaluate_quality_gates(
            "dwd", [], RULES,
            [gate("g2", "age-range"), gate("g1", "age-req")],
        )
        self.assertEqual(
            [item["rule_id"] for item in result["results"]], ["g2", "g1"]
        )
        self.assertEqual(
            [item["source_rule_id"] for item in result["results"]],
            ["age-range", "age-req"],
        )

    def test_empty_records_unknown_source_still_rejected(self):
        with self.assertRaises(UnknownQualityGateSourceError):
            evaluate_quality_gates(
                "d", [], RULES, [gate("g1", "no-such-rule")]
            )

    def test_empty_records_malformed_gate_still_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], RULES,
                [gate("g1", "age-req", max_failed_ratio=2.0)],
            )


class EvaluateQualityGatesRuleErrorTest(unittest.TestCase):
    def test_gates_not_list(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, {})

    def test_gate_not_object(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, ["g1"])

    def test_gate_missing_key(self):
        bad = gate("g1", "age-req")
        del bad["severity"]
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, [bad])

    def test_gate_extra_key(self):
        bad = gate("g1", "age-req")
        bad["extra"] = 1
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, [bad])

    def test_rule_id_empty(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES, [gate("", "age-req")]
            )

    def test_rule_id_non_string(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES, [gate(1, "age-req")]
            )

    def test_duplicate_gate_rule_id(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req"), gate("g1", "age-range")],
            )

    def test_source_rule_id_empty(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, [gate("g1", "")])

    def test_source_rule_id_non_string(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, [gate("g1", 5)])

    def test_invalid_severity_value(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req", severity="fatal")],
            )

    def test_severity_non_string(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req", severity=None)],
            )

    def test_ratio_bool_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req", max_failed_ratio=True)],
            )

    def test_ratio_non_number(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req", max_failed_ratio="0.1")],
            )

    def test_ratio_below_zero(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req", max_failed_ratio=-0.01)],
            )

    def test_ratio_above_one(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", RECORDS, RULES,
                [gate("g1", "age-req", max_failed_ratio=1.01)],
            )

    def test_unknown_source_rule_id(self):
        with self.assertRaises(UnknownQualityGateSourceError):
            evaluate_quality_gates(
                "d", RECORDS, RULES, [gate("g1", "missing-rule")]
            )

    def test_unknown_source_is_lookup_error(self):
        self.assertTrue(issubclass(UnknownQualityGateSourceError, LookupError))
        self.assertTrue(
            issubclass(InvalidQualityGateRuleError, ValueError)
        )

    def test_structure_checked_before_unknown_source(self):
        bad = gate("g1", "missing-rule", max_failed_ratio=2.0)
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", RECORDS, RULES, [bad])


class EvaluateQualityGatesDelegatedErrorTest(unittest.TestCase):
    def test_records_not_list(self):
        with self.assertRaises(InvalidInputError):
            evaluate_quality_gates("d", {}, RULES, [])

    def test_record_not_object(self):
        with self.assertRaises(InvalidInputError):
            evaluate_quality_gates("d", [1], RULES, [])

    def test_bad_rules_raise_invalid_rule_error(self):
        with self.assertRaises(InvalidRuleError):
            evaluate_quality_gates(
                "d", RECORDS, [{"id": "bad"}], []
            )

    def test_bad_rules_take_precedence_over_bad_gates(self):
        with self.assertRaises(InvalidRuleError):
            evaluate_quality_gates(
                "d", RECORDS, [{"id": "bad"}],
                [gate("g1", "bad", max_failed_ratio=2.0)],
            )

    def test_bad_records_take_precedence_over_bad_gates(self):
        with self.assertRaises(InvalidInputError):
            evaluate_quality_gates(
                "d", [1], RULES,
                [gate("g1", "age-req", max_failed_ratio=2.0)],
            )


if __name__ == "__main__":
    unittest.main()
