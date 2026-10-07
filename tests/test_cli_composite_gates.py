"""End-to-end tests for the ``dq composite-gates`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "composite-gates"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


RULE = {
    "rule_id": "dates",
    "fields": ["start", "end"],
    "severity": "error",
    "conditions": [
        {"type": "date_before", "earlier_field": "start", "later_field": "end"}
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


def payload(records, gates, dataset="orders", rules=None):
    return {
        "dataset": dataset,
        "records": records,
        "rules": [RULE] if rules is None else rules,
        "gates": gates,
    }


class CliCompositeGatesSuccessTest(unittest.TestCase):
    def test_failed_gate_still_exits_zero(self):
        records = [
            {"id": "r1", "start": "2026-03-01", "end": "2026-02-01"},
            {"id": "r2", "start": "2026-01-01", "end": "2026-02-01"},
        ]
        proc = run_json(payload(records, [gate(max_failed_ratio=0.1)]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["dataset"], "orders")
        entry = body["results"][0]
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

    def test_passed_gate_exits_zero(self):
        records = [{"id": "r1", "start": "2026-01-01", "end": "2026-02-01"}]
        proc = run_json(payload(records, [gate(max_failed_ratio=0)]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["results"][0]["status"], "PASSED")

    def test_all_skipped_exits_zero(self):
        proc = run_json(payload([{"id": "r1"}], [gate()]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        entry = body["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_NO_EVALUATED_RECORDS")
        self.assertEqual(entry["skipped_count"], 1)
        self.assertEqual(entry["evaluated_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["samples"], [])

    def test_output_is_a_single_line(self):
        records = [{"id": "r1", "start": "2026-01-01", "end": "2026-02-01"}]
        proc = run_json(payload(records, [gate()]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)


class CliCompositeGatesErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertIn("message", body["error"])

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_invalid_utf8(self):
        proc = run_cli(b"\xff\xfe")
        self.assert_error(proc, "INVALID_JSON")

    def test_payload_not_object(self):
        self.assert_error(run_json([1, 2]), "INVALID_INPUT")

    def test_missing_dataset(self):
        obj = payload([], [gate()])
        del obj["dataset"]
        self.assert_error(run_json(obj), "INVALID_INPUT")

    def test_missing_records(self):
        obj = payload([], [gate()])
        del obj["records"]
        self.assert_error(run_json(obj), "INVALID_INPUT")

    def test_missing_rules(self):
        obj = payload([], [gate()])
        del obj["rules"]
        self.assert_error(run_json(obj), "INVALID_INPUT")

    def test_missing_gates(self):
        obj = payload([], [gate()])
        del obj["gates"]
        self.assert_error(run_json(obj), "INVALID_INPUT")

    def test_extra_top_level_key(self):
        obj = payload([], [gate()])
        obj["exemptions"] = []
        self.assert_error(run_json(obj), "INVALID_INPUT")

    def test_empty_dataset_id(self):
        self.assert_error(run_json(payload([], [gate()], dataset="")), "INVALID_INPUT")

    def test_records_malformed(self):
        self.assert_error(run_json(payload([1], [gate()])), "INVALID_INPUT")

    def test_empty_rules(self):
        self.assert_error(run_json(payload([], [gate()], rules=[])), "INVALID_RULE_SET")

    def test_duplicate_rule_id(self):
        self.assert_error(
            run_json(payload([], [gate()], rules=[RULE, RULE])),
            "DUPLICATE_RULE_ID",
        )

    def test_bad_rule_severity(self):
        bad = dict(RULE, severity="fatal")
        self.assert_error(
            run_json(payload([], [gate()], rules=[bad])), "INVALID_SEVERITY"
        )

    def test_unsupported_condition(self):
        bad = dict(RULE, conditions=[{"type": "magic"}])
        self.assert_error(
            run_json(payload([], [gate()], rules=[bad])),
            "UNSUPPORTED_COMPOSITE_CONDITION",
        )

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
        self.assert_error(
            run_json(payload([], [gate()], rules=[bad])),
            "INVALID_COMPOSITE_RULE",
        )

    def test_gate_missing_key(self):
        bad = gate()
        del bad["severity"]
        self.assert_error(run_json(payload([], [bad])), "INVALID_QUALITY_GATE_RULE")

    def test_gate_extra_key(self):
        bad = gate()
        bad["extra"] = True
        self.assert_error(run_json(payload([], [bad])), "INVALID_QUALITY_GATE_RULE")

    def test_duplicate_gate_rule_id(self):
        self.assert_error(
            run_json(payload([], [gate("g1"), gate("g1")])),
            "INVALID_QUALITY_GATE_RULE",
        )

    def test_bad_gate_severity(self):
        self.assert_error(
            run_json(payload([], [gate(severity="fatal")])),
            "INVALID_QUALITY_GATE_RULE",
        )

    def test_ratio_not_number(self):
        self.assert_error(
            run_json(payload([], [gate(max_failed_ratio=True)])),
            "INVALID_QUALITY_GATE_RULE",
        )

    def test_ratio_out_of_range(self):
        self.assert_error(
            run_json(payload([], [gate(max_failed_ratio=2)])),
            "INVALID_QUALITY_GATE_RULE",
        )

    def test_unknown_source_rule_id(self):
        self.assert_error(
            run_json(payload([], [gate(source_rule_id="nope")])),
            "UNKNOWN_QUALITY_GATE_SOURCE",
        )


if __name__ == "__main__":
    unittest.main()
