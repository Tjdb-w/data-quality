"""Unit tests for quality gate aggregation with exemptions."""

import unittest

from data_quality.exemptions import InvalidExemptionError
from data_quality.quality_gates import evaluate_quality_gates


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


def exemption(
    exemption_id="ex1",
    rule_id="age-range",
    record_index=0,
    record_id="r1",
    field="age",
    reason="known outlier",
):
    return {
        "exemption_id": exemption_id,
        "rule_id": rule_id,
        "record_index": record_index,
        "record_id": record_id,
        "field": field,
        "reason": reason,
    }


RECORDS = [
    {"id": "r1", "age": 200},
    {"id": "r2", "age": 50},
    {"id": "r3", "age": 300},
]


class QualityGatesExemptionsBaselineTest(unittest.TestCase):
    def test_omitted_exemptions_keeps_baseline_shape(self):
        report = evaluate_quality_gates("people", RECORDS, [rule()], [gate()])
        entry = report["results"][0]
        self.assertNotIn("waived_count", entry)
        self.assertNotIn("waived_samples", entry)
        self.assertEqual(entry["failed_count"], 2)

    def test_empty_exemptions_keeps_baseline_shape(self):
        report = evaluate_quality_gates(
            "people", RECORDS, [rule()], [gate()], exemptions=[]
        )
        entry = report["results"][0]
        self.assertNotIn("waived_count", entry)
        self.assertNotIn("waived_samples", entry)
        self.assertEqual(entry["failed_count"], 2)


class QualityGatesExemptionsTest(unittest.TestCase):
    def test_waived_violations_leave_active_counts(self):
        report = evaluate_quality_gates(
            "people",
            RECORDS,
            [rule()],
            [gate(max_failed_ratio=0.3)],
            exemptions=[exemption()],
        )
        entry = report["results"][0]
        self.assertEqual(entry["status"], "FAILED")
        self.assertEqual(entry["failed_count"], 1)
        self.assertEqual(entry["record_count"], 3)
        self.assertEqual(entry["ratio"], 1 / 3)
        self.assertEqual(
            entry["samples"],
            [
                {
                    "record_index": 2,
                    "record_id": "r3",
                    "field": "age",
                    "value": 300,
                    "message": "is out of the allowed range",
                }
            ],
        )
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
                    "exemption_id": "ex1",
                    "reason": "known outlier",
                }
            ],
        )

    def test_waiving_all_violations_passes_the_gate(self):
        report = evaluate_quality_gates(
            "people",
            RECORDS,
            [rule()],
            [gate(max_failed_ratio=0.0)],
            exemptions=[
                exemption(),
                exemption("ex2", record_index=2, record_id="r3", reason="x"),
            ],
        )
        entry = report["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["failed_count"], 0)
        self.assertEqual(entry["ratio"], 0.0)
        self.assertEqual(entry["samples"], [])
        self.assertEqual(entry["waived_count"], 2)
        self.assertEqual(
            [s["exemption_id"] for s in entry["waived_samples"]],
            ["ex1", "ex2"],
        )

    def test_exemption_of_other_rule_does_not_affect_gate(self):
        rules = [
            rule(),
            {
                "id": "name-required",
                "type": "required",
                "options": {"field": "name"},
            },
        ]
        records = [{"id": "r1", "age": 200}, {"id": "r2", "age": 50}]
        report = evaluate_quality_gates(
            "people",
            records,
            rules,
            [gate(max_failed_ratio=0.5)],
            exemptions=[
                exemption(
                    rule_id="name-required",
                    record_index=0,
                    field="name",
                )
            ],
        )
        entry = report["results"][0]
        self.assertEqual(entry["failed_count"], 1)
        self.assertEqual(entry["waived_count"], 0)
        self.assertEqual(entry["waived_samples"], [])

    def test_unmatched_exemption_raises(self):
        with self.assertRaises(InvalidExemptionError) as ctx:
            evaluate_quality_gates(
                "people",
                RECORDS,
                [rule()],
                [gate()],
                exemptions=[exemption(record_index=1)],
            )
        self.assertEqual(ctx.exception.code, "INVALID_EXEMPTION_INPUT")

    def test_malformed_exemption_raises(self):
        with self.assertRaises(InvalidExemptionError):
            evaluate_quality_gates(
                "people",
                RECORDS,
                [rule()],
                [gate()],
                exemptions=[{"exemption_id": "ex1"}],
            )

    def test_exemption_with_empty_dataset_is_unmatched(self):
        with self.assertRaises(InvalidExemptionError):
            evaluate_quality_gates(
                "people", [], [rule()], [gate()], exemptions=[exemption()]
            )


if __name__ == "__main__":
    unittest.main()
