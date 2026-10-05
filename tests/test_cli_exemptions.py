"""End-to-end tests for the ``dq exemptions`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "exemptions"]
VALIDATE = [sys.executable, "-m", "data_quality", "validate"]


def run_cli(payload_bytes):
    return subprocess.run(PKG, input=payload_bytes, capture_output=True)


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


RULE = {
    "id": "age-range",
    "type": "range",
    "options": {"field": "age", "min": 0, "max": 120},
}

RECORDS = [
    {"id": "r1", "age": 200},
    {"id": "r2", "age": 50},
    {"id": "r3", "age": 7},
]


def validate_result(records=None):
    proc = subprocess.run(
        VALIDATE,
        input=json.dumps(
            {"records": RECORDS if records is None else records, "rules": [RULE]}
        ).encode("utf-8"),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.decode("utf-8"))


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


class CliExemptionsSuccessTest(unittest.TestCase):
    def test_waives_matching_violation(self):
        proc = run_json(
            {"result": validate_result(), "exemptions": [exemption()]}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["active_violations"], [])
        self.assertEqual(
            body["summary"],
            {
                "input_violation_count": 1,
                "active_violation_count": 0,
                "waived_violation_count": 1,
                "exemption_count": 1,
            },
        )
        self.assertEqual(
            body["waived_violations"][0],
            {
                "rule_id": "age-range",
                "record_index": 0,
                "record_id": "r1",
                "field": "age",
                "value": 200,
                "message": "is out of the allowed range",
                "exemption_id": "e1",
                "reason": "known legacy value",
            },
        )

    def test_empty_exemptions_keeps_violation_active(self):
        proc = run_json({"result": validate_result(), "exemptions": []})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(len(body["active_violations"]), 1)
        self.assertEqual(body["waived_violations"], [])
        self.assertEqual(body["summary"]["exemption_count"], 0)

    def test_output_is_a_single_line(self):
        proc = run_json({"result": validate_result(), "exemptions": []})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)


class CliExemptionsErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertIn("message", body["error"])

    def test_invalid_json(self):
        self.assert_error(run_cli(b"{not json"), "INVALID_JSON")

    def test_invalid_utf8(self):
        self.assert_error(run_cli(b'{"result": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self.assert_error(run_json([1, 2]), "INVALID_EXEMPTION_INPUT")

    def test_missing_result(self):
        proc = run_json({"exemptions": []})
        self.assert_error(proc, "INVALID_EXEMPTION_INPUT")

    def test_missing_exemptions(self):
        proc = run_json({"result": validate_result()})
        self.assert_error(proc, "INVALID_EXEMPTION_INPUT")

    def test_result_not_validate_result(self):
        proc = run_json({"result": {}, "exemptions": []})
        self.assert_error(proc, "INVALID_EXEMPTION_INPUT")

    def test_malformed_exemption(self):
        bad = exemption()
        del bad["reason"]
        self.assert_error(
            run_json({"result": validate_result(), "exemptions": [bad]}),
            "INVALID_EXEMPTION_INPUT",
        )

    def test_duplicate_exemption(self):
        self.assert_error(
            run_json(
                {
                    "result": validate_result(),
                    "exemptions": [exemption("e1"), exemption("e1")],
                }
            ),
            "INVALID_EXEMPTION_INPUT",
        )

    def test_conflicting_exemption(self):
        self.assert_error(
            run_json(
                {
                    "result": validate_result(),
                    "exemptions": [
                        exemption("e1", reason="one"),
                        exemption("e2", reason="two"),
                    ],
                }
            ),
            "INVALID_EXEMPTION_INPUT",
        )

    def test_unmatched_exemption(self):
        self.assert_error(
            run_json(
                {
                    "result": validate_result(),
                    "exemptions": [
                        exemption(record_index=2, record_id="r3")
                    ],
                }
            ),
            "INVALID_EXEMPTION_INPUT",
        )


if __name__ == "__main__":
    unittest.main()
