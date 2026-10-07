"""Tests for :func:`data_quality.evaluate_composite_quality_gates`."""

import unittest

from data_quality import (
    CompositeRuleSetError,
    DuplicateRuleIdError,
    InvalidCompositeRuleError,
    InvalidInputError,
    InvalidQualityGateRuleError,
    InvalidSeverityError,
    UnknownQualityGateSourceError,
    UnsupportedCompositeConditionError,
    evaluate_composite_quality_gates,
)


RULE = {
    "rule_id": "dates",
    "fields": ["start", "end"],
    "severity": "error",
    "conditions": [
        {"type": "date_before", "earlier_field": "start", "later_field": "end"}
    ],
}

EQUAL_RULE = {
    "rule_id": "confirm",
    "fields": ["email", "email_confirm"],
    "severity": "warning",
    "conditions": [
        {
            "type": "field_equal",
            "left_field": "email",
            "right_field": "email_confirm",
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


class EvaluateCompositeQualityGatesTest(unittest.TestCase):
    def test_failed_gate_with_samples(self):
        records = [
            {"id": "r1", "start": "2026-03-01", "end": "2026-02-01"},
            {"id": "r2", "start": "2026-01-01", "end": "2026-02-01"},
        ]
        report = evaluate_composite_quality_gates(
            "orders", records, [RULE], [gate(max_failed_ratio=0.1)]
        )
        self.assertEqual(report["dataset"], "orders")
        self.assertEqual(len(report["results"]), 1)
        entry = report["results"][0]
        self.assertEqual(
            entry,
            {
                "rule_id": "g1",
                "source_rule_id": "dates",
                "severity": "error",
                "status": "FAILED",
                "failed_count": 1,
                "skipped_count": 0,
                "evaluated_count": 2,
                "record_count": 2,
                "ratio": 0.5,
                "samples": [
                    {
                        "record_index": 0,
                        "record_id": "r1",
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
            },
        )

    def test_passed_gate(self):
        records = [{"id": "r1", "start": "2026-01-01", "end": "2026-02-01"}]
        report = evaluate_composite_quality_gates(
            "orders", records, [RULE], [gate(max_failed_ratio=0)]
        )
        entry = report["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["ratio"], 0.0)
        self.assertEqual(entry["samples"], [])

    def test_skipped_records_are_not_evaluated(self):
        records = [
            {"id": "r1", "start": "2026-01-01"},  # missing end -> skipped
            {"id": "r2", "start": "2026-03-01", "end": "2026-02-01"},
            {"id": "r3", "start": "2026-01-01", "end": "2026-02-01"},
        ]
        report = evaluate_composite_quality_gates(
            "orders", records, [RULE], [gate(max_failed_ratio=0.4)]
        )
        entry = report["results"][0]
        self.assertEqual(entry["status"], "FAILED")
        self.assertEqual(entry["failed_count"], 1)
        self.assertEqual(entry["skipped_count"], 1)
        self.assertEqual(entry["evaluated_count"], 2)
        self.assertEqual(entry["record_count"], 3)
        self.assertEqual(entry["ratio"], 0.5)
        self.assertEqual([s["record_index"] for s in entry["samples"]], [1])

    def test_empty_dataset_is_skipped_no_evaluated_records(self):
        report = evaluate_composite_quality_gates(
            "orders", [], [RULE], [gate()]
        )
        entry = report["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_NO_EVALUATED_RECORDS")
        self.assertEqual(entry["record_count"], 0)
        self.assertEqual(entry["evaluated_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["samples"], [])

    def test_all_skipped_is_skipped_no_evaluated_records(self):
        records = [{"id": "r1"}, {"id": "r2", "start": "2026-01-01"}]
        report = evaluate_composite_quality_gates(
            "orders", records, [RULE], [gate()]
        )
        entry = report["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_NO_EVALUATED_RECORDS")
        self.assertEqual(entry["skipped_count"], 2)
        self.assertEqual(entry["evaluated_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["samples"], [])

    def test_gates_keep_input_order_and_share_rules(self):
        records = [
            {"id": "r1", "start": "2026-03-01", "end": "2026-02-01"},
            {"id": "r2", "start": "2026-01-01", "end": "2026-02-01"},
        ]
        report = evaluate_composite_quality_gates(
            "orders",
            records,
            [RULE],
            [
                gate("g-first", max_failed_ratio=0.5),
                gate("g-second", max_failed_ratio=0.1, severity="warning"),
            ],
        )
        self.assertEqual(
            [r["rule_id"] for r in report["results"]],
            ["g-first", "g-second"],
        )
        first, second = report["results"]
        self.assertEqual(first["status"], "PASSED")
        self.assertEqual(second["status"], "FAILED")
        self.assertEqual(second["severity"], "warning")

    def test_sample_field_values_follow_declared_field_order(self):
        rule = {
            "rule_id": "pair",
            "fields": ["b", "a"],
            "severity": "info",
            "conditions": [
                {"type": "field_equal", "left_field": "a", "right_field": "b"}
            ],
        }
        records = [{"id": "r1", "a": 1, "b": 2}]
        report = evaluate_composite_quality_gates(
            "ds",
            records,
            [rule],
            [gate(source_rule_id="pair", max_failed_ratio=0)],
        )
        sample = report["results"][0]["samples"][0]
        self.assertEqual(sample["fields"], ["b", "a"])
        self.assertEqual(list(sample["field_values"]), ["b", "a"])
        self.assertEqual(sample["field_values"], {"b": 2, "a": 1})
        self.assertEqual(
            sample["failed_conditions"],
            [{"type": "field_equal", "left_field": "a", "right_field": "b"}],
        )

    def test_failed_conditions_keep_definition_order(self):
        rule = {
            "rule_id": "multi",
            "fields": ["a", "b", "c"],
            "severity": "error",
            "conditions": [
                {"type": "field_equal", "left_field": "a", "right_field": "b"},
                {
                    "type": "field_not_equal",
                    "left_field": "b",
                    "right_field": "c",
                },
            ],
        }
        records = [{"id": "r1", "a": 1, "b": 2, "c": 2}]
        report = evaluate_composite_quality_gates(
            "ds", records, [rule], [gate(source_rule_id="multi", max_failed_ratio=0)]
        )
        sample = report["results"][0]["samples"][0]
        self.assertEqual(
            sample["failed_conditions"],
            [
                {"type": "field_equal", "left_field": "a", "right_field": "b"},
                {
                    "type": "field_not_equal",
                    "left_field": "b",
                    "right_field": "c",
                },
            ],
        )

    def test_record_without_id_has_null_record_id(self):
        records = [{"start": "2026-03-01", "end": "2026-02-01"}]
        report = evaluate_composite_quality_gates(
            "orders", records, [RULE], [gate(max_failed_ratio=0)]
        )
        sample = report["results"][0]["samples"][0]
        self.assertEqual(sample["record_index"], 0)
        self.assertIsNone(sample["record_id"])

    def test_required_when_failed_condition_fields(self):
        rule = {
            "rule_id": "req-when",
            "fields": ["kind", "detail"],
            "severity": "error",
            "conditions": [
                {
                    "type": "required_when",
                    "when_field": "kind",
                    "equals": "full",
                    "required_field": "detail",
                }
            ],
        }
        records = [{"id": "r1", "kind": "full", "detail": None}]
        report = evaluate_composite_quality_gates(
            "ds",
            records,
            [rule],
            [gate(source_rule_id="req-when", max_failed_ratio=0)],
        )
        sample = report["results"][0]["samples"][0]
        self.assertEqual(
            sample["failed_conditions"],
            [
                {
                    "type": "required_when",
                    "when_field": "kind",
                    "required_field": "detail",
                }
            ],
        )


class EvaluateCompositeQualityGatesErrorTest(unittest.TestCase):
    def test_empty_dataset_id(self):
        with self.assertRaises(InvalidInputError):
            evaluate_composite_quality_gates("", [], [RULE], [gate()])

    def test_dataset_not_string(self):
        with self.assertRaises(InvalidInputError):
            evaluate_composite_quality_gates(None, [], [RULE], [gate()])

    def test_records_not_list(self):
        with self.assertRaises(InvalidInputError):
            evaluate_composite_quality_gates("ds", {}, [RULE], [gate()])

    def test_record_not_object(self):
        with self.assertRaises(InvalidInputError):
            evaluate_composite_quality_gates("ds", [1], [RULE], [gate()])

    def test_empty_rules(self):
        with self.assertRaises(CompositeRuleSetError):
            evaluate_composite_quality_gates("ds", [], [], [gate()])

    def test_duplicate_rule_id(self):
        with self.assertRaises(DuplicateRuleIdError):
            evaluate_composite_quality_gates("ds", [], [RULE, RULE], [gate()])

    def test_bad_rule_severity(self):
        bad = dict(RULE, severity="fatal")
        with self.assertRaises(InvalidSeverityError):
            evaluate_composite_quality_gates("ds", [], [bad], [gate()])

    def test_unsupported_condition(self):
        bad = dict(RULE, conditions=[{"type": "magic"}])
        with self.assertRaises(UnsupportedCompositeConditionError):
            evaluate_composite_quality_gates("ds", [], [bad], [gate()])

    def test_invalid_composite_rule(self):
        bad = dict(
            RULE,
            conditions=[
                {
                    "type": "field_equal",
                    "left_field": "start",
                    "right_field": "unknown",
                }
            ],
        )
        with self.assertRaises(InvalidCompositeRuleError):
            evaluate_composite_quality_gates("ds", [], [bad], [gate()])

    def test_gates_not_list(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_composite_quality_gates("ds", [], [RULE], {})

    def test_gate_missing_key(self):
        bad = gate()
        del bad["severity"]
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_composite_quality_gates("ds", [], [RULE], [bad])

    def test_gate_extra_key(self):
        bad = dict(gate(), extra=True)
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_composite_quality_gates("ds", [], [RULE], [bad])

    def test_duplicate_gate_rule_id(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_composite_quality_gates(
                "ds", [], [RULE], [gate("g1"), gate("g1")]
            )

    def test_gate_bad_ratio(self):
        with self.assertRaises(InvalidQualityGateRuleError):
            evaluate_composite_quality_gates(
                "ds", [], [RULE], [gate(max_failed_ratio=True)]
            )

    def test_unknown_source(self):
        with self.assertRaises(UnknownQualityGateSourceError):
            evaluate_composite_quality_gates(
                "ds", [], [RULE], [gate(source_rule_id="nope")]
            )


if __name__ == "__main__":
    unittest.main()
