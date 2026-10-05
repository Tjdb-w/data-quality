"""End-to-end tests for ``dq quality-gates`` with exemptions."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "quality-gates"]


def run_json(obj):
    return subprocess.run(
        PKG,
        input=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
    )


RULE = {
    "id": "age-range",
    "type": "range",
    "options": {"field": "age", "min": 0, "max": 120},
}

GATE = {
    "rule_id": "g1",
    "source_rule_id": "age-range",
    "max_failed_ratio": 0.0,
    "severity": "error",
}

EXEMPTION = {
    "exemption_id": "ex1",
    "rule_id": "age-range",
    "record_index": 0,
    "record_id": "r1",
    "field": "age",
    "reason": "known outlier",
}


def payload(exemptions=None):
    body = {
        "dataset": "people",
        "records": [{"id": "r1", "age": 200}, {"id": "r2", "age": 5}],
        "rules": [RULE],
        "gates": [GATE],
    }
    if exemptions is not None:
        body["exemptions"] = exemptions
    return body


class CliQualityGatesExemptionsTest(unittest.TestCase):
    def test_omitted_exemptions_keeps_baseline(self):
        proc = run_json(payload())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entry = json.loads(proc.stdout.decode("utf-8"))["results"][0]
        self.assertEqual(entry["status"], "FAILED")
        self.assertEqual(entry["failed_count"], 1)
        self.assertNotIn("waived_count", entry)
        self.assertNotIn("waived_samples", entry)

    def test_empty_exemptions_keeps_baseline(self):
        proc = run_json(payload(exemptions=[]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entry = json.loads(proc.stdout.decode("utf-8"))["results"][0]
        self.assertEqual(entry["failed_count"], 1)
        self.assertNotIn("waived_count", entry)

    def test_waived_violation_passes_gate(self):
        proc = run_json(payload(exemptions=[EXEMPTION]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entry = json.loads(proc.stdout.decode("utf-8"))["results"][0]
        self.assertEqual(entry["status"], "PASSED")
        self.assertEqual(entry["failed_count"], 0)
        self.assertEqual(entry["ratio"], 0.0)
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
                    "exemption_id": "ex1",
                    "reason": "known outlier",
                }
            ],
        )

    def test_invalid_exemption_exits_two(self):
        bad = dict(EXEMPTION, record_index=7)
        proc = run_json(payload(exemptions=[bad]))
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_EXEMPTION_INPUT")
        self.assertTrue(body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
