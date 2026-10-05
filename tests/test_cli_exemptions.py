"""End-to-end tests for the ``dq exemptions`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "exemptions"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


RESULT = {
    "passed": False,
    "summary": {"record_count": 2, "violation_count": 2, "checked_rule_count": 1},
    "violations": [
        {
            "rule_id": "age-range",
            "record_index": 0,
            "record_id": "r1",
            "field": "age",
            "value": 200,
            "message": "is out of the allowed range",
        },
        {
            "rule_id": "age-range",
            "record_index": 1,
            "record_id": "r2",
            "field": "age",
            "value": 300,
            "message": "is out of the allowed range",
        },
    ],
}

EXEMPTION = {
    "exemption_id": "ex1",
    "rule_id": "age-range",
    "record_index": 0,
    "record_id": "r1",
    "field": "age",
    "reason": "known outlier",
}


def payload(result=None, exemptions=None):
    return {
        "result": RESULT if result is None else result,
        "exemptions": [EXEMPTION] if exemptions is None else exemptions,
    }


class CliExemptionsSuccessTest(unittest.TestCase):
    def test_success_exits_zero_with_one_line(self):
        proc = run_json(payload())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.decode("utf-8")
        self.assertEqual(out.count("\n"), 1)
        body = json.loads(out)
        self.assertEqual(body["status"], "violations")
        self.assertEqual(
            body["summary"],
            {
                "input_violation_count": 2,
                "active_violation_count": 1,
                "waived_violation_count": 1,
                "exemption_count": 1,
            },
        )
        self.assertEqual(len(body["active_violations"]), 1)
        self.assertEqual(body["active_violations"][0]["record_id"], "r2")
        self.assertEqual(len(body["waived_violations"]), 1)
        waived = body["waived_violations"][0]
        self.assertEqual(waived["exemption_id"], "ex1")
        self.assertEqual(waived["reason"], "known outlier")

    def test_all_waived_yields_status_ok(self):
        second = dict(EXEMPTION, exemption_id="ex2", record_index=1,
                      record_id="r2")
        proc = run_json(payload(exemptions=[EXEMPTION, second]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["active_violations"], [])


class CliExemptionsErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        proc = run_cli(b"not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_non_object_payload(self):
        proc = run_json([1, 2])
        self.assert_error(proc, "INVALID_EXEMPTION_INPUT")

    def test_missing_keys(self):
        self.assert_error(run_json({"exemptions": []}), "INVALID_EXEMPTION_INPUT")
        self.assert_error(run_json({"result": RESULT}), "INVALID_EXEMPTION_INPUT")

    def test_malformed_result(self):
        self.assert_error(
            run_json(payload(result={"violations": {}})),
            "INVALID_EXEMPTION_INPUT",
        )

    def test_malformed_exemption(self):
        self.assert_error(
            run_json(payload(exemptions=[{"exemption_id": "ex1"}])),
            "INVALID_EXEMPTION_INPUT",
        )

    def test_unmatched_exemption(self):
        bad = dict(EXEMPTION, record_index=9)
        self.assert_error(
            run_json(payload(exemptions=[bad])), "INVALID_EXEMPTION_INPUT"
        )

    def test_conflicting_exemptions(self):
        other = dict(EXEMPTION, exemption_id="ex2")
        self.assert_error(
            run_json(payload(exemptions=[EXEMPTION, other])),
            "INVALID_EXEMPTION_INPUT",
        )


if __name__ == "__main__":
    unittest.main()
