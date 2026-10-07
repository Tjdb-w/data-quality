"""Unit tests for composite (cross-field) quality gate aggregation."""

import unittest

from data_quality import (
    CompositeRuleSetError,
    DuplicateRuleIdError,
    InvalidCompositeRuleError,
    InvalidSeverityError,
    UnsupportedCompositeConditionError,
)
from data_quality.composite_gates import (
    InvalidCompositeGateInputError,
    InvalidCompositeGateRuleError,
    UnknownCompositeGateSourceError,
    evaluate_composite_quality_gates,
)


def date_rule(rule_id="dates", severity="error"):
    return {
        "rule_id": rule_id,
        "fields": ["start", "end"],
        "severity": severity,
        "conditions": [
            {
                "type": "date_before",
                "earlier_field": "start",
                "later_field": "end",
            }
        ],
    }


def gate(
    rule_id="g1",
    source_rule_id="dates",
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
    {"id": "r1", "start": "2026-01-01", "end": "2026-02-01"},
    {"id": "r2", "start": "2026-03-01", "end": "2026-02-01"},
    {"id": "r3", "start": "2026-01-01", "end": "2026-02-01"},
]


class EvaluateCompositeGatesSuccessTest(unittest.TestCase):
    def test_report_shape(self):
        result = evaluate_composite_quality_gates(
            "people", RECORDS, [date_rule()], [gate(max_failed_ratio=0.2)]
        )
        self.assertEqual(result["dataset"], "people")
        self.assertEqual(
            result,
            {
                "dataset": "people",
                "results": [
                    {
                        "rule_id": "g1",
                        "source_rule_id": "dates",
                        "status": "FAILED",
                        "severity": "error",
                        "failed_count": 1,
                        "skipped_count": 0,
                        "evaluated_count": 3,
                        "record_count": 3,
                        "ratio": 1 / 3,
                        "samples": [
                            {
                                "record_index": 1,
                                "record_id": "r2",
                                "fields": ["start", "end"],
                                "field_values": {
                                    "start": "2026-03-01",
                                    "end": "2026-02-01",
                                },
                                "failed_conditions": [
                                    {
                                        "type": "date_before",
                                        "earlier_field": "start",
                                        "later_field": "end",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )

    def test_ratio_equal_to_threshold_is_passed(self):
        # 1 of 2 decidable records fails: ratio 0.5, threshold 0.5.
        passed = evaluate_composite_quality_gates(
            "d",
            [RECORDS[0], RECORDS[1]],
            [date_rule()],
            [gate(max_failed_ratio=0.5)],
        )
        self.assertEqual(passed["results"][0]["status"], "PASSED")
        self.assertEqual(passed["results"][0]["ratio"], 0.5)

        just_above = evaluate_composite_quality_gates(
            "d",
            [RECORDS[0], RECORDS[1]],
            [date_rule()],
            [gate(max_failed_ratio=0.49)],
        )
        self.assertEqual(just_above["results"][0]["status"], "FAILED")

    def test_zero_ratio_passes(self):
        result = evaluate_composite_quality_gates(
            "d", [RECORDS[0]], [date_rule()], [gate()]
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["failed_count"], 0)
        self.assertEqual(entry["skipped_count"], 0)
        self.assertEqual(entry["evaluated_count"], 1)
        self.assertEqual(entry["ratio"], 0)
        self.assertEqual(entry["samples"], [])

    def test_threshold_one_passes_even_when_all_fail(self):
        records = [
            {"start": "2026-03-01", "end": "2026-02-01"},
            {"start": "2026-03-01", "end": "2026-02-01"},
        ]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule()], [gate(max_failed_ratio=1)]
        )
        self.assertEqual(result["results"][0]["status"], "PASSED")
        self.assertEqual(result["results"][0]["ratio"], 1.0)

    def test_threshold_zero_any_failure_fails(self):
        result = evaluate_composite_quality_gates(
            "d",
            [RECORDS[0], RECORDS[1]],
            [date_rule()],
            [gate(max_failed_ratio=0)],
        )
        self.assertEqual(result["results"][0]["status"], "FAILED")

    def test_empty_dataset_is_skipped(self):
        result = evaluate_composite_quality_gates(
            "empty", [], [date_rule()], [gate()]
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_NO_EVALUATED_RECORDS")
        self.assertEqual(entry["record_count"], 0)
        self.assertEqual(entry["evaluated_count"], 0)
        self.assertEqual(entry["skipped_count"], 0)
        self.assertEqual(entry["failed_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["samples"], [])

    def test_all_skipped_is_skipped_not_failed(self):
        # A missing participating field skips the record; a dataset where
        # every record is skipped is not decidable at all.
        records = [
            {"id": "r1", "start": "2026-01-01"},
            {"id": "r2", "end": "2026-02-01"},
        ]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule()], [gate(max_failed_ratio=0)]
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_NO_EVALUATED_RECORDS")
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["skipped_count"], 2)
        self.assertEqual(entry["evaluated_count"], 0)
        self.assertEqual(entry["failed_count"], 0)
        self.assertEqual(entry["record_count"], 2)
        self.assertEqual(entry["samples"], [])

    def test_skipped_records_excluded_from_ratio_denominator(self):
        # 1 fail, 1 pass, 1 skipped -> ratio 1/2, not 1/3.
        records = [
            RECORDS[1],  # fails
            RECORDS[0],  # passes
            {"id": "r4"},  # skipped (missing both fields)
        ]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule()], [gate(max_failed_ratio=0.5)]
        )
        entry = result["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["failed_count"], 1)
        self.assertEqual(entry["evaluated_count"], 2)
        self.assertEqual(entry["skipped_count"], 1)
        self.assertEqual(entry["record_count"], 3)
        self.assertEqual(entry["ratio"], 0.5)

    def test_severity_is_echoed_in_result(self):
        for severity in ("error", "warning", "info"):
            result = evaluate_composite_quality_gates(
                "d",
                RECORDS,
                [date_rule()],
                [gate(severity=severity)],
            )
            entry = result["results"][0]
            self.assertEqual(entry["severity"], severity)
            self.assertEqual(
                set(entry),
                {
                    "rule_id",
                    "source_rule_id",
                    "status",
                    "severity",
                    "failed_count",
                    "skipped_count",
                    "evaluated_count",
                    "record_count",
                    "ratio",
                    "samples",
                },
            )

    def test_gates_aggregated_in_given_order(self):
        equal_rule = {
            "rule_id": "eq",
            "fields": ["a", "b"],
            "severity": "info",
            "conditions": [
                {"type": "field_equal", "left_field": "a", "right_field": "b"}
            ],
        }
        records = [
            {"id": "r1", "start": "2026-03-01", "end": "2026-02-01",
             "a": 1, "b": 1},
            {"id": "r2", "start": "2026-01-01", "end": "2026-02-01",
             "a": 1, "b": 2},
        ]
        gates = [
            gate("g-date", "dates", 0.0, "error"),
            gate("g-eq", "eq", 1.0, "info"),
        ]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule(), equal_rule], gates
        )
        self.assertEqual(
            [entry["rule_id"] for entry in result["results"]],
            ["g-date", "g-eq"],
        )
        self.assertEqual(result["results"][0]["failed_count"], 1)
        self.assertEqual(result["results"][1]["failed_count"], 1)
        self.assertEqual(result["results"][1]["severity"], "info")

    def test_samples_follow_record_order(self):
        records = [
            {"start": "bad", "end": "2026-02-01"},
            {"id": "b", "start": "2026-01-01", "end": "2026-02-01"},
            {"id": "c", "start": "2026-03-01", "end": "2026-02-01"},
        ]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule()], [gate(max_failed_ratio=0.1)]
        )
        samples = result["results"][0]["samples"]
        # Both unparseable dates and reversed dates fail, in record order.
        self.assertEqual([sample["record_index"] for sample in samples], [0, 2])
        self.assertEqual([sample["record_id"] for sample in samples], [None, "c"])

    def test_sample_field_values_frozen_in_field_order(self):
        records = [
            {"id": "r1", "end": "2026-02-01", "start": "2026-03-01",
             "ignored": "kept-out"},
        ]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule()], [gate()]
        )
        sample = result["results"][0]["samples"][0]
        self.assertEqual(sample["fields"], ["start", "end"])
        self.assertEqual(
            list(sample["field_values"]), ["start", "end"]
        )
        self.assertEqual(
            sample["field_values"],
            {"start": "2026-03-01", "end": "2026-02-01"},
        )
        self.assertNotIn("ignored", sample["field_values"])

    def test_failed_conditions_list_only_unsatisfied_in_definition_order(self):
        rule = {
            "rule_id": "multi",
            "fields": ["a", "b", "c"],
            "severity": "error",
            "conditions": [
                {"type": "field_equal", "left_field": "a", "right_field": "b"},
                {"type": "field_not_equal", "left_field": "b",
                 "right_field": "c"},
            ],
        }
        # a == b (condition 1 satisfied), b == c (condition 2 fails).
        records = [{"id": "r1", "a": 1, "b": 1, "c": 1}]
        result = evaluate_composite_quality_gates(
            "d", records, [rule], [gate(source_rule_id="multi")]
        )
        sample = result["results"][0]["samples"][0]
        self.assertEqual(
            sample["failed_conditions"],
            [
                {
                    "type": "field_not_equal",
                    "left_field": "b",
                    "right_field": "c",
                }
            ],
        )

    def test_failed_conditions_for_required_when(self):
        rule = {
            "rule_id": "rw",
            "fields": ["when", "req"],
            "severity": "warning",
            "conditions": [
                {
                    "type": "required_when",
                    "when_field": "when",
                    "equals": "x",
                    "required_field": "req",
                }
            ],
        }
        records = [{"id": "r1", "when": "x", "req": None}]
        result = evaluate_composite_quality_gates(
            "d", records, [rule], [gate(source_rule_id="rw")]
        )
        sample = result["results"][0]["samples"][0]
        self.assertEqual(
            sample["failed_conditions"],
            [
                {
                    "type": "required_when",
                    "when_field": "when",
                    "required_field": "req",
                }
            ],
        )

    def test_skipped_records_are_not_samples(self):
        records = [{"id": "r1"}, RECORDS[1]]
        result = evaluate_composite_quality_gates(
            "d", records, [date_rule()], [gate(max_failed_ratio=1)]
        )
        samples = result["results"][0]["samples"]
        self.assertEqual([sample["record_index"] for sample in samples], [1])


class EvaluateCompositeGatesInputErrorTest(unittest.TestCase):
    def test_dataset_empty(self):
        with self.assertRaises(InvalidCompositeGateInputError):
            evaluate_composite_quality_gates("", [], [date_rule()], [gate()])

    def test_dataset_not_string(self):
        with self.assertRaises(InvalidCompositeGateInputError):
            evaluate_composite_quality_gates(None, [], [date_rule()], [gate()])

    def test_records_not_list(self):
        with self.assertRaises(InvalidCompositeGateInputError):
            evaluate_composite_quality_gates(
                "d", {}, [date_rule()], [gate()]
            )

    def test_record_not_object(self):
        with self.assertRaises(InvalidCompositeGateInputError):
            evaluate_composite_quality_gates(
                "d", [1], [date_rule()], [gate()]
            )


class EvaluateCompositeGatesRuleErrorTest(unittest.TestCase):
    def test_rules_not_list(self):
        with self.assertRaises(CompositeRuleSetError):
            evaluate_composite_quality_gates("d", [], {}, [gate()])

    def test_rules_empty(self):
        with self.assertRaises(CompositeRuleSetError):
            evaluate_composite_quality_gates("d", [], [], [gate()])

    def test_duplicate_rule_id(self):
        with self.assertRaises(DuplicateRuleIdError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule(), date_rule()], [gate()]
            )

    def test_invalid_severity(self):
        with self.assertRaises(InvalidSeverityError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule(severity="fatal")], [gate()]
            )

    def test_unsupported_condition(self):
        bad = {
            "rule_id": "x",
            "fields": ["a", "b"],
            "severity": "info",
            "conditions": [
                {"type": "magic", "left_field": "a", "right_field": "b"}
            ],
        }
        with self.assertRaises(UnsupportedCompositeConditionError):
            evaluate_composite_quality_gates("d", [], [bad], [gate()])

    def test_invalid_rule_definition(self):
        bad = {
            "rule_id": "x",
            "fields": ["a"],
            "severity": "info",
            "conditions": [
                {"type": "field_equal", "left_field": "a", "right_field": "b"}
            ],
        }
        with self.assertRaises(InvalidCompositeRuleError):
            evaluate_composite_quality_gates("d", [], [bad], [gate()])


class EvaluateCompositeGatesGateErrorTest(unittest.TestCase):
    def test_gates_not_list(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], {}
            )

    def test_gate_not_object(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], ["g1"]
            )

    def test_gate_missing_key(self):
        bad = gate()
        del bad["severity"]
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [bad]
            )

    def test_gate_extra_key(self):
        bad = gate()
        bad["extra"] = 1
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [bad]
            )

    def test_rule_id_empty(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(rule_id="")]
            )

    def test_rule_id_not_string(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(rule_id=3)]
            )

    def test_duplicate_rule_id(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d",
                [],
                [date_rule()],
                [gate("g1"), gate("g1", max_failed_ratio=0.1)],
            )

    def test_source_rule_id_empty(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(source_rule_id="")]
            )

    def test_source_rule_id_not_string(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(source_rule_id=4)]
            )

    def test_invalid_severity(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(severity="fatal")]
            )

    def test_severity_not_string(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(severity=1)]
            )

    def test_ratio_boolean_rejected(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio=True)]
            )
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio=False)]
            )

    def test_ratio_string_rejected(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio="0.5")]
            )

    def test_ratio_none_rejected(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio=None)]
            )

    def test_ratio_below_zero_rejected(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio=-0.01)]
            )

    def test_ratio_above_one_rejected(self):
        with self.assertRaises(InvalidCompositeGateRuleError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio=1.01)]
            )

    def test_boundary_ratios_accepted(self):
        for boundary in (0, 1):
            result = evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(max_failed_ratio=boundary)]
            )
            self.assertEqual(
                result["results"][0]["status"],
                "SKIPPED_NO_EVALUATED_RECORDS",
            )


class EvaluateCompositeGatesUnknownSourceTest(unittest.TestCase):
    def test_unknown_source_rule_id(self):
        with self.assertRaises(UnknownCompositeGateSourceError):
            evaluate_composite_quality_gates(
                "d", [], [date_rule()], [gate(source_rule_id="other")]
            )

    def test_unknown_source_checked_before_records(self):
        # An unknown source aborts even if the records are also malformed.
        with self.assertRaises(UnknownCompositeGateSourceError):
            evaluate_composite_quality_gates(
                "d", [1], [date_rule()], [gate(source_rule_id="other")]
            )


if __name__ == "__main__":
    unittest.main()
