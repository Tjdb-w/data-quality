"""Unit tests for dataset-level quality gate aggregation."""

import unittest

from data_quality import InvalidInputError, InvalidRuleError
from data_quality.exemptions import InvalidExemptionError
from data_quality.quality_gates import (
    InvalidQualityGateRuleError,
    UnknownQualityGateSourceError,
    evaluate_quality_gates,
)


def rule(rule_id="age-range", field="age", minimum=0, maximum=120):
    return {
        "id": rule_id,
        "type": "range",
        "options": {"field": field, "min": minimum, "max": maximum},
    }


def gate(
    rule_id="g1",
    source_rule_id="age-range",
    max_failed_ratio=0.5,
    severity="error",
):
    return {
        "rule_id": rule_id,
        "source_rule_id": source_rule_id,
        "max_failed_ratio": max_failed_ratio,
        "severity": severity,
    }


RECORDS = [
    {"id": "r1", "age": 200},
    {"id": "r2", "age": 50},
    {"id": "r3", "age": 7},
]


class EvaluateQualityGatesSuccessTest(unittest.TestCase):
    def test_report_shape(self):
        result = evaluate_quality_gates(
            "people", RECORDS, [rule()], [gate(max_failed_ratio=0.2)]
        )
        self.assertEqual(
            result,
            {
                "dataset": "people",
                "results": [
                    {
                        "rule_id": "g1",
                        "source_rule_id": "age-range",
                        "status": "FAILED",
                        "failed_count": 1,
                        "record_count": 3,
                        "ratio": 1 / 3,
                        "samples": [
                            {
                                "record_index": 0,
                                "record_id": "r1",
                                "field": "age",
                                "value": 200,
                                "message": "is out of the allowed range",
                            }
                        ],
                    }
                ],
            },
        )

    def test_ratio_equal_to_threshold_is_passed(self):
        # 1 of 2 records fails: ratio 0.5, threshold 0.5 -> PASSED.
        records = [{"id": "r1", "age": 200}, {"id": "r2", "age": 10}]
        passed = evaluate_quality_gates(
            "d", records, [rule()], [gate(max_failed_ratio=0.5)]
        )
        self.assertEqual(passed["results"][0]["status"], "PASSED")
        self.assertEqual(passed["results"][0]["ratio"], 0.5)

        just_above = evaluate_quality_gates(
            "d", records, [rule()], [gate(max_failed_ratio=0.49)]
        )
        self.assertEqual(just_above["results"][0]["status"], "FAILED")

    def test_zero_ratio_passes(self):
        result = evaluate_quality_gates(
            "d", [{"id": "r1", "age": 10}], [rule()], [gate()]
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["failed_count"], 0)
        self.assertEqual(entry["ratio"], 0)
        self.assertEqual(entry["samples"], [])

    def test_threshold_one_passes_even_when_all_fail(self):
        records = [{"age": 999}, {"age": 999}]
        result = evaluate_quality_gates(
            "d", records, [rule()], [gate(max_failed_ratio=1)]
        )
        self.assertEqual(result["results"][0]["status"], "PASSED")
        self.assertEqual(result["results"][0]["ratio"], 1.0)

    def test_threshold_zero_any_failure_fails(self):
        records = [{"age": 1}, {"age": 999}]
        result = evaluate_quality_gates(
            "d", records, [rule()], [gate(max_failed_ratio=0)]
        )
        self.assertEqual(result["results"][0]["status"], "FAILED")

    def test_empty_dataset_is_skipped(self):
        result = evaluate_quality_gates("empty", [], [rule()], [gate()])
        entry = result["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_EMPTY_DATASET")
        self.assertEqual(entry["record_count"], 0)
        self.assertEqual(entry["failed_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["samples"], [])

    def test_gates_aggregated_in_order_with_different_sources(self):
        rules = [
            {"id": "name-req", "type": "required", "options": {"field": "name"}},
            rule(),
        ]
        records = [
            {"id": "r1", "age": 10},
            {"id": "r2", "age": 200},
        ]
        gates = [
            gate("g-age", "age-range", 0.0, "error"),
            gate("g-name", "name-req", 1.0, "info"),
        ]
        result = evaluate_quality_gates("d", records, rules, gates)
        self.assertEqual(
            [entry["rule_id"] for entry in result["results"]],
            ["g-age", "g-name"],
        )
        self.assertEqual(result["results"][0]["failed_count"], 1)
        self.assertEqual(result["results"][1]["failed_count"], 2)

    def test_samples_follow_record_order_and_keep_fields(self):
        records = [
            {"age": 999},
            {"id": "b", "age": 10},
            {"id": "c", "age": 999},
        ]
        result = evaluate_quality_gates(
            "d", records, [rule()], [gate(max_failed_ratio=0.1)]
        )
        samples = result["results"][0]["samples"]
        self.assertEqual([sample["record_index"] for sample in samples], [0, 2])
        self.assertEqual(
            [sample["record_id"] for sample in samples], [None, "c"]
        )
        self.assertTrue(
            all(sample["field"] == "age" for sample in samples)
        )
        self.assertTrue(
            all(sample["value"] == 999 for sample in samples)
        )
        self.assertEqual(
            {
                "record_index",
                "record_id",
                "field",
                "value",
                "message",
            },
            set(samples[0]),
        )

    def test_severity_is_echoed_nowhere_but_accepted(self):
        for severity in ("error", "warning", "info"):
            result = evaluate_quality_gates(
                "d", RECORDS, [rule()], [gate(severity=severity)]
            )
            self.assertEqual(
                set(result["results"][0]),
                {
                    "rule_id",
                    "source_rule_id",
                    "status",
                    "failed_count",
                    "record_count",
                    "ratio",
                    "samples",
                },
            )

    def test_dataset_may_be_none(self):
        result = evaluate_quality_gates(None, RECORDS, [rule()], [gate()])
        self.assertIsNone(result["dataset"])

    def test_unique_rule_violations_are_used_as_defined(self):
        rules = [
            {"id": "code-uq", "type": "unique", "options": {"field": "code"}}
        ]
        records = [
            {"id": "r1", "code": "x"},
            {"id": "r2", "code": "x"},
            {"id": "r3", "code": "x"},
        ]
        result = evaluate_quality_gates(
            "d", records, rules, [gate(source_rule_id="code-uq")]
        )
        entry = result["results"][0]
        # Only the later duplicates are violations under the unique rule.
        self.assertEqual(entry["failed_count"], 2)
        self.assertEqual(
            [sample["record_index"] for sample in entry["samples"]], [1, 2]
        )


class EvaluateQualityGatesInputErrorTest(unittest.TestCase):
    def test_records_not_list(self):
        with self.assertRaises(InvalidInputError):
            evaluate_quality_gates("d", {}, [rule()], [gate()])

    def test_record_not_object(self):
        with self.assertRaises(InvalidInputError):
            evaluate_quality_gates("d", [1], [rule()], [gate()])


class EvaluateQualityGatesRuleErrorTest(unittest.TestCase):
    def test_invalid_rule_definition(self):
        bad_rule = {"id": "r1", "type": "nope", "options": {"field": "x"}}
        with self.assertRaises(InvalidRuleError):
            evaluate_quality_gates("d", [], [bad_rule], [gate()])

    def test_rules_not_list(self):
        with self.assertRaises(InvalidRuleError):
            evaluate_quality_gates("d", [], {}, [gate()])


class EvaluateQualityGatesGateErrorTest(unittest.TestCase):
    def test_gates_not_list(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", [], [rule()], {})

    def test_gate_not_object(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", [], [rule()], ["g1"])

    def test_gate_missing_key(self):
        bad = gate()
        del bad["severity"]
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", [], [rule()], [bad])

    def test_gate_extra_key(self):
        bad = gate()
        bad["extra"] = 1
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates("d", [], [rule()], [bad])

    def test_rule_id_empty(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(rule_id="")]
            )

    def test_rule_id_not_string(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(rule_id=3)]
            )

    def test_duplicate_rule_id(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d",
                [],
                [rule()],
                [gate("g1"), gate("g1", max_failed_ratio=0.1)],
            )

    def test_source_rule_id_empty(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(source_rule_id="")]
            )

    def test_source_rule_id_not_string(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(source_rule_id=4)]
            )

    def test_invalid_severity(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(severity="fatal")]
            )

    def test_severity_not_string(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(severity=1)]
            )

    def test_ratio_boolean_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio=True)]
            )
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio=False)]
            )

    def test_ratio_string_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio="0.5")]
            )

    def test_ratio_none_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio=None)]
            )

    def test_ratio_below_zero_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio=-0.01)]
            )

    def test_ratio_above_one_rejected(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio=1.01)]
            )

    def test_boundary_ratios_accepted(self):
        for boundary in (0, 1):
            result = evaluate_quality_gates(
                "d", [], [rule()], [gate(max_failed_ratio=boundary)]
            )
            self.assertEqual(
                result["results"][0]["status"], "SKIPPED_EMPTY_DATASET"
            )


class EvaluateQualityGatesUnknownSourceTest(unittest.TestCase):
    def test_unknown_source_rule_id(self):
        with self.assertRaises(UnknownQualityGateSourceError):
            evaluate_quality_gates(
                "d", [], [rule()], [gate(source_rule_id="other")]
            )

    def test_unknown_source_checked_before_records(self):
        # An unknown source aborts even if the records are also malformed.
        with self.assertRaises(UnknownQualityGateSourceError):
            evaluate_quality_gates(
                "d", [1], [rule()], [gate(source_rule_id="other")]
            )


def exemption(
    exemption_id="e1",
    rule_id="age-range",
    record_index=0,
    record_id="r1",
    field="age",
    reason="known legacy value",
):
    return {
        "exemption_id": exemption_id,
        "rule_id": rule_id,
        "record_index": record_index,
        "record_id": record_id,
        "field": field,
        "reason": reason,
    }


class EvaluateQualityGatesExemptionTest(unittest.TestCase):
    def test_omitted_exemptions_keep_baseline_shape(self):
        result = evaluate_quality_gates(
            "people", RECORDS, [rule()], [gate(max_failed_ratio=0.2)]
        )
        entry = result["results"][0]
        self.assertEqual(entry["failed_count"], 1)
        self.assertEqual(entry["ratio"], 1 / 3)
        self.assertEqual(entry["status"], "FAILED")
        self.assertNotIn("waived_count", entry)
        self.assertNotIn("waived_samples", entry)

    def test_empty_exemptions_keep_baseline_shape(self):
        omitted = evaluate_quality_gates(
            "people", RECORDS, [rule()], [gate(max_failed_ratio=0.2)]
        )
        emptied = evaluate_quality_gates(
            "people",
            RECORDS,
            [rule()],
            [gate(max_failed_ratio=0.2)],
            exemptions=[],
        )
        self.assertEqual(emptied, omitted)
        entry = emptied["results"][0]
        self.assertNotIn("waived_count", entry)
        self.assertNotIn("waived_samples", entry)

    def test_waived_violation_drops_out_of_ratio_and_status(self):
        result = evaluate_quality_gates(
            "people",
            RECORDS,
            [rule()],
            [gate(max_failed_ratio=0.2)],
            exemptions=[exemption()],
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["failed_count"], 0)
        self.assertEqual(entry["record_count"], 3)
        self.assertEqual(entry["ratio"], 0)
        self.assertEqual(entry["samples"], [])
        self.assertEqual(entry["waived_count"], 1)
        self.assertEqual(
            entry["waived_samples"],
            [
                {
                    "record_index": 0,
                    "record_id": "r1",
                    "field": "age",
                    "value": 200,
                    "message": "is out of the allowed range",
                    "exemption_id": "e1",
                    "reason": "known legacy value",
                }
            ],
        )

    def test_active_and_waived_split_in_record_order(self):
        records = [
            {"id": "r1", "age": 999},
            {"id": "r2", "age": 5},
            {"id": "r3", "age": 999},
        ]
        result = evaluate_quality_gates(
            "d",
            records,
            [rule()],
            [gate(max_failed_ratio=0.0)],
            exemptions=[exemption("e1", record_index=0, record_id="r1")],
        )
        entry = result["results"][0]
        self.assertEqual(entry["failed_count"], 1)
        self.assertEqual(entry["ratio"], 1 / 3)
        self.assertEqual(
            [s["record_index"] for s in entry["samples"]], [2]
        )
        self.assertEqual(entry["waived_count"], 1)
        self.assertEqual(
            [s["record_index"] for s in entry["waived_samples"]], [0]
        )
        self.assertEqual(
            entry["waived_samples"][0]["exemption_id"], "e1"
        )
        self.assertEqual(
            entry["waived_samples"][0]["reason"], "known legacy value"
        )

    def test_exemptions_apply_per_source_rule(self):
        rules = [
            {"id": "name-req", "type": "required", "options": {"field": "name"}},
            rule(),
        ]
        records = [
            {"id": "r1", "name": "alice", "age": 999},
            {"age": 5},
        ]
        gates = [
            gate("g-age", "age-range", 0.0, "error"),
            gate("g-name", "name-req", 1.0, "info"),
        ]
        exemptions = [
            exemption("e-age", "age-range", 0, "r1", "age", "legacy"),
            {
                "exemption_id": "e-name",
                "rule_id": "name-req",
                "record_index": 1,
                "record_id": None,
                "field": "name",
                "reason": "legacy",
            },
        ]
        result = evaluate_quality_gates(
            "d", records, rules, gates, exemptions=exemptions
        )
        age_gate, name_gate = result["results"]
        self.assertEqual(age_gate["failed_count"], 0)
        self.assertEqual(age_gate["waived_count"], 1)
        self.assertEqual(
            age_gate["waived_samples"][0]["record_id"], "r1"
        )
        self.assertEqual(name_gate["failed_count"], 0)
        self.assertEqual(name_gate["waived_count"], 1)
        self.assertIsNone(name_gate["waived_samples"][0]["record_id"])

    def test_empty_dataset_with_exemptions_keeps_skipped_shape(self):
        result = evaluate_quality_gates(
            "empty",
            [],
            [rule()],
            [gate()],
            exemptions=[],
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_EMPTY_DATASET")
        self.assertEqual(entry["failed_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertNotIn("waived_count", entry)

    def test_exemption_unmatched_on_empty_dataset(self):
        with self.assertRaises(InvalidExemptionError):
            evaluate_quality_gates(
                "empty",
                [],
                [rule()],
                [gate()],
                exemptions=[exemption()],
            )

    def test_invalid_exemption_raises(self):
        bad = exemption()
        bad["record_index"] = -1
        with self.assertRaises(InvalidExemptionError):
            evaluate_quality_gates(
                "people", RECORDS, [rule()], [gate()], exemptions=[bad]
            )

    def test_duplicate_exemption_raises(self):
        with self.assertRaises(InvalidExemptionError):
            evaluate_quality_gates(
                "people",
                RECORDS,
                [rule()],
                [gate()],
                exemptions=[exemption("e1"), exemption("e1", reason="other")],
            )

    def test_unmatched_exemption_raises(self):
        with self.assertRaises(InvalidExemptionError):
            evaluate_quality_gates(
                "people",
                RECORDS,
                [rule()],
                [gate()],
                exemptions=[exemption(record_index=2, record_id="r3")],
            )


if __name__ == "__main__":
    unittest.main()
