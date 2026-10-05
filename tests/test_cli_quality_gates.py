"""End-to-end tests for the ``dq quality-gates`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "quality-gates"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


RULE = {
    "id": "age-range",
    "type": "range",
    "options": {"field": "age", "min": 0, "max": 120},
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


def payload(records, gates, dataset="people", rules=None):
    return {
        "dataset": dataset,
        "records": records,
        "rules": [RULE] if rules is None else rules,
        "gates": gates,
    }


class CliQualityGatesSuccessTest(unittest.TestCase):
    def test_failed_gate_still_exits_zero(self):
        records = [{"id": "r1", "age": 200}, {"id": "r2", "age": 5}]
        proc = run_json(payload(records, [gate(max_failed_ratio=0.1)]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["dataset"], "people")
        entry = body["results"][0]
        self.assertEqual(
            entry,
            {
                "rule_id": "g1",
                "source_rule_id": "age-range",
                "status": "FAILED",
                "failed_count": 1,
                "record_count": 2,
                "ratio": 0.5,
                "samples": [
                    {
                        "record_index": 0,
                        "record_id": "r1",
                        "field": "age",
                        "value": 200,
                        "message": "is out of the allowed range",
                    }
                ],
            },
        )

    def test_passed_gate_exits_zero(self):
        proc = run_json(
            payload([{"id": "r1", "age": 5}], [gate(max_failed_ratio=0)])
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["results"][0]["status"], "PASSED")

    def test_empty_dataset_skipped_exits_zero(self):
        proc = run_json(payload([], [gate()]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        entry = body["results"][0]
        self.assertEqual(entry["status"], "SKIPPED_EMPTY_DATASET")
        self.assertEqual(entry["record_count"], 0)
        self.assertIsNone(entry["ratio"])
        self.assertEqual(entry["samples"], [])

    def test_output_is_a_single_line(self):
        proc = run_json(payload([{"age": 1}], [gate()]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)


class CliQualityGatesErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertIn("message", body["error"])

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_payload_not_object(self):
        proc = run_json([1, 2])
        self.assert_error(proc, "INVALID_INPUT")

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

    def test_records_malformed(self):
        self.assert_error(
            run_json(payload([1], [gate()])), "INVALID_INPUT"
        )

    def test_rules_malformed(self):
        bad_rule = {"id": "r1", "type": "bogus", "options": {"field": "x"}}
        self.assert_error(
            run_json(payload([], [gate()], rules=[bad_rule])),
            "INVALID_RULE",
        )

    def test_gate_missing_key(self):
        bad = gate()
        del bad["severity"]
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_QUALITY_GATE_RULE"
        )

    def test_gate_extra_key(self):
        bad = gate()
        bad["extra"] = True
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_QUALITY_GATE_RULE"
        )

    def test_duplicate_gate_rule_id(self):
        self.assert_error(
            run_json(payload([], [gate("g1"), gate("g1")])),
            "INVALID_QUALITY_GATE_RULE",
        )

    def test_bad_severity(self):
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
